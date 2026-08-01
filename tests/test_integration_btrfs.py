"""Integration tests against a real loopback btrfs filesystem.

These tests exercise the btrfs regex parsers and the snapshot lifecycle
against an actual filesystem (``mkfs.btrfs`` on a loop device) instead of
mocked subprocess output.  They require root and btrfs-progs; the whole
module is skipped otherwise.

Run as root::

    sudo -E pytest tests/test_integration_btrfs.py -v

The fixture creates a 512 MiB loopback filesystem, mounts it, and tears
both down afterwards regardless of test outcome.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from manaradix.core.btrfs_ops import BtrfsOps
from manaradix.core.snapshot_manager import SnapshotManager
from manaradix.model.snapshot import SnapshotType

pytestmark = pytest.mark.skipif(
    os.geteuid() != 0 or shutil.which("mkfs.btrfs") is None,
    reason="requires root and btrfs-progs (loopback filesystem tests)",
)

_IMG_BYTES = 512 * 1024 * 1024


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


@pytest.fixture(scope="module")
def btrfs_fs(tmp_path_factory):
    """Create and mount a loopback btrfs filesystem; yield the mount point."""
    workdir = tmp_path_factory.mktemp("btrfs-loop")
    img = workdir / "fs.img"
    mount = workdir / "mnt"
    mount.mkdir()

    with open(img, "wb") as fh:
        fh.truncate(_IMG_BYTES)

    device = Path(_run(["losetup", "--find", "--show", str(img)]).stdout.strip())
    assert device.exists(), "losetup did not allocate a loop device"

    try:
        _run(["mkfs.btrfs", "-f", "-q", str(device)])
        _run(["mount", str(device), str(mount)])
        yield str(mount)
    finally:
        _run(["umount", str(mount)], check=False)
        _run(["losetup", "-d", str(device)], check=False)


def make_manager(mount: str) -> tuple[BtrfsOps, SnapshotManager]:
    ops = BtrfsOps()
    return ops, SnapshotManager(ops)


# ---------------------------------------------------------------------------
# Real-output parser validation
# ---------------------------------------------------------------------------


class TestRealParsers:
    def test_filesystem_usage(self, btrfs_fs: str) -> None:
        usage = BtrfsOps().filesystem_usage(btrfs_fs)
        overall = usage.get("overall", {})
        assert overall.get("device_size", 0) > 0
        assert overall.get("free_estimated", 0) > 0

    def test_filesystem_show_includes_loop_device(self, btrfs_fs: str) -> None:
        filesystems = BtrfsOps().filesystem_show()
        devices = [
            device["path"]
            for fs in filesystems
            for device in fs["devices"]
        ]
        assert any("/dev/loop" in d for d in devices), f"no loop device in {devices}"
        # Sizes parsed as raw bytes
        assert all(fs["fs_bytes_used"] >= 0 for fs in filesystems)

    def test_btrfs_mounts_includes_loop_mount(self, btrfs_fs: str) -> None:
        mounts = BtrfsOps().btrfs_mounts()
        assert any(m["target"] == btrfs_fs for m in mounts)


# ---------------------------------------------------------------------------
# Snapshot lifecycle against a real filesystem
# ---------------------------------------------------------------------------


class TestSnapshotLifecycle:
    def test_create_get_list_rename_delete(self, btrfs_fs: str) -> None:
        ops, mgr = make_manager(btrfs_fs)

        snap = mgr.create(
            subvolume="/",
            description="integration test",
            mount=btrfs_fs,
        )
        assert snap.type is SnapshotType.MANUAL
        assert snap.subvolume == "/"
        assert snap.path.startswith(f"{btrfs_fs}/.snapshots/")

        # get() reads the snapshot back from disk (info.json sidecar)
        fetched = mgr.get(snap.id, mount=btrfs_fs)
        assert fetched is not None
        assert fetched.description == "integration test"

        # list_all() discovers it from the real filesystem
        listed = mgr.list_all(mount=btrfs_fs)
        assert any(s.id == snap.id for s in listed)

        # rename() updates the description in the sidecar
        mgr.rename(fetched, "renamed description")
        renamed = mgr.get(snap.id, mount=btrfs_fs)
        assert renamed.description == "renamed description"

        # delete() removes the snapshot subvolume from the filesystem
        mgr.delete(snap)
        assert mgr.get(snap.id, mount=btrfs_fs) is None

    def test_prune_keeps_most_recent(self, btrfs_fs: str) -> None:
        ops, mgr = make_manager(btrfs_fs)

        for _ in range(5):
            mgr.create(
                subvolume="/",
                schedule="hourly",
                snapshot_type=SnapshotType.TIMED,
                mount=btrfs_fs,
            )

        deleted = mgr.prune(
            subvolume="/",
            schedule="hourly",
            keep_count=2,
            mount=btrfs_fs,
        )
        assert len(deleted) == 3

        remaining = [
            s for s in mgr.list_all(mount=btrfs_fs)
            if s.schedule == "hourly"
        ]
        assert len(remaining) == 2

    def test_browse_mounts_snapshot_read_only(self, btrfs_fs: str) -> None:
        ops, mgr = make_manager(btrfs_fs)
        snap = mgr.create(subvolume="/", mount=btrfs_fs)
        browse_point = f"{btrfs_fs}-browse"
        Path(browse_point).mkdir(exist_ok=True)

        try:
            ops.mount_subvolume(
                snap.path, browse_point, read_only=True,
            )
            mounts = ops.btrfs_mounts()
            assert any(m["target"] == browse_point for m in mounts)
            # The mount must be read-only
            flags = subprocess.run(
                ["findmnt", "-n", "-o", "OPTIONS", browse_point],
                capture_output=True, text=True, check=True,
            ).stdout
            assert "ro" in flags
        finally:
            ops.umount(browse_point, dry_run=False)

    def test_restore_swap_method(self, btrfs_fs: str) -> None:
        ops, mgr = make_manager(btrfs_fs)

        # Create a test subvolume with a file, snapshot it, modify, restore.
        subvol = f"{btrfs_fs}/subvol"
        _run(["btrfs", "subvolume", "create", subvol])
        _run(["touch", f"{subvol}/original"])

        snap = mgr.create(subvolume=subvol, mount=btrfs_fs)
        _run(["rm", f"{subvol}/original"])
        _run(["touch", f"{subvol}/newfile"])

        pre_restore = mgr.restore(snap, mount=btrfs_fs)
        assert pre_restore.type is SnapshotType.PRE_RESTORE

        # After the swap, the subvolume reflects the snapshot state.
        assert (Path(subvol) / "original").exists()
        assert not (Path(subvol) / "newfile").exists()
