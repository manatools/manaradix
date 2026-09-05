"""Unit tests for the Dashboard page section builders."""

from unittest.mock import MagicMock

from manaradix.ui.pages.dashboard import DashboardPage, _disk_key


def make_page(btrfs_ops, filesystems=None, snapshots=None):
    app = MagicMock()
    app.btrfs_ops = btrfs_ops
    page = DashboardPage(app)
    page._usage_cache = {}
    if filesystems is not None:
        page._filesystems = filesystems
    if snapshots is not None:
        page._snapshots = snapshots
    return page


def fs(**overrides):
    """A filesystem_show() entry with sensible defaults."""
    data = {
        "label": "",
        "uuid": "3149ac5f-25ee-4805-b0ae-e6679af72699",
        "total_devices": 1,
        "fs_bytes_used": 19005067264,
        "devices": [
            {"devid": 1, "size": 0, "used": 0, "path": "/dev/nvme0n1p3", "missing": True},
        ],
    }
    data.update(overrides)
    return data


def usage(**overrides):
    """A filesystem_usage() dict with realistic internal sections."""
    data = {
        "overall": {
            "device_size": 211618365440,
            "free_estimated": 190525214720,
            "data": {"size": 20409483264, "used": 18928889856},
            "metadata": {"size": 1073741824, "used": 382238720},
            "system": {"size": 8388608, "used": 16384},
        },
    }
    data["overall"].update(overrides)
    return data


class TestPageIdentity:
    def test_page_id_and_title(self) -> None:
        page = DashboardPage(MagicMock())
        assert page.page_id == "dashboard"
        assert page.title == "Dashboard"

    def test_build_model_uses_two_columns(self) -> None:
        """Sections lay out in a grid (like Settings) to avoid vertical
        overflow on small screens."""
        ops = MagicMock()
        ops.filesystem_show.return_value = []
        ops.btrfs_mounts.return_value = [
            {"target": "/", "source": "/dev/nvme0n1p3", "uuid": "aaa"},
        ]
        ops.filesystem_usage.return_value = usage()
        ops.snapshot_dir = ".snapshots"
        ops.filesystem_du.return_value = {}
        page = make_page(ops)
        model = page.build_model()
        assert model.columns == 2
        # All sections pin a column; usage sections on 0, the rest on 1.
        for section in model.sections:
            assert "column" in section, section["title"]
        col0 = [s["title"] for s in model.sections if s["column"] == 0]
        col1 = [s["title"] for s in model.sections if s["column"] == 1]
        assert "Physical Disks" in col0
        assert "Internal Filesystem Usage" in col0
        assert "Btrfs Filesystems" in col1
        assert "Snapshot Usage" in col1


class TestDiskKey:
    def test_partition_suffix_stripping(self) -> None:
        assert _disk_key("/dev/nvme0n1p3") == "nvme0n1"
        assert _disk_key("/dev/nvme0n1") == "nvme0n1"
        assert _disk_key("/dev/sda2") == "sda"
        assert _disk_key("/dev/sda") == "sda"
        assert _disk_key("/dev/mmcblk0p1") == "mmcblk0"
        assert _disk_key("/dev/vda1") == "vda"

    def test_non_disk_devices_pass_through(self) -> None:
        assert _disk_key("/dev/mapper/crypt_nvme0n1p6") == "/dev/mapper/crypt_nvme0n1p6"
        assert _disk_key("/dev/loop0") == "/dev/loop0"


