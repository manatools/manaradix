"""Unit tests for data models — snapshot, subvolume, policy."""

from datetime import datetime

import pytest

from manaradix.model.policy import (
    RetentionPolicy,
    SnapshotPolicy,
)
from manaradix.model.snapshot import Snapshot, SnapshotType
from manaradix.model.subvolume import Subvolume


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


class TestSnapshotType:
    def test_enum_values(self) -> None:
        assert SnapshotType.MANUAL.value == "manual"
        assert SnapshotType.READ_ONLY.value == "readonly"
        assert SnapshotType.TIMED.value == "timed"
        assert SnapshotType.BOOT.value == "boot"
        assert SnapshotType.PRE_RESTORE.value == "pre-restore"
        assert SnapshotType.PRE_INSTALL.value == "pre-install"
        assert SnapshotType.PRE_UPGRADE.value == "pre-upgrade"

    def test_enum_from_value(self) -> None:
        assert SnapshotType("manual") == SnapshotType.MANUAL
        assert SnapshotType("readonly") == SnapshotType.READ_ONLY


class TestSnapshot:
    def _make_snapshot(self, **kwargs) -> Snapshot:
        defaults = {
            "id": "2025-01-15_14-00-00",
            "subvolume": "/",
            "path": "/.snapshots/2025-01-15_14-00-00/root",
            "date": datetime(2025, 1, 15, 14, 0, 0),
            "type": SnapshotType.TIMED,
            "description": "auto hourly snapshot",
        }
        defaults.update(kwargs)
        return Snapshot(**defaults)

    def test_is_read_only_timed(self) -> None:
        snap = self._make_snapshot(type=SnapshotType.TIMED)
        assert snap.is_read_only is True

    def test_is_read_only_manual(self) -> None:
        snap = self._make_snapshot(type=SnapshotType.MANUAL)
        assert snap.is_read_only is False

    def test_is_read_only_readonly(self) -> None:
        snap = self._make_snapshot(type=SnapshotType.READ_ONLY)
        assert snap.is_read_only is True

    def test_is_read_only_boot(self) -> None:
        snap = self._make_snapshot(type=SnapshotType.BOOT)
        assert snap.is_read_only is True

    def test_is_read_only_pre_upgrade(self) -> None:
        snap = self._make_snapshot(type=SnapshotType.PRE_UPGRADE)
        assert snap.is_read_only is True

    def test_is_read_only_pre_restore(self) -> None:
        snap = self._make_snapshot(type=SnapshotType.PRE_RESTORE)
        assert snap.is_read_only is False

    def test_snapshot_subvol_path(self) -> None:
        snap = self._make_snapshot(
            path="/.snapshots/2025-01-15_14-00-00/root"
        )
        assert snap.snapshot_subvol_path == "root"

    def test_snapshot_subvol_path_home(self) -> None:
        snap = self._make_snapshot(
            subvolume="/home",
            path="/.snapshots/2025-01-15_14-00-00/home",
        )
        assert snap.snapshot_subvol_path == "home"

    def test_default_description(self) -> None:
        snap = Snapshot(
            id="x",
            subvolume="/",
            path="/x",
            date=datetime.now(),
            type=SnapshotType.MANUAL,
        )
        assert snap.description == ""

    def test_default_schedule(self) -> None:
        snap = Snapshot(
            id="x",
            subvolume="/",
            path="/x",
            date=datetime.now(),
            type=SnapshotType.MANUAL,
        )
        assert snap.schedule == ""

    def test_schedule_explicit(self) -> None:
        snap = Snapshot(
            id="x",
            subvolume="/",
            path="/x",
            date=datetime.now(),
            type=SnapshotType.TIMED,
            schedule="daily",
        )
        assert snap.schedule == "daily"


# ---------------------------------------------------------------------------
# Subvolume
# ---------------------------------------------------------------------------


