"""Tests for :mod:`manaradix.core.usage_store`.

The store has two read/write paths to exercise:

* The writer (:class:`UsageStore`) owns the database and runs inside
  the root collector process.
* The reader (:class:`UsageReader`) opens the database read-only, as
  the GUI and the D-Bus daemon do.

Every test runs against a fresh ``tempfile`` database — no global
state, no root needed.
"""

from __future__ import annotations

import os
import types
from unittest.mock import MagicMock

import pytest

from manaradix.core.usage_store import (
    DEFAULT_DB_PATH,
    UsageCollector,
    UsageReader,
    UsageStore,
    _parse_du_multi_path_output,
)


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


class TestUsageStoreLifecycle:
    """Schema bootstrap and meta-row bookkeeping."""

    def test_creates_database_on_first_use(self, tmp_path) -> None:
        db = tmp_path / "usage.sqlite"
        UsageStore(str(db))
        assert db.exists()

    def test_creates_parent_directory(self, tmp_path) -> None:
        db = tmp_path / "nested" / "dir" / "usage.sqlite"
        UsageStore(str(db))
        assert db.exists()

    def test_start_collect_marks_running(self, tmp_path) -> None:
        store = UsageStore(str(tmp_path / "u.sqlite"))
        store.start_collect()
        reader = UsageReader(str(tmp_path / "u.sqlite"))
        assert reader.status()["state"] == "running"
        assert reader.status()["last_started"] is not None

    def test_finish_collect_sets_ok(self, tmp_path) -> None:
        store = UsageStore(str(tmp_path / "u.sqlite"))
        store.start_collect()
        store.finish_collect(
            [{
                "mount": "/",
                "snapshot_dir": "/.snapshots",
                "total_bytes": 100,
                "exclusive_bytes": 10,
                "device_size_bytes": 1000,
            }],
            [],
        )
        reader = UsageReader(str(tmp_path / "u.sqlite"))
        status = reader.status()
        assert status["state"] == "ok"
        assert status["last_started"] is not None
        assert status["last_finished"] is not None
        assert status["last_error"] is None

    def test_fail_collect_records_error(self, tmp_path) -> None:
        store = UsageStore(str(tmp_path / "u.sqlite"))
        store.start_collect()
        store.fail_collect("boom")
        reader = UsageReader(str(tmp_path / "u.sqlite"))
        status = reader.status()
        assert status["state"] == "failed"
        assert status["last_error"] == "boom"

    def test_fail_collect_truncates_long_message(self, tmp_path) -> None:
        store = UsageStore(str(tmp_path / "u.sqlite"))
        store.start_collect()
        store.fail_collect("x" * 1000)
        reader = UsageReader(str(tmp_path / "u.sqlite"))
        assert len(reader.status()["last_error"]) <= 200

    def test_finish_collect_replaces_previous_data(self, tmp_path) -> None:
        store = UsageStore(str(tmp_path / "u.sqlite"))
        store.start_collect()
        store.finish_collect(
            [{
                "mount": "/",
                "snapshot_dir": "/.snapshots",
                "total_bytes": 100,
                "exclusive_bytes": 10,
                "device_size_bytes": 1000,
            }],
            [],
        )
        store.start_collect()
        store.finish_collect(
            [{
                "mount": "/",
                "snapshot_dir": "/.snapshots",
                "total_bytes": 200,
                "exclusive_bytes": 20,
                "device_size_bytes": 2000,
            }],
            [],
        )
        rows = UsageReader(str(tmp_path / "u.sqlite")).aggregates()
        assert len(rows) == 1
        assert rows[0]["total_bytes"] == 200

    def test_fail_collect_keeps_previous_data(self, tmp_path) -> None:
        store = UsageStore(str(tmp_path / "u.sqlite"))
        store.start_collect()
        store.finish_collect(
            [{
                "mount": "/",
                "snapshot_dir": "/.snapshots",
                "total_bytes": 100,
                "exclusive_bytes": 10,
                "device_size_bytes": 1000,
            }],
            [],
        )
        store.start_collect()
        store.fail_collect("boom")
        reader = UsageReader(str(tmp_path / "u.sqlite"))
        assert reader.aggregate_for("/") is not None
        assert reader.aggregate_for("/")["total_bytes"] == 100
        assert reader.status()["state"] == "failed"


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------


