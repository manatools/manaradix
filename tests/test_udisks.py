"""Tests for the UDisks2 SMART probe (manaradix.dbus.udisks)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from manaradix.dbus import udisks

_DRIVE = "org.freedesktop.UDisks2.Drive"
_MANAGER = "org.freedesktop.DBus.ObjectManager"


def _make_bus(objects: dict, attrs: dict | None = None) -> MagicMock:
    """A fake system bus answering GetManagedObjects with *objects*.

    *attrs* maps object path -> SmartGetAttributes return value (an
    empty tuple by default); the dbus.Interface wrapper is replaced by
    the drive object itself.
    """
    bus = MagicMock()
    manager = MagicMock()
    manager.GetManagedObjects.return_value = objects
    attrs = attrs or {}

    def get_object(name: str, path: str) -> object:
        if path == "/org/freedesktop/UDisks2":
            return manager
        drive = MagicMock()
        drive.SmartGetAttributes.return_value = attrs.get(path, [])
        return drive

    bus.get_object.side_effect = get_object
    return bus


def _drive_object(path: str, device_file: str, **properties: object) -> dict:
    props = {"DeviceFile": device_file}
    props.update(properties)
    return {path: {_DRIVE: props}}


class TestWholeDisk:
    def test_partition_stripped(self) -> None:
        assert udisks.whole_disk("/dev/sda2") == "/dev/sda"

    def test_nvme_partition_stripped(self) -> None:
        assert udisks.whole_disk("/dev/nvme0n1p3") == "/dev/nvme0n1"

    def test_whole_disk_unchanged(self) -> None:
        assert udisks.whole_disk("/dev/sda") == "/dev/sda"
        assert udisks.whole_disk("/dev/mapper/vg-root") == "/dev/mapper/vg-root"


class TestSmartProbeAll:
    def test_returns_report_for_requested_whole_disk(self) -> None:
        path = "/org/freedesktop/UDisks2/drives/drive_sda"
        bus = _make_bus(
            _drive_object(
                path, "/dev/sda",
                SmartUpdated=123456000,
                SmartFailing=False,
                SmartTemperature=38.0,
            ),
            attrs={path: [(5, 0, 100), (197, 0, 100), (198, 0, 100), (9, 0, 80)]},
        )
        with patch("dbus.Interface",
                   side_effect=lambda obj, iface: obj):
            reports = udisks.smart_probe_all(["/dev/sda2"], bus=bus)
        assert reports == {
            "/dev/sda": {
                "failing": False,
                "temperature": 38.0,
                "reallocated_sectors": 100,
                "pending_sectors": 100,
                "uncorrectable_errors": 100,
            }
        }

    def test_failing_drive(self) -> None:
        path = "/org/freedesktop/UDisks2/drives/drive_sdb"
        bus = _make_bus(
            _drive_object(
                path, "/dev/sdb",
                SmartUpdated=123456000,
                SmartFailing=True,
                SmartTemperature=52.0,
            )
        )
        with patch("dbus.Interface",
                   side_effect=lambda obj, iface: obj):
            reports = udisks.smart_probe_all(["/dev/sdb"], bus=bus)
        assert reports["/dev/sdb"]["failing"] is True
        assert reports["/dev/sdb"]["temperature"] == 52.0

    def test_drive_without_smart_skipped(self) -> None:
        # SmartUpdated == 0 → the drive never returned SMART data.
        bus = _make_bus(
            _drive_object(
                "/org/freedesktop/UDisks2/drives/drive_sdc",
                "/dev/sdc",
                SmartUpdated=0,
                SmartFailing=False,
                SmartTemperature=0.0,
            )
        )
        assert udisks.smart_probe_all(["/dev/sdc"], bus=bus) == {}

    def test_unrelated_drive_ignored(self) -> None:
        bus = _make_bus(
            _drive_object(
                "/org/freedesktop/UDisks2/drives/drive_sdd",
                "/dev/sdd",
                SmartUpdated=1,
                SmartFailing=False,
                SmartTemperature=30.0,
            )
        )
        assert udisks.smart_probe_all(["/dev/sda"], bus=bus) == {}

    def test_no_udisks_service(self) -> None:
        bus = MagicMock()
        bus.get_object.side_effect = Exception("no bus daemon")
        assert udisks.smart_probe_all(["/dev/sda"], bus=bus) == {}

    def test_empty_device_list(self) -> None:
        assert udisks.smart_probe_all([], bus=MagicMock()) == {}
