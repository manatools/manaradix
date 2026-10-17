"""Tests for the `usage` CLI subcommands.

The CLI commands are thin wrappers around the UsageStore and UsageCollector:
- `usage collect`: runs a single measurement cycle
- `usage status`: prints collector state (last run status, timestamps)
- `usage show`: prints aggregate and per-snapshot usage data

Tests exercise the real command implementations against temp DBs, following
the same pattern as `test_activity_status.py` (monkeypatch the default
DB path constant).
"""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

from manaradix.core import usage_store
from manaradix.core.usage_store import UsageStore
from manaradix.module import (
    _cmd_usage_collect,
    _cmd_usage_show,
    _cmd_usage_status,
)


# ---------------------------------------------------------------------------
# usage collect
# ---------------------------------------------------------------------------


class TestCmdUsageCollect:
    def test_collect_success(self, tmp_path, monkeypatch):
        """`usage collect` runs the collector and returns 0."""
        monkeypatch.setattr(usage_store, "DEFAULT_DB_PATH", str(tmp_path / "u.sqlite"))
        ctx = MagicMock()
        ctx.btrfs_ops.btrfs_mounts.return_value = []
        args = argparse.Namespace()

        result = _cmd_usage_collect(ctx, args)

        assert result == 0

    def test_collect_failure_returns_1(self, tmp_path, monkeypatch):
        """Collector exceptions turn into exit code 1."""
        monkeypatch.setattr(usage_store, "DEFAULT_DB_PATH", str(tmp_path / "u.sqlite"))
        ctx = MagicMock()
        ctx.btrfs_ops.btrfs_mounts.side_effect = RuntimeError("boom")
        args = argparse.Namespace()

        result = _cmd_usage_collect(ctx, args)

        assert result == 1


# ---------------------------------------------------------------------------
# usage status
# ---------------------------------------------------------------------------


class TestCmdUsageStatus:
    def test_status_never_run(self, tmp_path, monkeypatch, capsys):
        """Before any collection, status is 'idle' with no timestamps."""
        monkeypatch.setattr(usage_store, "DEFAULT_DB_PATH", str(tmp_path / "no.sqlite"))
        ctx = MagicMock()
        args = argparse.Namespace()

        result = _cmd_usage_status(ctx, args)

        assert result == 0
        out = capsys.readouterr().out
        assert "Status: idle" in out
        assert "Last started:" not in out
        assert "Last finished:" not in out

    def test_status_after_successful_collect(self, tmp_path, monkeypatch, capsys):
        """After a successful collect, status shows timestamps."""
        db = str(tmp_path / "u.sqlite")
        monkeypatch.setattr(usage_store, "DEFAULT_DB_PATH", db)
        # Pre-populate via the writer
        store = UsageStore(db)
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
        ctx = MagicMock()
        args = argparse.Namespace()

        result = _cmd_usage_status(ctx, args)

        assert result == 0
        out = capsys.readouterr().out
        assert "Status: ok" in out
        assert "Last started:" in out
        assert "Last finished:" in out

    def test_status_after_failure(self, tmp_path, monkeypatch, capsys):
        """After a failure, status shows the error message."""
        db = str(tmp_path / "u.sqlite")
        monkeypatch.setattr(usage_store, "DEFAULT_DB_PATH", db)
        store = UsageStore(db)
        store.start_collect()
        store.fail_collect("btrfs du exploded")
        ctx = MagicMock()
        args = argparse.Namespace()

        result = _cmd_usage_status(ctx, args)

        assert result == 0
        out = capsys.readouterr().out
        assert "Status: failed" in out
        assert "Last error: btrfs du exploded" in out


# ---------------------------------------------------------------------------
# usage show
# ---------------------------------------------------------------------------


