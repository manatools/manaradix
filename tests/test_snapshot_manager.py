"""Unit tests for SnapshotManager."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from manaradix.core.btrfs_ops import (
    BtrfsError,
    BtrfsOps,
    SnapshotNotFoundError,
)
from manaradix.core.snapshot_manager import SnapshotManager
from manaradix.model.snapshot import Snapshot, SnapshotType


def _make_snapshot(**kwargs) -> Snapshot:
    defaults = {
        "id": "2025-01-15_14-00-00",
        "subvolume": "/",
        "path": "/.snapshots/2025-01-15_14-00-00/root",
        "date": datetime(2025, 1, 15, 14, 0, 0),
        "type": SnapshotType.TIMED,
    }
    defaults.update(kwargs)
    return Snapshot(**defaults)


class TestSnapshotManagerCreate:
    @patch("subprocess.run")
    @patch("os.makedirs")
    @patch("builtins.open", new_callable=MagicMock)
    def test_create_writes_metadata(
        self,
        mock_file: MagicMock,
        mock_makedirs: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="", stderr=""
        )
        ops = BtrfsOps()
        mgr = SnapshotManager(ops)
        snap = mgr.create("/", description="test snapshot")

        assert snap.subvolume == "/"
        assert snap.description == "test snapshot"
        assert snap.type == SnapshotType.MANUAL
        assert len(mgr.get_journal()) == 1
        assert mgr.get_journal()[0]["action"] == "snapshot_created"

    def test_create_dry_run(self) -> None:
        ops = BtrfsOps()
        mgr = SnapshotManager(ops)
        snap = mgr.create("/", description="dry test", dry_run=True)
        assert snap.description == "dry test"
        # Journal should NOT have an entry for dry run
        assert len(mgr.get_journal()) == 0


class TestSnapshotManagerList:
    @patch.object(BtrfsOps, "list_snapshots")
    def test_list_all_delegates(self, mock_list: MagicMock) -> None:
        mock_list.return_value = [_make_snapshot(), _make_snapshot(id="snap2")]
        ops = BtrfsOps()
        mgr = SnapshotManager(ops)
        snaps = mgr.list_all("/")
        assert len(snaps) == 2

    def test_list_all_dry_run(self) -> None:
        ops = BtrfsOps()
        mgr = SnapshotManager(ops)
        snaps = mgr.list_all("/", dry_run=True)
        assert snaps == []


class TestSnapshotManagerGet:
    @patch.object(BtrfsOps, "list_snapshots")
    def test_get_found(self, mock_list: MagicMock) -> None:
        snap = _make_snapshot(id="target")
        mock_list.return_value = [snap, _make_snapshot(id="other")]
        ops = BtrfsOps()
        mgr = SnapshotManager(ops)
        assert mgr.get("target", "/") is snap

    @patch.object(BtrfsOps, "list_snapshots")
    def test_get_not_found(self, mock_list: MagicMock) -> None:
        mock_list.return_value = [_make_snapshot(id="other")]
        ops = BtrfsOps()
        mgr = SnapshotManager(ops)
        assert mgr.get("nonexistent", "/") is None


class TestSnapshotManagerDelete:
    @patch.object(BtrfsOps, "delete_snapshot")
    @patch("os.path.isdir", return_value=True)
    @patch("os.path.dirname", return_value="/.snapshots/2025-01-15_14-00-00")
    @patch("os.remove")
    @patch("os.listdir", return_value=[])
    @patch("shutil.rmtree")
    def test_delete_removes_metadata(
        self,
        mock_rmtree: MagicMock,
        mock_listdir: MagicMock,
        mock_remove: MagicMock,
        mock_dirname: MagicMock,
        mock_isdir: MagicMock,
        mock_delete: MagicMock,
    ) -> None:
        ops = BtrfsOps()
        mgr = SnapshotManager(ops)
        snap = _make_snapshot()
        mgr.delete(snap)
        mock_delete.assert_called_once_with(snap.path, dry_run=False)
        assert mgr.get_journal()[0]["action"] == "snapshot_deleted"

    def test_delete_missing_raises(self) -> None:
        ops = BtrfsOps()
        mgr = SnapshotManager(ops)
        snap = _make_snapshot(path="/nonexistent/path")
        with patch("os.path.isdir", return_value=False):
            with pytest.raises(SnapshotNotFoundError):
                mgr.delete(snap)


class TestSnapshotManagerRename:
    @patch.object(BtrfsOps, "write_snapshot_info")
    def test_rename_updates_description(self, mock_write: MagicMock) -> None:
        ops = BtrfsOps()
        mgr = SnapshotManager(ops)
        snap = _make_snapshot(description="old name")
        updated = mgr.rename(snap, "new name")
        assert updated.description == "new name"
        mock_write.assert_called_once()
        assert mgr.get_journal()[0]["action"] == "snapshot_renamed"


class TestSnapshotManagerJournal:
    def test_get_journal_is_copy(self) -> None:
        ops = BtrfsOps()
        mgr = SnapshotManager(ops)
        mgr._journal_op("test", {"key": "value"})
        journal = mgr.get_journal()
        journal.append({"mutated": True})
        assert len(mgr.get_journal()) == 1  # original unchanged


class TestSnapshotManagerExport:
    def test_export_read_only_snapshot(self) -> None:
        ops = BtrfsOps()
        mgr = SnapshotManager(ops)
        snap = _make_snapshot(type=SnapshotType.READ_ONLY)
        # Dry run — should not raise
        mgr.export_snapshot(snap, "/tmp/export.btrfs", dry_run=True)

    def test_export_read_write_raises(self) -> None:
        ops = BtrfsOps()
        mgr = SnapshotManager(ops)
        snap = _make_snapshot(type=SnapshotType.MANUAL)
        with pytest.raises(ValueError, match="read-only"):
            mgr.export_snapshot(snap, "/tmp/export.btrfs")

    @patch("subprocess.run")
    @patch("builtins.open", new_callable=MagicMock)
    def test_export_runs_btrfs_send(
        self,
        mock_file: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        ops = BtrfsOps()
        mgr = SnapshotManager(ops)
        snap = _make_snapshot(type=SnapshotType.READ_ONLY)
        mgr.export_snapshot(snap, "/tmp/export.btrfs")
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "send" in args


class TestSnapshotManagerBrowse:
    @patch("subprocess.run")
    @patch("os.makedirs")
    def test_browse_mounts_snapshot(
        self, mock_makedirs: MagicMock, mock_run: MagicMock
    ) -> None:
        ops = BtrfsOps()
        mgr = SnapshotManager(ops)
        snap = _make_snapshot()
        mgr.browse(snap, "/mnt/test-browse")
        mock_run.assert_called_once()

    @patch("subprocess.run", side_effect=FileNotFoundError)
    @patch("os.makedirs")
    def test_browse_mount_failure(
        self, mock_makedirs: MagicMock, mock_run: MagicMock
    ) -> None:
        ops = BtrfsOps()
        mgr = SnapshotManager(ops)
        snap = _make_snapshot()
        with pytest.raises(BtrfsError, match="Failed to mount"):
            mgr.browse(snap)


class TestSnapshotManagerCreateWithSchedule:
    @patch("subprocess.run")
    @patch("os.makedirs")
    @patch("builtins.open", new_callable=MagicMock)
    def test_create_with_schedule(
        self,
        mock_file: MagicMock,
        mock_makedirs: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        ops = BtrfsOps()
        mgr = SnapshotManager(ops)
        snap = mgr.create(
            "/", description="daily snapshot",
            snapshot_type=SnapshotType.TIMED, schedule="daily",
        )
        assert snap.schedule == "daily"
        assert snap.type == SnapshotType.TIMED


class TestSnapshotManagerPrune:
    def _snap(self, id: str, schedule: str, days_ago: int = 0) -> Snapshot:
        """Helper to build a snapshot at a relative date."""
        from datetime import timedelta

        return Snapshot(
            id=id,
            subvolume="/",
            path=f"/.snapshots/{id}/root",
            date=datetime(2025, 1, 15, 14, 0, 0) - timedelta(days=days_ago),
            type=SnapshotType.TIMED,
            schedule=schedule,
        )

    @patch.object(BtrfsOps, "list_snapshots")
    @patch.object(BtrfsOps, "delete_snapshot")
    @patch("os.path.isdir", return_value=True)
    @patch("os.path.dirname")
    @patch("os.remove")
    @patch("os.listdir", return_value=[])
    @patch("shutil.rmtree")
    def test_prune_keeps_count(
        self,
        mock_rmtree: MagicMock,
        mock_listdir: MagicMock,
        mock_remove: MagicMock,
        mock_dirname: MagicMock,
        mock_isdir: MagicMock,
        mock_delete: MagicMock,
        mock_list: MagicMock,
    ) -> None:
        mock_list.return_value = [
            self._snap("s1", "daily", days_ago=0),
            self._snap("s2", "daily", days_ago=1),
            self._snap("s3", "daily", days_ago=2),
            self._snap("s4", "daily", days_ago=3),
            self._snap("s5", "daily", days_ago=4),
        ]
        ops = BtrfsOps()
        mgr = SnapshotManager(ops)
        deleted = mgr.prune("/", "daily", keep_count=3)

        assert len(deleted) == 2
        assert "s4" in deleted
        assert "s5" in deleted
        assert "s1" not in deleted
        assert mock_delete.call_count == 2

    @patch.object(BtrfsOps, "list_snapshots")
    @patch.object(BtrfsOps, "delete_snapshot")
    @patch("os.path.isdir", return_value=True)
    @patch("os.path.dirname")
    @patch("os.remove")
    @patch("os.listdir", return_value=[])
    @patch("shutil.rmtree")
    def test_prune_no_excess(
        self,
        mock_rmtree: MagicMock,
        mock_listdir: MagicMock,
        mock_remove: MagicMock,
        mock_dirname: MagicMock,
        mock_isdir: MagicMock,
        mock_delete: MagicMock,
        mock_list: MagicMock,
    ) -> None:
        mock_list.return_value = [self._snap("s1", "daily")]
        ops = BtrfsOps()
        mgr = SnapshotManager(ops)
        deleted = mgr.prune("/", "daily", keep_count=5)
        assert deleted == []
        mock_delete.assert_not_called()

    @patch.object(BtrfsOps, "list_snapshots")
    @patch.object(BtrfsOps, "delete_snapshot")
    @patch("os.path.isdir", return_value=True)
    @patch("os.path.dirname")
    @patch("os.remove")
    @patch("os.listdir", return_value=[])
    @patch("shutil.rmtree")
    def test_prune_different_schedule_unaffected(
        self,
        mock_rmtree: MagicMock,
        mock_listdir: MagicMock,
        mock_remove: MagicMock,
        mock_dirname: MagicMock,
        mock_isdir: MagicMock,
        mock_delete: MagicMock,
        mock_list: MagicMock,
    ) -> None:
        mock_list.return_value = [
            self._snap("s1", "daily", days_ago=0),
            self._snap("s2", "hourly", days_ago=1),
            self._snap("s3", "hourly", days_ago=2),
            self._snap("s4", "daily", days_ago=3),
        ]
        ops = BtrfsOps()
        mgr = SnapshotManager(ops)
        deleted = mgr.prune("/", "daily", keep_count=1)
        # Only daily snapshots should be considered; s4 is excess
        assert len(deleted) == 1
        assert "s4" in deleted
        # hourly snapshots untouched
        assert "s2" not in deleted
        assert "s3" not in deleted

    @patch.object(BtrfsOps, "list_snapshots")
    @patch.object(BtrfsOps, "delete_snapshot")
    @patch("os.path.isdir", return_value=True)
    @patch("os.path.dirname")
    @patch("os.remove")
    @patch("os.listdir", return_value=[])
    @patch("shutil.rmtree")
    def test_prune_dry_run(
        self,
        mock_rmtree: MagicMock,
        mock_listdir: MagicMock,
        mock_remove: MagicMock,
        mock_dirname: MagicMock,
        mock_isdir: MagicMock,
        mock_delete: MagicMock,
        mock_list: MagicMock,
    ) -> None:
        mock_list.return_value = [
            self._snap("s1", "daily", days_ago=0),
            self._snap("s2", "daily", days_ago=1),
        ]
        ops = BtrfsOps()
        mgr = SnapshotManager(ops)
        deleted = mgr.prune("/", "daily", keep_count=1, dry_run=True)
        assert len(deleted) == 1
        # delete_snapshot was called with dry_run=True (no real delete)
        call_kwargs = mock_delete.call_args
        assert call_kwargs[1].get("dry_run") is True

    def test_prune_negative_keep_count(self) -> None:
        ops = BtrfsOps()
        mgr = SnapshotManager(ops)
        with pytest.raises(ValueError, match="non-negative"):
            mgr.prune("/", "daily", keep_count=-1)