class TestDisksSection:
    def test_groups_devices_by_physical_disk(self) -> None:
        ops = MagicMock()
        page = make_page(
            ops,
            filesystems=[
                fs(
                    devices=[
                        {"devid": 1, "size": 32212254720, "used": 10737418240,
                         "path": "/dev/nvme0n1p3", "missing": False},
                        {"devid": 2, "size": 10737418240, "used": 2147483648,
                         "path": "/dev/nvme0n1p4", "missing": False},
                    ],
                ),
                fs(
                    devices=[
                        {"devid": 1, "size": 21474836480, "used": 5368709120,
                         "path": "/dev/sda2", "missing": False},
                    ],
                ),
                fs(
                    devices=[
                        {"devid": 1, "size": 8589934592, "used": 1073741824,
                         "path": "/dev/mapper/crypt_nvme0n1p6", "missing": False},
                    ],
                ),
            ],
        )
        section = page._disks_section()
        assert section["title"] == "Physical Disks"
        rows = section["fields"][0]["rows"]

        assert len(rows) == 3
        # nvme0n1: 30 GiB + 10 GiB total, 10 + 2 GiB used → 30.0%
        assert rows[0]["Disk"] == "nvme0n1"
        assert rows[0]["Total"] == "40.0 GiB"
        assert rows[0]["Used"] == "12.0 GiB"
        assert rows[0]["Free"] == "28.0 GiB"
        assert rows[0]["Used %"] == "30.0%"
        # dm-crypt device is its own group keyed by full path
        assert rows[2]["Disk"] == "crypt_nvme0n1p6"
        assert rows[2]["Used %"] == "12.5%"

    def test_empty_devices_returns_label_section(self) -> None:
        ops = MagicMock()
        page = make_page(ops, filesystems=[])
        section = page._disks_section()
        assert section["fields"][0]["widget"] == "label"
        assert section["fields"][0]["text"] == "No physical disks found."

    def test_dropdown_when_more_than_three(self) -> None:
        ops = MagicMock()
        devices = [
            fs(devices=[{"devid": 1, "size": 100, "used": 10,
                         "path": f"/dev/sd{chr(ord('a') + i)}1", "missing": False}])
            for i in range(4)
        ]
        section = make_page(ops, filesystems=devices)._disks_section()
        fields = section["fields"]
        assert fields[0]["widget"] == "dropdown"
        assert fields[0]["id"] == "disk_selector"
        assert fields[0]["options"] == ["sda", "sdb", "sdc", "sdd"]
        assert len(fields[1]["rows"]) == 1

    def test_no_dropdown_with_three_disks(self) -> None:
        ops = MagicMock()
        devices = [
            fs(devices=[{"devid": 1, "size": 100, "used": 10,
                         "path": f"/dev/sd{chr(ord('a') + i)}1", "missing": False}])
            for i in range(3)
        ]
        section = make_page(ops, filesystems=devices)._disks_section()
        fields = section["fields"]
        assert fields[0]["widget"] == "tree"
        assert len(fields[0]["rows"]) == 3

    def test_selection_persists_across_builds(self) -> None:
        ops = MagicMock()
        devices = [
            fs(devices=[{"devid": 1, "size": 100, "used": 10,
                         "path": f"/dev/sd{chr(ord('a') + i)}1", "missing": False}])
            for i in range(4)
        ]
        page = make_page(ops, filesystems=devices)
        page._selected_disk = "sdc"
        section = page._disks_section()
        assert section["fields"][0]["selected"] == "sdc"
        assert section["fields"][1]["rows"][0]["Disk"] == "sdc"
        page.on_disk_selected("sdd")
        assert page._selected_disk == "sdd"