class TestCmdUsageShow:
    def test_show_no_data(self, tmp_path, monkeypatch, capsys):
        """Before any collection, show prints 'no data' hint."""
        monkeypatch.setattr(usage_store, "DEFAULT_DB_PATH", str(tmp_path / "no.sqlite"))
        ctx = MagicMock()
        args = argparse.Namespace()

        result = _cmd_usage_show(ctx, args)

        assert result == 0
        out = capsys.readouterr().out
        assert "No usage data available" in out

    def test_show_with_data(self, tmp_path, monkeypatch, capsys):
        """After a collect, show prints aggregate + per-snapshot rows."""
        db = str(tmp_path / "u.sqlite")
        monkeypatch.setattr(usage_store, "DEFAULT_DB_PATH", db)
        store = UsageStore(db)
        store.start_collect()
        store.finish_collect(
            [{
                "mount": "/",
                "snapshot_dir": "/.snapshots",
                "total_bytes": 1073741824,
                "exclusive_bytes": 536870912,
                "device_size_bytes": 10737418240,
            }],
            [
                {
                    "mount": "/",
                    "snapshot_id": "snap1",
                    "snapshot_path": "/.snapshots/snap1/root",
                    "total_bytes": 536870912,
                    "exclusive_bytes": 268435456,
                },
                {
                    "mount": "/",
                    "snapshot_id": "snap2",
                    "snapshot_path": "/.snapshots/snap2/root",
                    "total_bytes": 268435456,
                    "exclusive_bytes": 134217728,
                },
            ],
        )
        ctx = MagicMock()
        args = argparse.Namespace()

        result = _cmd_usage_show(ctx, args)

        assert result == 0
        out = capsys.readouterr().out
        # Aggregate row present
        assert "/" in out
        assert "1.0 GiB" in out or "1073741824" in out
        assert "512.0 MiB" in out or "536870912" in out
        # Per-snapshot rows
        assert "snap1" in out
        assert "snap2" in out

    def test_show_with_empty_snapshots(self, tmp_path, monkeypatch, capsys):
        """Aggregate-only collect (no snapshots) still shows the aggregate."""
        db = str(tmp_path / "u.sqlite")
        monkeypatch.setattr(usage_store, "DEFAULT_DB_PATH", db)
        store = UsageStore(db)
        store.start_collect()
        store.finish_collect(
            [{
                "mount": "/",
                "snapshot_dir": "/.snapshots",
                "total_bytes": 1073741824,
                "exclusive_bytes": 536870912,
                "device_size_bytes": 10737418240,
            }],
            [],
        )
        ctx = MagicMock()
        args = argparse.Namespace()

        result = _cmd_usage_show(ctx, args)

        assert result == 0
        out = capsys.readouterr().out
        assert "/" in out
        # No per-snapshot rows means no 'snap' substrings from the loop
        assert "snap1" not in out


# ---------------------------------------------------------------------------
# privilege classification (uses the existing pkexec tables)
# ---------------------------------------------------------------------------


class TestUsagePrivilegeClassification:
    def test_collect_is_privileged(self):
        from manaradix.utils.pkexec import needs_elevation
        assert needs_elevation("usage", "collect") is True

    def test_status_is_read_only(self):
        from manaradix.utils.pkexec import needs_elevation
        assert needs_elevation("usage", "status") is False

    def test_show_is_read_only(self):
        from manaradix.utils.pkexec import needs_elevation
        assert needs_elevation("usage", "show") is False

    def test_unknown_usage_subcommand_defaults_to_elevated(self):
        from manaradix.utils.pkexec import needs_elevation
        assert needs_elevation("usage", "bogus") is True


# ---------------------------------------------------------------------------
# parser wiring
# ---------------------------------------------------------------------------


class TestUsageParser:
    def test_usage_collect_parses(self):
        from manaradix.module import _build_parser
        parser = _build_parser()
        args = parser.parse_args(["usage", "collect"])
        assert args.command == "usage"
        assert args.usage_command == "collect"

    def test_usage_status_parses(self):
        from manaradix.module import _build_parser
        parser = _build_parser()
        args = parser.parse_args(["usage", "status"])
        assert args.command == "usage"
        assert args.usage_command == "status"

    def test_usage_show_parses(self):
        from manaradix.module import _build_parser
        parser = _build_parser()
        args = parser.parse_args(["usage", "show"])
        assert args.command == "usage"
        assert args.usage_command == "show"
