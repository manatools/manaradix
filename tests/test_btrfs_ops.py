"""Unit tests for BtrfsOps — mocking the btrfs CLI."""

import json
import subprocess
from datetime import datetime
from unittest.mock import MagicMock, mock_open, patch

import pytest

from manaradix.core.btrfs_ops import (
    BtrfsError,
    BtrfsOps,
    BtrfsPermissionError,
    SnapshotNotFoundError,
)
from manaradix.model.snapshot import Snapshot, SnapshotType


class TestBtrfsOpsInit:
    def test_default_snapshot_dir(self) -> None:
        ops = BtrfsOps()
        assert ops.snapshot_dir == ".snapshots"

    def test_custom_snapshot_dir(self) -> None:
        ops = BtrfsOps(snapshot_dir="/mnt/data/.my-snaps")
        assert ops.snapshot_dir == "/mnt/data/.my-snaps"

    def test_strips_trailing_slash(self) -> None:
        ops = BtrfsOps(snapshot_dir=".snapshots/")
        assert ops.snapshot_dir == ".snapshots"


class TestRunDryRun:
    def test_dry_run_does_not_execute(self) -> None:
        ops = BtrfsOps()
        result = ops._run(["subvolume", "list", "/"], dry_run=True)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_dry_run_returns_fake_completed_process(self) -> None:
        ops = BtrfsOps()
        result = ops._run(["some", "command"], dry_run=True)
        assert isinstance(result, subprocess.CompletedProcess)


class TestRunReal:
    @patch("subprocess.run")
    def test_successful_run(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            ["btrfs", "subvolume", "list", "/"],
            returncode=0,
            stdout="ID 257 gen 42 top level 5 path root\n",
            stderr="",
        )
        ops = BtrfsOps()
        result = ops._run(["subvolume", "list", "/"])
        assert result.returncode == 0
        assert "ID 257" in result.stdout

    @patch("subprocess.run")
    def test_permission_error(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1,
            cmd=["btrfs"],
            stderr="operation not permitted",
        )
        ops = BtrfsOps()
        with pytest.raises(BtrfsPermissionError):
            ops._run(["subvolume", "create", "/test"])

    @patch("subprocess.run")
    def test_generic_error(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1,
            cmd=["btrfs"],
            stderr="something went wrong",
        )
        ops = BtrfsOps()
        with pytest.raises(BtrfsError):
            ops._run(["subvolume", "delete", "/nope"])


class TestCreateSnapshot:
    @patch("subprocess.run")
    def test_create_read_only(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            ["btrfs", "subvolume", "snapshot", "-r", "/", "/dest"],
            returncode=0,
            stdout="",
            stderr="",
        )
        ops = BtrfsOps()
        snap = ops.create_snapshot("/", "/dest", read_only=True)
        assert snap.type == SnapshotType.READ_ONLY
        assert snap.subvolume == "/"
        assert snap.path == "/dest"

    @patch("subprocess.run")
    def test_create_read_write(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            ["btrfs", "subvolume", "snapshot", "/", "/dest"],
            returncode=0,
            stdout="",
            stderr="",
        )
        ops = BtrfsOps()
        snap = ops.create_snapshot("/", "/dest", read_only=False)
        assert snap.type == SnapshotType.MANUAL

    def test_create_dry_run(self) -> None:
        ops = BtrfsOps()
        snap = ops.create_snapshot("/", "/dest", dry_run=True)
        assert snap.type == SnapshotType.READ_ONLY
        assert snap.subvolume == "/"

    @patch("subprocess.run")
    @patch("os.makedirs")
    def test_create_mkdirs_destination_parent(
        self, mock_mkdir: MagicMock, mock_run: MagicMock
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            ["btrfs", "subvolume", "snapshot", "-r", "/", "/dest/root"],
            returncode=0,
            stdout="",
            stderr="",
        )
        ops = BtrfsOps()
        ops.create_snapshot("/", "/dest/root")
        # btrfs requires the destination's parent to exist.
        mock_mkdir.assert_called_once_with("/dest", exist_ok=True)

    @patch("os.makedirs")
    def test_create_dry_run_does_not_mkdir(self, mock_mkdir: MagicMock) -> None:
        ops = BtrfsOps()
        ops.create_snapshot("/", "/dest/root", dry_run=True)
        mock_mkdir.assert_not_called()


