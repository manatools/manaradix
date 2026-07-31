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


class TestConfigStoreSaveLoad:
    def test_save_and_load_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "config.toml")

            store = ConfigStore(path=config_path)
            store.default_subvolume = "/"
            store.snapshot_dir = ".my-snaps"
            store.boot_from_snapshot = True
            store.set_policy(
                SnapshotPolicy(
                    subvolume="/",
                    schedule_hourly=True,
                    schedule_daily=True,
                    retention=RetentionPolicy(keep_daily=14, keep_hourly=20),
                )
            )
            store.save()

            # Load into a new store
            store2 = ConfigStore(path=config_path)
            store2.load()

            assert store2.default_subvolume == "/"
            assert store2.snapshot_dir == ".my-snaps"
            assert store2.boot_from_snapshot is True

            policy = store2.get_policy("/")
            assert policy is not None
            assert policy.schedule_hourly is True
            assert policy.schedule_daily is True
            assert policy.retention.keep_daily == 14
            assert policy.retention.keep_hourly == 20

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


class TestConfigStoreProperties:
    def test_setters(self) -> None:
        store = ConfigStore(path="/nonexistent/config.toml")
        store.default_subvolume = "/data"
        store.snapshot_dir = ".snaps"
        store.boot_from_snapshot = True
        assert store.default_subvolume == "/data"
        assert store.snapshot_dir == ".snaps"
        assert store.boot_from_snapshot is True