class TestFilesystemsSection:
    def test_rows_with_mounts(self) -> None:
        ops = MagicMock()
        ops.filesystem_show.return_value = [
            fs(),
            fs(
                label="root",
                uuid="efff4471-576f-4fbb-976e-162761395cfa",
                fs_bytes_used=0,
                devices=[
                    {
                        "devid": 1, "size": 32212254720, "used": 0,
                        "path": "/dev/sda2", "missing": False,
                    },
                ],
            ),
        ]
        # Second fs mounts via uuid only (findmnt reports the dm device,
        # btrfs reports the mapper name).
        ops.btrfs_mounts.return_value = [
            {"target": "/", "source": "/dev/nvme0n1p3", "uuid": "3149ac5f-25ee-4805-b0ae-e6679af72699"},
            {"target": "/home", "source": "/dev/dm-1", "uuid": "efff4471-576f-4fbb-976e-162761395cfa"},
        ]
        page = make_page(ops, filesystems=ops.filesystem_show.return_value)

        section = page._filesystems_section()
        assert section["title"] == "Btrfs Filesystems"
        rows = section["fields"][0]["rows"]

        assert len(rows) == 2
        # device size 0 (unprivileged) → no size data, no percentage
        assert rows[0] == {
            "Label": "-",
            "Devices": "/dev/nvme0n1p3",
            "Total": "-",
            "Used": "17.7 GiB",  # FS-level used is available unprivileged
            "Free": "-",
            "Used %": "-",
            "Mount": "/",
        }
        # uuid fallback: no device-path match, uuid matches
        assert rows[1]["Mount"] == "/home"
        assert rows[1]["Total"] == "30.0 GiB"
        assert rows[1]["Used"] == "-"  # fs_bytes_used 0
        assert rows[1]["Free"] == "30.0 GiB"
        assert rows[1]["Used %"] == "0.0%"

    def test_dropdown_when_more_than_three(self) -> None:
        ops = MagicMock()
        filesystems = [fs(uuid=f"uuid-{i:02d}", label=f"fs{i}") for i in range(4)]
        page = make_page(ops, filesystems=filesystems)
        section = page._filesystems_section()
        fields = section["fields"]
        assert fields[0]["widget"] == "dropdown"
        assert fields[0]["selected"] == "fs0"
        assert len(fields[1]["rows"]) == 1

    def test_selection_persists_across_builds(self) -> None:
        ops = MagicMock()
        filesystems = [fs(uuid=f"uuid-{i:02d}", label=f"fs{i}") for i in range(4)]
        page = make_page(ops, filesystems=filesystems)
        page.on_fs_selected("fs2")
        assert page._selected_fs == "uuid-02"
        section = page._filesystems_section()
        assert section["fields"][0]["selected"] == "fs2"

    def test_empty_returns_label_section(self) -> None:
        ops = MagicMock()
        page = make_page(ops, filesystems=[])
        section = page._filesystems_section()
        assert section["fields"][0]["widget"] == "label"
        assert section["fields"][0]["text"] == "No btrfs filesystems found."

    def test_filesystem_show_error_yields_label_section(self) -> None:
        ops = MagicMock()
        ops.filesystem_show.side_effect = RuntimeError("boom")
        page = make_page(ops)  # _filesystems defaults to [] via build_model
        page._filesystems = page._list_filesystems()
        section = page._filesystems_section()
        assert section["fields"][0]["text"] == "No btrfs filesystems found."

    def test_mounts_error_still_builds_rows(self) -> None:
        ops = MagicMock()
        ops.btrfs_mounts.side_effect = RuntimeError("findmnt broken")
        page = make_page(ops, filesystems=[fs()])
        rows = page._filesystems_section()["fields"][0]["rows"]
        assert rows[0]["Mount"] == "-"