class TestSnapshotInfo:
    def test_sidecar_lives_next_to_snapshot(self, tmp_path) -> None:
        ops = BtrfsOps()
        snap = Snapshot(
            id="2026-08-02_10-00-00",
            subvolume="/",
            path=str(tmp_path / "2026-08-02_10-00-00" / "root"),
            date=datetime(2026, 8, 2, 10, 0, 0),
            type=SnapshotType.READ_ONLY,
        )
        ops.write_snapshot_info(snap)
        info = tmp_path / "2026-08-02_10-00-00" / "info.json"
        # The sidecar sits next to the snapshot subvolume, never
        # inside it: read-only snapshots reject writes with EROFS.
        assert info.is_file()
        assert not (
            tmp_path / "2026-08-02_10-00-00" / "root" / "info.json"
        ).exists()
        assert json.loads(info.read_text())["id"] == "2026-08-02_10-00-00"

    def test_roundtrip_preserves_subvolume_path(self, tmp_path) -> None:
        """write + list must agree on the snapshot's subvolume path —
        the timestamp directory is not what btrfs acts on."""
        ops = BtrfsOps()
        ts = "2026-08-02_10-00-00"
        subvol = tmp_path / ".snapshots" / ts / "root"
        subvol.mkdir(parents=True)
        snap = Snapshot(
            id="root",
            subvolume="/",
            path=str(subvol),
            date=datetime(2026, 8, 2, 10, 0, 0),
            type=SnapshotType.READ_ONLY,
        )
        ops.write_snapshot_info(snap)
        listed = ops.list_snapshots(str(tmp_path))
        assert len(listed) == 1
        assert listed[0].path == str(subvol)

    def test_sidecar_read_joins_named_subvolume(self, tmp_path) -> None:
        ops = BtrfsOps()
        snap_dir = tmp_path / "2026-08-02_10-00-00"
        snap_dir.mkdir()
        info = snap_dir / "info.json"
        info.write_text(
            json.dumps(
                {
                    "id": "home",
                    "subvolume": "/home",
                    "date": "2026-08-02T10:00:00",
                    "type": "manual",
                }
            )
        )
        snap = ops._read_snapshot_info(str(info), str(snap_dir))
        assert snap is not None
        assert snap.path == str(snap_dir / "home")


class TestListSubvolumes:
    @patch("subprocess.run")
    def test_parses_list_output(self, mock_run: MagicMock) -> None:
        # First call: subvolume list
        list_output = (
            "ID 257 gen 42 parent 5 top level 5 path root\n"
            "ID 258 gen 43 parent 5 top level 5 path home\n"
        )
        # Second call: subvolume show (one per subvolume)
        show_output = "UUID: aaaa-bbbb\nParent UUID: cccc-dddd\n"

        mock_run.side_effect = [
            subprocess.CompletedProcess(
                ["btrfs", "subvolume", "list", "-p", "/"],
                returncode=0,
                stdout=list_output,
                stderr="",
            ),
            subprocess.CompletedProcess(
                ["btrfs", "subvolume", "show", "/root"],
                returncode=0,
                stdout=show_output,
                stderr="",
            ),
            subprocess.CompletedProcess(
                ["btrfs", "subvolume", "show", "/home"],
                returncode=0,
                stdout=show_output,
                stderr="",
            ),
        ]

        ops = BtrfsOps()
        subvols = ops.list_subvolumes("/")
        assert len(subvols) == 2
        assert subvols[0].id == 257
        assert subvols[0].path == "root"
        assert subvols[0].parent_id == 5
        assert subvols[1].id == 258
        assert subvols[1].path == "home"

    def test_dry_run_returns_empty(self) -> None:
        ops = BtrfsOps()
        result = ops.list_subvolumes("/", dry_run=True)
        assert result == []


