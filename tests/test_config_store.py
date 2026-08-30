"""Unit tests for ConfigStore."""

import os
import tempfile
from pathlib import Path

import pytest

from manaradix.core.config_store import (
    ConfigError,
    ConfigStore,
)
from manaradix.model.policy import (
    RetentionPolicy,
    SnapshotPolicy,
)


class TestConfigStoreDefaults:
    def test_default_values(self) -> None:
        store = ConfigStore(path="/nonexistent/config.toml")
        assert store.default_subvolume == "/"
        assert store.snapshot_dir == ".snapshots"
        assert store.boot_from_snapshot is False
        assert store.snapshot_before_upgrade is False

    def test_default_no_policies(self) -> None:
        store = ConfigStore(path="/nonexistent/config.toml")
        assert store.list_policies() == []

    def test_load_missing_file_does_not_raise(self) -> None:
        store = ConfigStore(path="/nonexistent/config.toml")
        store.load()  # should not raise
        assert store.default_subvolume == "/"


class TestConfigStorePolicies:
    def test_set_and_get_policy(self) -> None:
        store = ConfigStore(path="/nonexistent/config.toml")
        policy = SnapshotPolicy(subvolume="/", schedule_daily=True)
        store.set_policy(policy)
        assert store.get_policy("/") is policy

    def test_set_policy_empty_subvolume_raises(self) -> None:
        store = ConfigStore(path="/nonexistent/config.toml")
        with pytest.raises(ConfigError, match="non-empty subvolume"):
            store.set_policy(SnapshotPolicy(subvolume=""))

    def test_list_policies(self) -> None:
        store = ConfigStore(path="/nonexistent/config.toml")
        store.set_policy(SnapshotPolicy(subvolume="/"))
        store.set_policy(SnapshotPolicy(subvolume="/home"))
        assert len(store.list_policies()) == 2

    def test_remove_policy(self) -> None:
        store = ConfigStore(path="/nonexistent/config.toml")
        store.set_policy(SnapshotPolicy(subvolume="/"))
        store.remove_policy("/")
        assert store.get_policy("/") is None

    def test_replace_policy(self) -> None:
        store = ConfigStore(path="/nonexistent/config.toml")
        p1 = SnapshotPolicy(subvolume="/", schedule_hourly=True)
        p2 = SnapshotPolicy(subvolume="/", schedule_daily=True)
        store.set_policy(p1)
        store.set_policy(p2)
        assert store.get_policy("/") is p2
        assert len(store.list_policies()) == 1


class TestToggleSchedule:
    def test_enable_sets_flag_and_reactivates(self) -> None:
        store = ConfigStore(path="/nonexistent/config.toml")
        policy = SnapshotPolicy(subvolume="/", schedule_daily=True)
        policy.enabled = False
        store.set_policy(policy)
        updated = store.toggle_schedule("/", "weekly", True)
        assert updated is policy
        assert policy.schedule_weekly is True
        assert policy.enabled is True

    def test_disable_keeps_other_schedules(self) -> None:
        store = ConfigStore(path="/nonexistent/config.toml")
        policy = SnapshotPolicy(subvolume="/", schedule_daily=True, schedule_weekly=True)
        store.set_policy(policy)
        store.toggle_schedule("/", "weekly", False)
        assert policy.schedule_weekly is False
        assert policy.enabled is True
        assert policy.active_schedules == ["daily"]

    def test_disable_last_schedule_disables_policy(self) -> None:
        store = ConfigStore(path="/nonexistent/config.toml")
        policy = SnapshotPolicy(subvolume="/", schedule_weekly=True, schedule_daily=False)
        store.set_policy(policy)
        store.toggle_schedule("/", "weekly", False)
        assert policy.active_schedules == []
        assert policy.enabled is False

    def test_unknown_subvolume_returns_none(self) -> None:
        store = ConfigStore(path="/nonexistent/config.toml")
        assert store.toggle_schedule("/nope", "daily", True) is None

    def test_global_trigger_is_not_a_policy_schedule(self) -> None:
        store = ConfigStore(path="/nonexistent/config.toml")
        policy = SnapshotPolicy(subvolume="/", schedule_daily=True)
        store.set_policy(policy)
        assert store.toggle_schedule("/", "pre-upgrade", True) is policy
        assert policy.schedule_daily is True  # untouched