class TestUsageReader:
    def test_missing_database_is_idle(self, tmp_path) -> None:
        reader = UsageReader(str(tmp_path / "nonexistent.sqlite"))
        assert reader.status()["state"] == "idle"
        assert reader.aggregates() == []
        assert reader.snapshots() == []
        assert reader.aggregate_for("/") is None

    def test_read_only_does_not_modify_file(self, tmp_path) -> None:
        # Pre-populate via the writer, then open read-only and confirm
        # every read is a SELECT.
        store = UsageStore(str(tmp_path / "u.sqlite"))
        store.start_collect()
        store.finish_collect(
            [{
                "mount": "/",
                "snapshot_dir": "/.snapshots",
                "total_bytes": 100,
                "exclusive_bytes": 10,
                "device_size_bytes": 1000,
            }],
            [{
                "mount": "/",
                "snapshot_id": "snap1",
                "snapshot_path": "/.snapshots/snap1/root",
                "total_bytes": 10,
                "exclusive_bytes": 1,
            }],
        )
        # Remove write permissions: a read-only URI must still succeed.
        os.chmod(str(tmp_path / "u.sqlite"), 0o444)
        try:
            reader = UsageReader(str(tmp_path / "u.sqlite"))
            assert reader.aggregate_for("/")["total_bytes"] == 100
            assert len(reader.snapshots()) == 1
        finally:
            os.chmod(str(tmp_path / "u.sqlite"), 0o644)

    def test_aggregate_for_unknown_mount_returns_none(self, tmp_path) -> None:
        store = UsageStore(str(tmp_path / "u.sqlite"))
        store.start_collect()
        store.finish_collect(
            [{
                "mount": "/",
                "snapshot_dir": "/.snapshots",
                "total_bytes": 100,
                "exclusive_bytes": 10,
                "device_size_bytes": 1000,
            }],
            [],
        )
        reader = UsageReader(str(tmp_path / "u.sqlite"))
        assert reader.aggregate_for("/home") is None

    def test_snapshots_filtered_by_mount(self, tmp_path) -> None:
        store = UsageStore(str(tmp_path / "u.sqlite"))
        store.start_collect()
        store.finish_collect(
            [
                {
                    "mount": "/",
                    "snapshot_dir": "/.snapshots",
                    "total_bytes": 100,
                    "exclusive_bytes": 10,
                    "device_size_bytes": 1000,
                },
                {
                    "mount": "/home",
                    "snapshot_dir": "/home/.snapshots",
                    "total_bytes": 50,
                    "exclusive_bytes": 5,
                    "device_size_bytes": 500,
                },
            ],
            [
                {
                    "mount": "/",
                    "snapshot_id": "snap-root",
                    "snapshot_path": "/.snapshots/snap-root/root",
                    "total_bytes": 10,
                    "exclusive_bytes": 1,
                },
                {
                    "mount": "/home",
                    "snapshot_id": "snap-home",
                    "snapshot_path": "/home/.snapshots/snap-home/home",
                    "total_bytes": 20,
                    "exclusive_bytes": 2,
                },
            ],
        )
        reader = UsageReader(str(tmp_path / "u.sqlite"))
        assert [r["snapshot_id"] for r in reader.snapshots("/")] == ["snap-root"]
        assert [r["snapshot_id"] for r in reader.snapshots("/home")] == ["snap-home"]
        assert len(reader.snapshots()) == 2


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class TestMultiPathParser:
    def test_parses_summary_rows(self) -> None:
        stdout = (
            "     Total   Exclusive  Set shared  Filename\n"
            " 12288000    1228800          0  /.snapshots\n"
            "   204800       2048          0  /.snapshots/snap1/root\n"
        )
        rows = _parse_du_multi_path_output(stdout)
        assert rows == [
            {"path": "/.snapshots", "total_bytes": 12288000, "exclusive_bytes": 1228800},
            {"path": "/.snapshots/snap1/root", "total_bytes": 204800, "exclusive_bytes": 2048},
        ]

    def test_skips_blank_and_header_lines(self) -> None:
        stdout = (
            "\n"
            "     Total   Exclusive  Set shared  Filename\n"
            "\n"
            "   123          10          0  /a\n"
            "\n"
        )
        rows = _parse_du_multi_path_output(stdout)
        assert rows == [{"path": "/a", "total_bytes": 123, "exclusive_bytes": 10}]

    def test_ignores_garbage_lines(self) -> None:
        stdout = (
            "WARNING: something weird happened\n"
            "     Total   Exclusive  Set shared  Filename\n"
            "   123          10          0  /a\n"
        )
        rows = _parse_du_multi_path_output(stdout)
        assert rows == [{"path": "/a", "total_bytes": 123, "exclusive_bytes": 10}]


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------