class TestInternalStatsSection:
    def _page_with_mounts(self, ops, mounts):
        ops.btrfs_mounts.return_value = mounts
        return make_page(ops)

    def test_rows_per_mount(self) -> None:
        ops = MagicMock()
        ops.filesystem_usage.side_effect = [
            usage(),  # / mount
            usage(device_size=10737418240, data={"size": 1000000000, "used": 750000000}),
        ]
        page = self._page_with_mounts(
            ops,
            [
                {"target": "/", "source": "/dev/nvme0n1p3", "uuid": "aaa"},
                {"target": "/home", "source": "/dev/mapper/crypt", "uuid": "bbb"},
            ],
        )
        section = page._internal_stats_section()
        rows = section["fields"][0]["rows"]

        assert len(rows) == 6  # Data/Metadata/System × 2 mounts
        assert rows[0] == {
            "Filesystem": "/",
            "Section": "Data",
            "Size": "19.0 GiB",
            "Used": "17.6 GiB",
            "Used %": "92.7%",
        }
        home_data = [r for r in rows if r["Filesystem"] == "/home" and r["Section"] == "Data"][0]
        assert home_data["Used %"] == "75.0%"

    def test_dedupes_mounts_by_uuid(self) -> None:
        ops = MagicMock()
        ops.filesystem_usage.return_value = usage()
        page = self._page_with_mounts(
            ops,
            [
                {"target": "/", "source": "/dev/nvme0n1p3", "uuid": "aaa"},
                {"target": "/var", "source": "/dev/nvme0n1p3", "uuid": "aaa"},
            ],
        )
        section = page._internal_stats_section()
        rows = section["fields"][0]["rows"]
        # one group only, using the shortest target (/)
        assert len(rows) == 3
        assert all(r["Filesystem"] == "/" for r in rows)

    def test_mounts_error_falls_back_to_root(self) -> None:
        ops = MagicMock()
        ops.btrfs_mounts.side_effect = RuntimeError("findmnt broken")
        ops.filesystem_usage.return_value = usage()
        section = make_page(ops)._internal_stats_section()
        rows = section["fields"][0]["rows"]
        assert len(rows) == 3
        assert all(r["Filesystem"] == "/" for r in rows)

    def test_no_device_size_returns_none(self) -> None:
        ops = MagicMock()
        ops.filesystem_usage.return_value = usage(device_size=0)
        page = self._page_with_mounts(
            ops, [{"target": "/", "source": "/dev/nvme0n1p3", "uuid": "aaa"}],
        )
        assert page._internal_stats_section() is None


class TestSnapshotUsageSection:
    def test_row_with_sizes(self) -> None:
        ops = MagicMock()
        ops.snapshot_dir = ".snapshots"
        ops.filesystem_du.return_value = {"total": 6442450944, "exclusive": 2147483648}
        ops.filesystem_usage.return_value = usage()
        page = make_page(
            ops, snapshots=[MagicMock(), MagicMock(), MagicMock()],
        )
        page.on_measure_snapshot_usage()
        section = page._snapshot_usage_section()
        row = section["fields"][0]["rows"][0]
        assert row == {
            "Snapshots": 3,
            "Total": "6.0 GiB",
            "Exclusive": "2.0 GiB",
            "% of device": "1.0%",
        }

    def test_no_snapshots_and_du_error(self) -> None:
        ops = MagicMock()
        ops.snapshot_dir = ".snapshots"
        ops.filesystem_du.return_value = {}
        ops.filesystem_usage.return_value = usage()
        page = make_page(ops, snapshots=[])
        page.on_measure_snapshot_usage()
        row = page._snapshot_usage_section()["fields"][0]["rows"][0]
        assert row == {
            "Snapshots": 0,
            "Total": "-",
            "Exclusive": "-",
            "% of device": "-",
        }

    def test_not_measured_until_button_pressed(self) -> None:
        """The slow ``btrfs filesystem du`` never runs on page build."""
        ops = MagicMock()
        ops.snapshot_dir = ".snapshots"
        ops.filesystem_usage.return_value = usage()
        page = make_page(ops, snapshots=[MagicMock()])
        section = page._snapshot_usage_section()
        ops.filesystem_du.assert_not_called()
        assert section["fields"][0]["rows"][0] == {
            "Snapshots": 1,
            "Total": "-",
            "Exclusive": "-",
            "% of device": "-",
        }
        assert section["fields"][1]["id"] == "btn_measure_snapshot_usage"

    def test_measure_error_is_swallowed(self) -> None:
        ops = MagicMock()
        ops.snapshot_dir = ".snapshots"
        ops.filesystem_du.side_effect = RuntimeError("timed out")
        ops.filesystem_usage.return_value = usage()
        page = make_page(ops, snapshots=[])
        page.on_measure_snapshot_usage()
        assert page._snapshot_du == {}

    def test_absolute_snapshot_dir(self) -> None:
        ops = MagicMock()
        ops.snapshot_dir = "/mnt/data/.my-snaps"
        ops.filesystem_du.return_value = {}
        ops.filesystem_usage.return_value = usage()
        page = make_page(ops, snapshots=[])
        page.on_measure_snapshot_usage()
        ops.filesystem_du.assert_called_once_with("/mnt/data/.my-snaps")