class TestSnapshotPath:
    @patch("subprocess.run")
    def test_snapshot_path_root(self, mock_run: MagicMock) -> None:
        ops = BtrfsOps()
        # Freeze time so the timestamp is deterministic
        with patch(
            "manaradix.core.btrfs_ops.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 15, 14, 0, 0)
            mock_dt.strftime = datetime.strftime
            path = ops._snapshot_path("/", "/", "2025-01-15_14-00-00")
            assert path == "/.snapshots/2025-01-15_14-00-00/root"

    def test_snapshot_path_home(self) -> None:
        ops = BtrfsOps()
        path = ops._snapshot_path("/", "/home", "2025-01-15_14-00-00")
        assert path == "/.snapshots/2025-01-15_14-00-00/home"


class TestParseHelpers:
    def test_parse_subvol_list_line(self) -> None:
        from manaradix.core.btrfs_ops import (
            _parse_subvol_list_line,
        )

        result = _parse_subvol_list_line(
            "ID 257 gen 42 parent 5 top level 5 path root"
        )
        assert result is not None
        assert result["id"] == 257
        assert result["path"] == "root"
        assert result["parent_id"] == 5

    def test_parse_subvol_list_line_no_parent(self) -> None:
        from manaradix.core.btrfs_ops import (
            _parse_subvol_list_line,
        )

        result = _parse_subvol_list_line(
            "ID 5 gen 10 top level 5 path <FS_TREE>/"
        )
        assert result is not None
        assert result["id"] == 5
        assert result["parent_id"] == 0

    def test_parse_subvol_list_line_invalid(self) -> None:
        from manaradix.core.btrfs_ops import (
            _parse_subvol_list_line,
        )

        assert _parse_subvol_list_line("some random text") is None

    def test_parse_subvol_show(self) -> None:
        from manaradix.core.btrfs_ops import (
            _parse_subvol_show,
        )

        text = "UUID:\t01234567-89ab-cdef-0123-456789abcdef\nParent UUID:\tdeadbeef-dead-beef-dead-beefdeadbeef\n"
        result = _parse_subvol_show(text)
        assert result["uuid"] == "01234567-89ab-cdef-0123-456789abcdef"
        assert result["parent_uuid"] == "deadbeef-dead-beef-dead-beefdeadbeef"

    def test_parse_subvol_show_no_match(self) -> None:
        from manaradix.core.btrfs_ops import (
            _parse_subvol_show,
        )

        assert _parse_subvol_show("no uuids here") == {}


# ---------------------------------------------------------------------------
# btrfs filesystem show / findmnt tests
# ---------------------------------------------------------------------------

_FS_SHOW_SAMPLE = """\
Label: none  uuid: 3149ac5f-25ee-4805-b0ae-e6679af72699
\tTotal devices 1 FS bytes used 19005067264
\tdevid    1 size 0 used 0 path /dev/nvme0n1p3 MISSING

Label: 'root'  uuid: efff4471-576f-4fbb-976e-162761395cfa
\tTotal devices 2 FS bytes used 13139845120
\tdevid    1 size 32212254720 used 13139845120 path /dev/sda2
\tdevid    2 size 32212254720 used 0 path /dev/sdb2
"""


class TestFilesystemShow:
    @patch("subprocess.run")
    def test_parses_multiple_filesystems(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            ["btrfs", "filesystem", "show", "--raw"],
            returncode=0,
            stdout=_FS_SHOW_SAMPLE,
            stderr="",
        )
        ops = BtrfsOps()
        filesystems = ops.filesystem_show()

        assert len(filesystems) == 2

        first = filesystems[0]
        assert first["label"] == ""  # "Label: none"
        assert first["uuid"] == "3149ac5f-25ee-4805-b0ae-e6679af72699"
        assert first["total_devices"] == 1
        assert first["fs_bytes_used"] == 19005067264
        assert first["devices"] == [
            {
                "devid": 1, "size": 0, "used": 0,
                "path": "/dev/nvme0n1p3", "missing": True,
            },
        ]

        second = filesystems[1]
        assert second["label"] == "root"  # quotes stripped
        assert second["total_devices"] == 2
        assert len(second["devices"]) == 2
        assert second["devices"][0] == {
            "devid": 1, "size": 32212254720, "used": 13139845120,
            "path": "/dev/sda2", "missing": False,
        }
        assert second["devices"][1]["path"] == "/dev/sdb2"

    @patch("subprocess.run")
    def test_error_returns_empty_list(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1, cmd=["btrfs"], stderr="ERROR: not a btrfs filesystem",
        )
        assert BtrfsOps().filesystem_show() == []

    def test_dry_run_returns_empty(self) -> None:
        assert BtrfsOps().filesystem_show(dry_run=True) == []


class TestBtrfsMounts:
    @patch("subprocess.run")
    def test_parses_mount_rows(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            ["/usr/bin/findmnt", "-t", "btrfs", "-o", "TARGET,SOURCE,UUID", "-r"],
            returncode=0,
            stdout=(
                "TARGET\tSOURCE\tUUID\n"
                "/\t/dev/nvme0n1p3\t3149ac5f-25ee-4805-b0ae-e6679af72699\n"
                "/home\t/dev/mapper/crypt_nvme0n1p6\tefff4471-576f-4fbb-976e-162761395cfa\n"
            ),
            stderr="",
        )
        ops = BtrfsOps()
        mounts = ops.btrfs_mounts()

        assert len(mounts) == 2
        assert mounts[0] == {
            "target": "/",
            "source": "/dev/nvme0n1p3",
            "uuid": "3149ac5f-25ee-4805-b0ae-e6679af72699",
        }
        assert mounts[1]["target"] == "/home"

    @patch("subprocess.run")
    def test_parses_space_separated_rows(self, mock_run: MagicMock) -> None:
        # Real findmnt -r separates columns with single spaces, not tabs.
        mock_run.return_value = subprocess.CompletedProcess(
            ["/usr/bin/findmnt", "-t", "btrfs", "-o", "TARGET,SOURCE,UUID", "-r"],
            returncode=0,
            stdout=(
                "TARGET SOURCE UUID\n"
                "/ /dev/nvme0n1p3 3149ac5f-25ee-4805-b0ae-e6679af72699\n"
                "/home /dev/mapper/crypt_nvme0n1p6 efff4471-576f-4fbb-976e-162761395cfa\n"
            ),
            stderr="",
        )
        mounts = BtrfsOps().btrfs_mounts()
        assert len(mounts) == 2
        assert mounts[0] == {
            "target": "/",
            "source": "/dev/nvme0n1p3",
            "uuid": "3149ac5f-25ee-4805-b0ae-e6679af72699",
        }
        assert mounts[1]["target"] == "/home"

    @patch("subprocess.run")
    def test_no_mounts_returns_empty(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            ["/usr/bin/findmnt"], returncode=0, stdout="TARGET\tSOURCE\tUUID\n", stderr="",
        )
        assert BtrfsOps().btrfs_mounts() == []

    @patch("subprocess.run")
    def test_findmnt_missing_returns_empty(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = OSError("findmnt not found")
        assert BtrfsOps().btrfs_mounts() == []

    def test_dry_run_returns_empty(self) -> None:
        assert BtrfsOps().btrfs_mounts(dry_run=True) == []


# ---------------------------------------------------------------------------
# filesystem_usage / filesystem_du tests
# ---------------------------------------------------------------------------


# Real `btrfs filesystem usage -b /` output (btrfs-progs 6.19): raw
# numbers, blank lines between sections, and a percentage in parentheses
# trailing the Used value of each profile section.
_FS_USAGE_SAMPLE = """\
Overall:
    Device size:\t\t      211618365440
    Device allocated:\t      22544384000
    Device unallocated:\t      189073981440
    Device missing:\t      0
    Used:\t\t      19311316992
    Free (estimated):\t      190525214720\t(min: 96002904064)
    Free (statfs, best-effort):\t      190525214720
    Data ratio:\t\t      1.00
    Metadata ratio:\t      2.00
    Global reserve:\t      536870912\t(used: 0)
    Multiple profiles:\t      no

Data,single: Size:20409483264, Used:18928889856 (92.75%)

Metadata,DUP: Size:1073741824, Used:382238720 (35.60%)

System,DUP: Size:8388608, Used:16384 (0.20%)
"""


class TestFilesystemUsage:
    @patch("subprocess.run")
    def test_parses_internal_sections(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            ["btrfs", "filesystem", "usage", "-b", "/"],
            returncode=0,
            stdout=_FS_USAGE_SAMPLE,
            stderr="",
        )
        overall = BtrfsOps().filesystem_usage("/")["overall"]
        assert overall["device_size"] == 211618365440
        assert overall["free_estimated"] == 190525214720
        assert overall["data"] == {"size": 20409483264, "used": 18928889856}
        assert overall["metadata"] == {"size": 1073741824, "used": 382238720}
        assert overall["system"] == {"size": 8388608, "used": 16384}

    @patch("subprocess.run")
    def test_internal_sections_absent_when_missing(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            ["btrfs", "filesystem", "usage", "-b", "/"],
            returncode=0,
            stdout="Overall:\n    Device size:\t\t      1000\n",
            stderr="",
        )
        overall = BtrfsOps().filesystem_usage("/")["overall"]
        assert "data" not in overall
        assert "metadata" not in overall
        assert "system" not in overall

    @patch("subprocess.run")
    def test_error_returns_empty(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1, cmd=["btrfs"], stderr="not a btrfs fs"
        )
        assert BtrfsOps().filesystem_usage("/") == {}


class TestFilesystemDu:
    @patch("subprocess.run")
    def test_parses_summary_row(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            ["btrfs", "filesystem", "du", "-s", "--raw", "/.snapshots"],
            returncode=0,
            stdout=(
                "     Total   Exclusive  Set shared  Filename\n"
                " 12288000    1228800          0  /.snapshots\n"
            ),
            stderr="",
        )
        assert BtrfsOps().filesystem_du("/.snapshots") == {
            "total": 12288000,
            "exclusive": 1228800,
        }

    @patch("subprocess.run")
    def test_uses_raw_flag_not_b(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            ["btrfs", "filesystem", "du", "-s", "--raw", "/.snapshots"],
            returncode=0,
            stdout="     Total   Exclusive  Set shared  Filename\n",
            stderr="",
        )
        ops = BtrfsOps()
        ops.filesystem_du("/.snapshots")
        args = mock_run.call_args[0][0]
        assert args[1:] == ["filesystem", "du", "-s", "--raw", "/.snapshots"]

    @patch("subprocess.run")
    def test_error_returns_empty(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1, cmd=["btrfs"], stderr="no such file"
        )
        assert BtrfsOps().filesystem_du("/nonexistent") == {}

    @patch("subprocess.run")
    def test_no_data_row_returns_empty(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            ["btrfs", "filesystem", "du", "-s", "--raw", "/.snapshots"],
            returncode=0,
            stdout="     Total   Exclusive  Set shared  Filename\n",
            stderr="",
        )
        assert BtrfsOps().filesystem_du("/.snapshots") == {}


# ---------------------------------------------------------------------------
# mount_subvolume / umount tests
# ---------------------------------------------------------------------------


class TestMountSubvolume:
    def _mounts_stdout(self):
        return (
            "TARGET\tSOURCE\tUUID\n"
            "/\t/dev/nvme0n1p3\t3149ac5f-25ee-4805-b0ae-e6679af72699\n"
            "/home\t/dev/mapper/crypt_nvme0n1p6\tefff4471-576f-4fbb-976e-162761395cfa\n"
        )

    @patch("subprocess.run")
    def test_explicit_device(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0, "", "")
        BtrfsOps().mount_subvolume("/.snapshots/s1/root", "/mnt/x", device="/dev/sda2")
        args = mock_run.call_args[0][0]
        assert args[0] == "mount"
        assert "subvol=/.snapshots/s1/root" in args
        assert "ro" in args
        assert args[-2:] == ["/dev/sda2", "/mnt/x"]

    @patch("subprocess.run")
    def test_read_write_option(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0, "", "")
        BtrfsOps().mount_subvolume("/subvol", "/mnt/x", device="/dev/sda2", read_only=False)
        args = mock_run.call_args[0][0]
        assert "rw" in args

    @patch("subprocess.run")
    def test_device_resolved_from_mounts(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            [], 0, self._mounts_stdout(), ""
        )
        ops = BtrfsOps()
        ops.mount_subvolume("/.snapshots/s1/root", "/mnt/x")
        # Two calls: findmnt lookup, then the mount itself
        assert mock_run.call_count == 2
        mount_args = mock_run.call_args_list[1][0][0]
        assert mount_args[-2] == "/dev/nvme0n1p3"  # device of the "/" mount

    @patch("subprocess.run")
    def test_nested_subvolume_uses_longest_prefix(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            [], 0, self._mounts_stdout(), ""
        )
        ops = BtrfsOps()
        ops.mount_subvolume("/home/data/sub", "/mnt/x")
        mount_args = mock_run.call_args_list[1][0][0]
        assert mount_args[-2] == "/dev/mapper/crypt_nvme0n1p6"

    @patch("subprocess.run")
    def test_no_matching_mount_raises(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            [], 0, "TARGET\tSOURCE\tUUID\n", ""
        )
        with pytest.raises(BtrfsError, match="Cannot determine the btrfs device"):
            BtrfsOps().mount_subvolume("/nonexistent", "/mnt/x")

    @patch("subprocess.run")
    def test_mount_failure_raises_btrfs_error(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1, cmd=["mount"], stderr="mount: permission denied",
        )
        with pytest.raises(BtrfsPermissionError):
            BtrfsOps().mount_subvolume("/s", "/mnt/x", device="/dev/sda2")

    @patch("subprocess.run")
    def test_mount_binary_missing(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = FileNotFoundError("mount")
        with pytest.raises(BtrfsError, match="util-linux"):
            BtrfsOps().mount_subvolume("/s", "/mnt/x", device="/dev/sda2")

    def test_dry_run_skips_execution(self) -> None:
        BtrfsOps().mount_subvolume("/s", "/mnt/x", device="/dev/sda2", dry_run=True)


class TestUmount:
    @patch("subprocess.run")
    def test_unmounts(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0, "", "")
        BtrfsOps().umount("/mnt/x")
        args = mock_run.call_args[0][0]
        assert args == ["umount", "/mnt/x"]

    @patch("subprocess.run")
    def test_unmount_failure(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1, cmd=["umount"], stderr="target is busy",
        )
        with pytest.raises(BtrfsError, match="target is busy"):
            BtrfsOps().umount("/mnt/x")

    def test_dry_run_skips_execution(self) -> None:
        BtrfsOps().umount("/mnt/x", dry_run=True)


class TestFilesystemUsageCache:
    """PERF-01: filesystem_usage — the heaviest dashboard subprocess —
    is cached per mount within a TTL."""

    def _usage_ops(self) -> BtrfsOps:
        ops = BtrfsOps()
        ops._usage_cache_ttl = 5.0
        return ops

    @patch("subprocess.run")
    def test_cached_within_ttl(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            ["btrfs", "filesystem", "usage", "-b", "/"],
            returncode=0,
            stdout="Overall:\n    Device size:\t\t      1000\n",
            stderr="",
        )
        ops = self._usage_ops()
        first = ops.filesystem_usage("/")
        second = ops.filesystem_usage("/")
        assert first is second
        mock_run.assert_called_once()

    @patch("subprocess.run")
    def test_ttl_expiry_refetches(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            ["btrfs", "filesystem", "usage", "-b", "/"],
            returncode=0,
            stdout="Overall:\n    Device size:\t\t      1000\n",
            stderr="",
        )
        ops = self._usage_ops()
        with patch("manaradix.core.btrfs_ops.time.monotonic",
                   side_effect=[100.0, 104.0, 106.0]):
            ops.filesystem_usage("/")
            ops.filesystem_usage("/")  # hit (104.0 < 105.0 expiry)
            ops.filesystem_usage("/")  # miss (106.0 >= 105.0)
        assert mock_run.call_count == 2

    @patch("subprocess.run")
    def test_errors_not_cached(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = subprocess.CalledProcessError(1, "btrfs", stderr="boom")
        ops = self._usage_ops()
        assert ops.filesystem_usage("/") == {}
        assert ops.filesystem_usage("/") == {}
        assert mock_run.call_count == 2

    @patch("subprocess.run")
    def test_cache_keyed_per_mount(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            ["btrfs", "filesystem", "usage", "-b", "/mnt/data"],
            returncode=0,
            stdout="Overall:\n    Device size:\t\t      1000\n",
            stderr="",
        )
        ops = self._usage_ops()
        ops.filesystem_usage("/")
        ops.filesystem_usage("/mnt/data")
        ops.filesystem_usage("/")
        assert mock_run.call_count == 2