class TestSubvolume:
    def test_is_top_level(self) -> None:
        sv = Subvolume(id=5, path="/", parent_id=0)
        assert sv.is_top_level is True

    def test_is_not_top_level(self) -> None:
        sv = Subvolume(id=257, path="/home", parent_id=5)
        assert sv.is_top_level is False

    def test_defaults(self) -> None:
        sv = Subvolume(id=1, path="/")
        assert sv.parent_id == 0
        assert sv.uuid == ""
        assert sv.parent_uuid == ""


# ---------------------------------------------------------------------------
# RetentionPolicy
# ---------------------------------------------------------------------------


class TestRetentionPolicy:
    def test_defaults(self) -> None:
        rp = RetentionPolicy()
        assert rp.keep_hourly == 10
        assert rp.keep_daily == 7
        assert rp.keep_weekly == 4
        assert rp.keep_monthly == 3
        assert rp.keep_boot == 5
        assert rp.keep_pre_upgrade == 3

    def test_validate_all_valid(self) -> None:
        rp = RetentionPolicy()
        assert rp.validate() == []

    def test_validate_negative(self) -> None:
        rp = RetentionPolicy(keep_daily=-1)
        errors = rp.validate()
        assert len(errors) == 1
        assert "keep_daily" in errors[0]

    def test_validate_non_integer(self) -> None:
        rp = RetentionPolicy(keep_hourly="ten")  # type: ignore[arg-type]
        errors = rp.validate()
        assert len(errors) == 1
        assert "keep_hourly" in errors[0]

    def test_custom_values(self) -> None:
        rp = RetentionPolicy(
            keep_hourly=5,
            keep_daily=3,
            keep_weekly=2,
            keep_monthly=1,
            keep_boot=0,
            keep_pre_upgrade=2,
        )
        assert rp.keep_hourly == 5
        assert rp.keep_boot == 0
        assert rp.keep_pre_upgrade == 2
        assert rp.validate() == []

    def test_validate_negative_pre_upgrade(self) -> None:
        rp = RetentionPolicy(keep_pre_upgrade=-1)
        errors = rp.validate()
        assert len(errors) == 1
        assert "keep_pre_upgrade" in errors[0]


# ---------------------------------------------------------------------------
# SnapshotPolicy
# ---------------------------------------------------------------------------


class TestSnapshotPolicy:
    def test_defaults(self) -> None:
        sp = SnapshotPolicy(subvolume="/")
        assert sp.subvolume == "/"
        assert sp.enabled is True
        assert sp.schedule_daily is True
        assert sp.schedule_hourly is False
        assert sp.schedule_weekly is False
        assert sp.schedule_monthly is False
        assert sp.schedule_boot is False

    def test_active_schedules_daily_only(self) -> None:
        sp = SnapshotPolicy(subvolume="/", schedule_daily=True)
        assert sp.active_schedules == ["daily"]

    def test_active_schedules_all(self) -> None:
        sp = SnapshotPolicy(
            subvolume="/",
            schedule_hourly=True,
            schedule_daily=True,
            schedule_weekly=True,
            schedule_monthly=True,
            schedule_boot=True,
        )
        assert sp.active_schedules == [
            "hourly",
            "daily",
            "weekly",
            "monthly",
            "boot",
        ]

    def test_active_schedules_none(self) -> None:
        sp = SnapshotPolicy(
            subvolume="/",
            schedule_daily=False,
        )
        assert sp.active_schedules == []

    def test_validate_empty_subvolume(self) -> None:
        sp = SnapshotPolicy(subvolume="")
        errors = sp.validate()
        assert any("subvolume" in e for e in errors)

    def test_validate_no_schedules(self) -> None:
        sp = SnapshotPolicy(subvolume="/", schedule_daily=False)
        errors = sp.validate()
        assert any("schedule" in e for e in errors)

    def test_validate_valid(self) -> None:
        sp = SnapshotPolicy(subvolume="/")
        assert sp.validate() == []

    def test_custom_retention(self) -> None:
        rp = RetentionPolicy(keep_daily=14)
        sp = SnapshotPolicy(subvolume="/home", retention=rp)
        assert sp.retention.keep_daily == 14
