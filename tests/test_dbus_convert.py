"""Unit tests for the D-Bus conversion layer (manaradix.dbus.convert)."""

from datetime import datetime

import dbus

from manaradix.dbus import convert
from manaradix.model.policy import RetentionPolicy, SnapshotPolicy
from manaradix.model.snapshot import Snapshot, SnapshotType
from manaradix.model.subvolume import Subvolume


def _snapshot(**overrides):
    return Snapshot(
        id=overrides.get("id", "2025-01-15_14-00-00"),
        subvolume=overrides.get("subvolume", "/"),
        path=overrides.get("path", "/mnt/.snapshots/2025-01-15_14-00-00/root"),
        date=overrides.get("date", datetime(2025, 1, 15, 14, 0, 0)),
        type=overrides.get("type", SnapshotType.MANUAL),
        description=overrides.get("description", "pre-upgrade"),
        schedule=overrides.get("schedule", ""),
    )


class TestSnapshotToDbus:
    def test_full_serialization(self) -> None:
        data = convert.snapshot_to_dbus(_snapshot())
        assert data["id"] == "2025-01-15_14-00-00"
        assert data["subvolume"] == "/"
        assert data["date"] == "2025-01-15T14:00:00"
        assert data["type"] == "manual"
        assert data["description"] == "pre-upgrade"
        assert data["is_read_only"] is False

    def test_read_only_types_flagged(self) -> None:
        for snap_type in (SnapshotType.READ_ONLY, SnapshotType.TIMED,
                          SnapshotType.BOOT):
            data = convert.snapshot_to_dbus(_snapshot(type=snap_type))
            assert data["is_read_only"] is True

    def test_iso_roundtrip(self) -> None:
        snap = _snapshot()
        assert convert.iso_to_datetime(
            convert.snapshot_to_dbus(snap)["date"]
        ) == snap.date


class TestSubvolumeToDbus:
    def test_serialization(self) -> None:
        data = convert.subvolume_to_dbus(Subvolume(
            id=257, path="/home", parent_id=5,
            uuid="01234567-89ab-cdef-0123-456789abcdef",
            parent_uuid="00000000-0000-0000-0000-000000000005",
        ))
        assert data["id"] == 257
        assert data["path"] == "/home"
        assert data["parent_id"] == 5
        assert data["uuid"].startswith("01234567")
        assert data["is_top_level"] is False

    def test_top_level(self) -> None:
        data = convert.subvolume_to_dbus(Subvolume(id=5, path="/"))
        assert data["is_top_level"] is True


class TestStatusToDbus:
    def test_balance_status(self) -> None:
        data = convert.balance_status_to_dbus(
            type("BS", (), {"total": 10, "relocated": 4, "running": True})()
        )
        assert data == {"total": 10, "relocated": 4, "running": True}

    def test_scrub_status(self) -> None:
        data = convert.scrub_status_to_dbus(
            type("SS", (), {"data_extents_scrubbed": 99, "errors": 0,
                            "running": False})()
        )
        assert data == {"data_extents_scrubbed": 99, "errors": 0,
                        "running": False}
        assert "last_started" not in data  # None is not encodable in a{sv}

    def test_scrub_status_with_last_started(self) -> None:
        data = convert.scrub_status_to_dbus(
            type("SS", (), {"data_extents_scrubbed": 1, "errors": 0,
                            "running": False,
                            "last_started": "2026-08-07T09:00:00"})()
        )
        assert data["last_started"] == "2026-08-07T09:00:00"


class TestPolicyToDbus:
    def test_serialization(self) -> None:
        policy = SnapshotPolicy(
            subvolume="/home", enabled=False,
            schedule_hourly=True, schedule_daily=False,
            schedule_weekly=True, schedule_monthly=False, schedule_boot=True,
            retention=RetentionPolicy(
                keep_hourly=2, keep_daily=3, keep_weekly=4,
                keep_monthly=5, keep_boot=6,
            ),
        )
        data = convert.policy_to_dbus(policy)
        assert data["subvolume"] == "/home"
        assert data["enabled"] is False
        assert data["schedule_hourly"] is True
        assert data["schedule_boot"] is True
        assert data["retention"] == {
            "keep_hourly": 2, "keep_daily": 3, "keep_weekly": 4,
            "keep_monthly": 5, "keep_boot": 6, "keep_pre_upgrade": 3,
        }

    def test_roundtrip(self) -> None:
        policy = SnapshotPolicy(subvolume="/", schedule_daily=True,
                                schedule_weekly=True)
        restored = convert.policy_to_dbus_roundtrip(policy)
        assert restored == policy


class TestPolicyFromArgs:
    def test_builds_model_with_defaults(self) -> None:
        policy = convert.policy_from_args(subvolume="/")
        assert policy.subvolume == "/"
        assert policy.enabled is True
        assert policy.schedule_daily is True
        assert policy.schedule_hourly is False
        assert policy.retention == RetentionPolicy()

    def test_dbustype_coercion(self) -> None:
        policy = convert.policy_from_args(
            subvolume=dbus.String("/srv"),
            enabled=dbus.Boolean(True),
            schedule_hourly=dbus.Boolean(True),
            schedule_daily=dbus.Boolean(False),
            keep_hourly=dbus.Int32(12),
        )
        assert policy.subvolume == "/srv"
        assert policy.enabled is True
        assert policy.schedule_hourly is True
        assert policy.schedule_daily is False
        assert policy.retention.keep_hourly == 12
        assert policy.retention.keep_daily == 7  # default

    def test_unknown_keys_ignored(self) -> None:
        policy = convert.policy_from_args(subvolume="/", extra=True)
        assert policy.subvolume == "/"


class TestProfileAndSetupStatus:
    def test_profile_to_dbus(self) -> None:
        profile = type("P", (), {"id": "desktop", "name": "Desktop",
                                 "description": "ws", "role": "desktop"})()
        data = convert.profile_to_dbus(profile)
        assert data == {"id": "desktop", "name": "Desktop",
                        "description": "ws", "role": "desktop"}

    def test_setup_status_to_dbus(self) -> None:
        data = convert.setup_status_to_dbus(
            needs_setup=True, role="desktop", default_profile={"id": "desktop"},
            root_is_btrfs=True, grub_present=True,
        )
        assert data["needs_setup"] is True
        assert data["role"] == "desktop"
        assert data["default_profile"] == {"id": "desktop"}
        assert data["root_is_btrfs"] is True
        assert data["grub_present"] is True