def _make_snapshot(snap_id: str, path: str):
    """A minimal Snapshot-shaped object for the collector tests."""
    return types.SimpleNamespace(
        id=snap_id,
        subvolume="/",
        path=path,
        date=None,
        type=None,
        description="",
        schedule="",
    )


class TestUsageCollector:
    @staticmethod
    def _coll(tmp_path, *, btrfs_mounts=None, filesystem_usage=None,
              snapshots=None, du_output=None, du_raises=None):
        """Build a collector with every slow path stubbed."""
        ops = MagicMock()
        ops.BTRFS_BIN = "/usr/sbin/btrfs"
        ops.snapshot_dir = ".snapshots"
        ops.btrfs_mounts.return_value = btrfs_mounts or []
        if du_raises is not None:
            # Override _measure below after construction.
            pass
        ops.filesystem_usage.return_value = filesystem_usage or {
            "overall": {"device_size": 1000}
        }
        sm = MagicMock()
        sm.list_all.return_value = snapshots or []
        store = UsageStore(str(tmp_path / "u.sqlite"))
        coll = UsageCollector(ops, sm, store)
        if du_raises is not None:
            coll._measure = lambda paths: (_ for _ in ()).throw(du_raises)
        elif du_output is not None:
            coll._measure = lambda paths: du_output
        return coll, store

    def test_empty_mounts_yields_empty_but_successful(self, tmp_path) -> None:
        coll, store = self._coll(tmp_path)
        coll.collect_once()
        reader = UsageReader(store.path)
        assert reader.status()["state"] == "ok"
        assert reader.aggregates() == []
        assert reader.snapshots() == []

    def test_skips_mounts_without_snapshot_dir(self, tmp_path, monkeypatch) -> None:
        # Only reject the snapshot directory, not the pytest temp dir
        # the store lives under.
        real_isdir = os.path.isdir
        monkeypatch.setattr(
            os.path, "isdir",
            lambda p: False if p.rstrip("/").endswith("/.snapshots") or p == "/.snapshots"
            else real_isdir(p),
        )
        coll, store = self._coll(
            tmp_path,
            btrfs_mounts=[{"target": "/", "uuid": "u1"}],
        )
        coll.collect_once()
        reader = UsageReader(store.path)
        assert reader.status()["state"] == "ok"
        assert reader.aggregates() == []

    def test_collapses_duplicate_uuid_mounts(self, tmp_path, monkeypatch) -> None:
        # Two rows for the same filesystem (top-level mount + subvol
        # mount) must produce ONE aggregate, not two.
        monkeypatch.setattr(os.path, "isdir", lambda p: True)
        coll, _ = self._coll(
            tmp_path,
            btrfs_mounts=[
                {"target": "/", "uuid": "u1"},
                {"target": "/var", "uuid": "u1"},
            ],
            snapshots=[_make_snapshot("s1", "/.snapshots/s1/root")],
            du_output=[
                {"path": "/.snapshots", "total_bytes": 100, "exclusive_bytes": 10},
                {"path": "/.snapshots/s1/root", "total_bytes": 50, "exclusive_bytes": 5},
            ],
        )
        coll.collect_once()
        reader = UsageReader(str(tmp_path / "u.sqlite"))
        aggs = reader.aggregates()
        assert len(aggs) == 1
        assert aggs[0]["mount"] == "/"

    def test_collects_aggregate_and_per_snapshot_rows(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(os.path, "isdir", lambda p: True)
        coll, store = self._coll(
            tmp_path,
            btrfs_mounts=[{"target": "/", "uuid": "u1"}],
            filesystem_usage={"overall": {"device_size": 1000}},
            snapshots=[
                _make_snapshot("s1", "/.snapshots/s1/root"),
                _make_snapshot("s2", "/.snapshots/s2/root"),
            ],
            du_output=[
                {"path": "/.snapshots", "total_bytes": 100, "exclusive_bytes": 10},
                {"path": "/.snapshots/s1/root", "total_bytes": 40, "exclusive_bytes": 4},
                {"path": "/.snapshots/s2/root", "total_bytes": 60, "exclusive_bytes": 6},
            ],
        )
        coll.collect_once()
        reader = UsageReader(store.path)
        assert reader.aggregate_for("/") == {
            "mount": "/",
            "snapshot_dir": "/.snapshots",
            "total_bytes": 100,
            "exclusive_bytes": 10,
            "device_size_bytes": 1000,
            "measured_at": reader.aggregate_for("/")["measured_at"],
        }
        snaps = sorted(reader.snapshots(), key=lambda r: r["snapshot_id"])
        assert [s["snapshot_id"] for s in snaps] == ["s1", "s2"]
        assert snaps[0]["total_bytes"] == 40

    def test_failure_records_status_failed(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(os.path, "isdir", lambda p: True)
        coll, store = self._coll(
            tmp_path,
            btrfs_mounts=[{"target": "/", "uuid": "u1"}],
            snapshots=[_make_snapshot("s1", "/.snapshots/s1/root")],
            du_raises=RuntimeError("btrfs du exploded"),
        )
        # Pre-populate with data so we can verify failure keeps it.
        store.start_collect()
        store.finish_collect(
            [{
                "mount": "/",
                "snapshot_dir": "/.snapshots",
                "total_bytes": 999,
                "exclusive_bytes": 99,
                "device_size_bytes": 1000,
            }],
            [],
        )
        coll.collect_once()
        reader = UsageReader(store.path)
        assert reader.status()["state"] == "failed"
        assert "exploded" in reader.status()["last_error"]
        # Previous data still visible.
        assert reader.aggregate_for("/")["total_bytes"] == 999

    def test_snapshot_id_extraction(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(os.path, "isdir", lambda p: True)
        coll, _ = self._coll(
            tmp_path,
            btrfs_mounts=[{"target": "/", "uuid": "u1"}],
            snapshots=[_make_snapshot("2026-01-01_00-00-00",
                                    "/.snapshots/2026-01-01_00-00-00/root")],
            du_output=[
                {"path": "/.snapshots", "total_bytes": 100, "exclusive_bytes": 10},
                {"path": "/.snapshots/2026-01-01_00-00-00/root",
                 "total_bytes": 50, "exclusive_bytes": 5},
            ],
        )
        coll.collect_once()
        reader = UsageReader(str(tmp_path / "u.sqlite"))
        rows = reader.snapshots()
        assert rows[0]["snapshot_id"] == "2026-01-01_00-00-00"

    def test_stray_row_before_any_mount_is_skipped(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(os.path, "isdir", lambda p: True)
        coll, _ = self._coll(
            tmp_path,
            btrfs_mounts=[{"target": "/", "uuid": "u1"}],
            # Output starts with a snapshot path — no aggregate row yet.
            du_output=[
                {"path": "/.snapshots/odd/root", "total_bytes": 1, "exclusive_bytes": 1},
                {"path": "/.snapshots", "total_bytes": 100, "exclusive_bytes": 10},
            ],
        )
        coll.collect_once()
        reader = UsageReader(str(tmp_path / "u.sqlite"))
        # The stray row was dropped; the aggregate was kept.
        assert reader.aggregate_for("/")["total_bytes"] == 100
        assert reader.snapshots() == []


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_default_db_path_is_under_var_lib() -> None:
    """Production DB location must be under /var/lib/manaradix."""
    assert DEFAULT_DB_PATH.startswith("/var/lib/manaradix")
