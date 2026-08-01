"""Unit tests for the Overview page section builders."""

from unittest.mock import MagicMock

from manaradix.ui.pages.overview import OverviewPage


def make_page(btrfs_ops):
    app = MagicMock()
    app.btrfs_ops = btrfs_ops
    return OverviewPage(app)


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

        section = make_page(ops)._filesystems_section()
        assert section["title"] == "Btrfs Filesystems"
        rows = section["fields"][0]["rows"]

        assert len(rows) == 2
        assert rows[0] == {
            "Label": "-",
            "UUID": "3149ac5f",
            "Devices": "/dev/nvme0n1p3",
            "Total": "-",  # device size 0 (unprivileged) → no data
            "Used": "17.7 GiB",  # FS-level used is available unprivileged
            "Mount": "/",
        }
        # uuid fallback: no device-path match, uuid matches
        assert rows[1]["Mount"] == "/home"
        assert rows[1]["Total"] == "30.0 GiB"  # device size sum

    def test_empty_returns_label_section(self) -> None:
        ops = MagicMock()
        ops.filesystem_show.return_value = []
        section = make_page(ops)._filesystems_section()
        assert section["fields"][0]["widget"] == "label"
        assert section["fields"][0]["text"] == "No btrfs filesystems found."

    def test_error_returns_none(self) -> None:
        ops = MagicMock()
        ops.filesystem_show.side_effect = RuntimeError("boom")
        assert make_page(ops)._filesystems_section() is None

    def test_mounts_error_still_builds_rows(self) -> None:
        ops = MagicMock()
        ops.filesystem_show.return_value = [fs()]
        ops.btrfs_mounts.side_effect = RuntimeError("findmnt broken")
        rows = make_page(ops)._filesystems_section()["fields"][0]["rows"]
        assert rows[0]["Mount"] == "-"