class TestConfigStoreSaveLoad:
    def test_save_and_load_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "config.toml")

            store = ConfigStore(path=config_path)
            store.default_subvolume = "/"
            store.snapshot_dir = ".my-snaps"
            store.boot_from_snapshot = True
            store.snapshot_before_upgrade = True
            store.set_policy(
                SnapshotPolicy(
                    subvolume="/",
                    schedule_hourly=True,
                    schedule_daily=True,
                    retention=RetentionPolicy(
                        keep_daily=14, keep_hourly=20, keep_pre_upgrade=2
                    ),
                )
            )
            store.save()

            # Load into a new store
            store2 = ConfigStore(path=config_path)
            store2.load()

            assert store2.default_subvolume == "/"
            assert store2.snapshot_dir == ".my-snaps"
            assert store2.boot_from_snapshot is True
            assert store2.snapshot_before_upgrade is True

            policy = store2.get_policy("/")
            assert policy is not None
            assert policy.schedule_hourly is True
            assert policy.schedule_daily is True
            assert policy.retention.keep_daily == 14
            assert policy.retention.keep_hourly == 20
            assert policy.retention.keep_pre_upgrade == 2

    def test_load_invalid_toml_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "config.toml")
            Path(config_path).write_text("this is not valid toml {{{")

            store = ConfigStore(path=config_path)
            with pytest.raises(ConfigError, match="Failed to read config"):
                store.load()

    def test_save_creates_parent_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "subdir", "config.toml")
            store = ConfigStore(path=config_path)
            store.save()
            assert os.path.isfile(config_path)


class TestConfigStoreToDict:
    def test_to_dict_empty(self) -> None:
        store = ConfigStore(path="/nonexistent/config.toml")
        d = store.to_dict()
        assert d["global"]["default_subvolume"] == "/"
        assert d["global"]["snapshot_before_upgrade"] is False
        assert d["policies"] == {}

    def test_to_dict_with_policies(self) -> None:
        store = ConfigStore(path="/nonexistent/config.toml")
        store.set_policy(
            SnapshotPolicy(
                subvolume="/",
                schedule_daily=True,
                retention=RetentionPolicy(keep_daily=7),
            )
        )
        d = store.to_dict()
        assert "/" in d["policies"]
        assert d["policies"]["/"]["schedule_daily"] is True
        assert d["policies"]["/"]["retention"]["keep_daily"] == 7
        assert d["policies"]["/"]["retention"]["keep_pre_upgrade"] == 3


class TestConfigStoreProperties:
    def test_setters(self) -> None:
        store = ConfigStore(path="/nonexistent/config.toml")
        store.default_subvolume = "/data"
        store.snapshot_dir = ".snaps"
        store.boot_from_snapshot = True
        store.snapshot_before_upgrade = True
        assert store.default_subvolume == "/data"
        assert store.snapshot_dir == ".snaps"
        assert store.boot_from_snapshot is True
        assert store.snapshot_before_upgrade is True


class TestAppliedProfile:
    def test_default_empty(self) -> None:
        store = ConfigStore(path="/nonexistent/config.toml")
        assert store.applied_profile == ""

    def test_setter(self) -> None:
        store = ConfigStore(path="/nonexistent/config.toml")
        store.applied_profile = "server"
        assert store.applied_profile == "server"

    def test_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "config.toml")
            store = ConfigStore(path=config_path)
            store.applied_profile = "kiosk"
            store.save()
            store2 = ConfigStore(path=config_path)
            store2.load()
            assert store2.applied_profile == "kiosk"


class TestTraySettings:
    def test_defaults(self) -> None:
        store = ConfigStore(path="/nonexistent/config.toml")
        tray = store.tray
        assert tray.enabled is True
        assert tray.autostart is True
        assert tray.hide_when_ok is False
        assert tray.quiet_hours == ""
        assert tray.space_warn_pct == 85
        assert tray.space_critical_pct == 95
        assert tray.space_interval_min == 10
        assert tray.device_interval_min == 60
        assert tray.retention_interval_min == 1440
        assert tray.scrub_interval_min == 60

    def test_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "config.toml")
            store = ConfigStore(path=config_path)
            store.tray.enabled = False
            store.tray.quiet_hours = "22:30-07:00"
            store.tray.space_warn_pct = 80
            store.tray.space_critical_pct = 92
            store.tray.space_interval_min = 5
            store.tray.device_interval_min = 30
            store.tray.retention_interval_min = 720
            store.tray.scrub_interval_min = 90
            store.save()

            store2 = ConfigStore(path=config_path)
            store2.load()
            assert store2.tray.enabled is False
            assert store2.tray.quiet_hours == "22:30-07:00"
            assert store2.tray.space_warn_pct == 80
            assert store2.tray.space_critical_pct == 92
            assert store2.tray.space_interval_min == 5
            assert store2.tray.device_interval_min == 30
            assert store2.tray.retention_interval_min == 720
            assert store2.tray.scrub_interval_min == 90

    def test_missing_section_keeps_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "config.toml")
            with open(config_path, "w") as fh:
                fh.write('[global]\ndefault_subvolume = "/"\n')
            store = ConfigStore(path=config_path)
            store.load()
            assert store.tray.enabled is True
            assert store.tray.space_warn_pct == 85

    def test_to_dict_includes_tray(self) -> None:
        store = ConfigStore(path="/nonexistent/config.toml")
        data = store.to_dict()
        assert data["tray"]["space_warn_pct"] == 85
        assert data["global"]["applied_profile"] == ""
