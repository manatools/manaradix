"""Unit tests for the D-Bus service handler (manaradix.dbus.service).

The handler is pure logic with injectable auth/pid seams, so the tests
drive ``dispatch()`` directly with fake services — no bus, no polkit,
no root.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch
from xml.etree import ElementTree

import pytest

from manaradix.core.btrfs_ops import (
    BtrfsError,
    SnapshotNotFoundError,
    SubvolumeNotFoundError,
)
from manaradix.core.config_store import ConfigError
from manaradix.core.grub_integration import GrubIntegrationError
from manaradix.core.policy_engine import PolicyEngineError
from manaradix.dbus.constants import (
    ACTION_MANAGE_FILESYSTEM,
    ACTION_MANAGE_POLICIES,
    ACTION_MANAGE_SNAPSHOTS,
    ACTION_READ,
    METHOD_ACTIONS,
)
from manaradix.dbus.errors import (
    AuthorizationError,
    InvalidArgumentError,
    NotFoundError,
    OperationError,
)
from manaradix.dbus.service import ManaradixHandler
from manaradix.model.snapshot import Snapshot, SnapshotType

_POLKIT_POLICY = "data/polkit/org.mageia.manaradix.policy"


def make_handler(**overrides):
    """Handler with a fake AppContext and scripted auth/pid seams."""
    ctx = MagicMock()
    auth_calls = []

    def auth_check(pid, action):
        auth_calls.append((pid, action))
        raise_on = overrides.get("raise_on_auth")
        if raise_on:
            raise raise_on

    handler = ManaradixHandler(
        ctx,
        auth_check=auth_check,
        pid_resolver=lambda connection, sender: overrides.get("pid", 1234),
    )
    return handler, ctx, auth_calls


def _snapshot():
    return Snapshot(
        id="2025-01-15_14-00-00",
        subvolume="/",
        path="/mnt/.snapshots/2025-01-15_14-00-00/root",
        date=datetime(2025, 1, 15, 14, 0, 0),
        type=SnapshotType.MANUAL,
        description="pre-upgrade",
    )


def _action_ids_from_policy() -> set[str]:
    root = ElementTree.parse(_POLKIT_POLICY).getroot()
    return {action.get("id") for action in root.findall("action")}


class TestMethodActionsTable:
    """The METHOD_ACTIONS table is complete and consistent with the code."""

    def test_every_method_has_handler(self) -> None:
        handler = ManaradixHandler(MagicMock())
        for method in METHOD_ACTIONS:
            assert hasattr(handler, f"handle_{method}"), method

    def test_actions_match_polkit_policy(self) -> None:
        assert set(METHOD_ACTIONS.values()) == _action_ids_from_policy()

    def test_all_four_tiers_used(self) -> None:
        assert ACTION_READ in METHOD_ACTIONS.values()
        assert ACTION_MANAGE_SNAPSHOTS in METHOD_ACTIONS.values()
        assert ACTION_MANAGE_POLICIES in METHOD_ACTIONS.values()
        assert ACTION_MANAGE_FILESYSTEM in METHOD_ACTIONS.values()

    def test_ping_maps_to_read(self) -> None:
        assert METHOD_ACTIONS["Ping"] == ACTION_READ


class TestDispatch:
    def test_authorizes_caller_pid_with_method_action(self) -> None:
        handler, _, auth_calls = make_handler(pid=9876)
        handler._ctx.snapshot_manager.list_all.return_value = []
        handler.dispatch("ListSnapshots", {"mount": "/"}, ":1.5")
        assert auth_calls == [(9876, ACTION_READ)]

    def test_snapshot_tier_uses_manage_action(self) -> None:
        handler, ctx, auth_calls = make_handler()
        ctx.snapshot_manager.create.return_value = _snapshot()
        handler.dispatch("CreateSnapshot", {}, ":1.5")
        assert auth_calls == [(1234, ACTION_MANAGE_SNAPSHOTS)]

    def test_denied_auth_raises(self) -> None:
        handler, _, _ = make_handler(
            raise_on_auth=AuthorizationError("no", action_id=ACTION_READ)
        )
        with pytest.raises(AuthorizationError):
            handler.dispatch("Ping", {}, ":1.5")

    def test_unknown_method_raises(self) -> None:
        handler, _, _ = make_handler()
        with pytest.raises(InvalidArgumentError):
            handler.dispatch("Nope", {}, ":1.5")

    def test_exception_mapping(self) -> None:
        cases = [
            (SnapshotNotFoundError("x"), NotFoundError),
            (SubvolumeNotFoundError("x"), NotFoundError),
            (ValueError("bad"), InvalidArgumentError),
            (TypeError("bad"), InvalidArgumentError),
            (BtrfsError("x"), OperationError),
            (ConfigError("x"), OperationError),
            (PolicyEngineError("x"), OperationError),
            (GrubIntegrationError("x"), OperationError),
            (OSError("x"), OperationError),
        ]
        for exc_type, expected in cases:
            handler, ctx, _ = make_handler()
            ctx.btrfs_ops.list_subvolumes.side_effect = exc_type
            with pytest.raises(expected):
                handler.dispatch("ListSubvolumes", {}, ":1.5")


class TestSnapshotDelegation:
    def test_create_forwards_args(self) -> None:
        handler, ctx, _ = make_handler()
        ctx.snapshot_manager.create.return_value = _snapshot()
        result = handler.dispatch(
            "CreateSnapshot",
            {
                "subvolume": "/home", "description": "d",
                "read_only": True, "snapshot_type": "timed",
                "schedule": "daily", "mount": "/", "dry_run": True,
            },
            ":1.5",
        )
        ctx.snapshot_manager.create.assert_called_once_with(
            "/home", description="d", read_only=True,
            snapshot_type=SnapshotType.TIMED, schedule="daily",
            mount="/", dry_run=True,
        )
        assert result["id"] == "2025-01-15_14-00-00"

    def test_create_invalid_type_raises(self) -> None:
        handler, _, _ = make_handler()
        with pytest.raises(InvalidArgumentError):
            handler.dispatch(
                "CreateSnapshot",
                {"snapshot_type": "hourly-everything"}, ":1.5",
            )

    def test_get_missing_snapshot_raises_not_found(self) -> None:
        handler, ctx, _ = make_handler()
        ctx.snapshot_manager.get.return_value = None
        with pytest.raises(NotFoundError):
            handler.dispatch("GetSnapshot", {"id": "nope"}, ":1.5")

    def test_delete_resolves_then_deletes(self) -> None:
        handler, ctx, _ = make_handler()
        ctx.snapshot_manager.get.return_value = _snapshot()
        result = handler.dispatch(
            "DeleteSnapshot", {"id": "2025-01-15_14-00-00", "dry_run": True},
            ":1.5",
        )
        assert result is True
        ctx.snapshot_manager.delete.assert_called_once_with(
            ctx.snapshot_manager.get.return_value, dry_run=True
        )

    def test_prune_forwards(self) -> None:
        handler, ctx, _ = make_handler()
        ctx.snapshot_manager.prune.return_value = ["s1"]
        result = handler.dispatch(
            "PruneSnapshots",
            {"subvolume": "/", "schedule": "daily", "keep_count": 7,
             "mount": "/", "dry_run": False},
            ":1.5",
        )
        assert result == ["s1"]
        ctx.snapshot_manager.prune.assert_called_once_with(
            "/", "daily", 7, mount="/", dry_run=False
        )

    def test_restore_export_import_browse_unbrowse(self) -> None:
        handler, ctx, _ = make_handler()
        ctx.snapshot_manager.get.return_value = _snapshot()
        ctx.snapshot_manager.restore.return_value = _snapshot()

        handler.dispatch("RestoreSnapshot",
                         {"id": "x", "mount": "/", "dry_run": True}, ":1.5")
        assert ctx.snapshot_manager.restore.called

        handler.dispatch("ExportSnapshot",
                         {"id": "x", "output_file": "/tmp/out",
                          "dry_run": True}, ":1.5")
        ctx.snapshot_manager.export_snapshot.assert_called_once_with(
            ctx.snapshot_manager.get.return_value, "/tmp/out", dry_run=True
        )

        handler.dispatch("ImportSnapshot",
                         {"input_file": "/tmp/in", "dest_path": "/mnt",
                          "dry_run": True}, ":1.5")
        ctx.snapshot_manager.import_snapshot.assert_called_once_with(
            "/tmp/in", "/mnt", dry_run=True
        )

        handler.dispatch("BrowseSnapshot",
                         {"id": "x", "mount_point": "/mnt/x",
                          "dry_run": True}, ":1.5")
        ctx.snapshot_manager.browse.assert_called_once_with(
            ctx.snapshot_manager.get.return_value, "/mnt/x", dry_run=True
        )

        handler.dispatch("Unbrowse", {"mount_point": "/mnt/x",
                                      "dry_run": True}, ":1.5")
        ctx.snapshot_manager.unbrowse.assert_called_once_with(
            "/mnt/x", dry_run=True
        )


class TestPolicyDelegation:
    def test_apply_policy_writes_config(self) -> None:
        handler, ctx, _ = make_handler()
        ctx.policy_engine.apply_policy.return_value = ["/usr/lib/..."]
        result = handler.dispatch(
            "ApplyPolicy",
            {
                "subvolume": "/", "enabled": True, "schedule_daily": True,
                "schedule_boot": True, "keep_daily": 14,
                "dry_run": False,
            },
            ":1.5",
        )
        assert result == ["/usr/lib/..."]
        policy = ctx.policy_engine.apply_policy.call_args[0][0]
        assert policy.subvolume == "/"
        assert policy.schedule_daily is True
        assert policy.retention.keep_daily == 14
        ctx.config_store.set_policy.assert_called_once_with(policy)
        ctx.config_store.save.assert_called_once()

    def test_apply_policy_dry_run_skips_config(self) -> None:
        handler, ctx, _ = make_handler()
        handler.dispatch("ApplyPolicy", {"subvolume": "/", "dry_run": True},
                         ":1.5")
        ctx.config_store.save.assert_not_called()

    def test_remove_policy(self) -> None:
        handler, ctx, _ = make_handler()
        ctx.policy_engine.remove_policy.return_value = ["unit"]
        result = handler.dispatch(
            "RemovePolicy", {"subvolume": "/home", "dry_run": False}, ":1.5"
        )
        assert result == ["unit"]
        ctx.config_store.remove_policy.assert_called_once_with("/home")
        ctx.config_store.save.assert_called_once()

    def test_set_config_only_changes_given_keys(self) -> None:
        handler, ctx, _ = make_handler()
        ctx.config_store.default_subvolume = "/"
        handler.dispatch(
            "SetConfig",
            {"default_subvolume": "", "snapshot_dir": ".snapshots",
             "boot_from_snapshot": True, "dry_run": False},
            ":1.5",
        )
        ctx.config_store.save.assert_called_once()
        assert ctx.config_store.boot_from_snapshot is True

    def test_setup_apply(self) -> None:
        handler, ctx, _ = make_handler()
        with patch("manaradix.core.setup_wizard.apply_profile",
                   return_value={"config_written": True}) as mock_apply:
            result = handler.dispatch(
                "SetupApply", {"profile_id": "desktop", "dry_run": True},
                ":1.5",
            )
        mock_apply.assert_called_once_with(ctx, "desktop", dry_run=True)
        assert result["config_written"] is True

    def test_grub_install_uninstall_update(self) -> None:
        handler, ctx, _ = make_handler()
        handler.dispatch("GrubInstall",
                         {"snapshot_dir": "/.snapshots", "subvol_name": "root",
                          "dry_run": True}, ":1.5")
        ctx.grub_integration.install_script.assert_called_once_with(
            "/.snapshots", "root", dry_run=True
        )
        handler.dispatch("GrubUninstall", {"dry_run": True}, ":1.5")
        ctx.grub_integration.uninstall_script.assert_called_once_with(
            dry_run=True
        )
        handler.dispatch("GrubUpdate", {"dry_run": True}, ":1.5")
        ctx.grub_integration.update_grub.assert_called_once_with(dry_run=True)


class TestFilesystemDelegation:
    def test_balance_start_forwards_filters(self) -> None:
        handler, ctx, _ = make_handler()
        handler.dispatch(
            "BalanceStart",
            {"mount": "/", "dusage": 10, "musage": 20, "dry_run": True},
            ":1.5",
        )
        ctx.btrfs_ops.balance_start.assert_called_once_with(
            "/", dusage=10, musage=20, dry_run=True
        )

    def test_balance_cancel_and_status(self) -> None:
        handler, ctx, _ = make_handler()
        ctx.btrfs_ops.balance_status.return_value = type(
            "BS", (), {"total": 5, "relocated": 2, "running": True})()
        result = handler.dispatch("BalanceStatus", {"mount": "/"}, ":1.5")
        assert result == {"total": 5, "relocated": 2, "running": True}
        handler.dispatch("BalanceCancel", {"mount": "/", "dry_run": True},
                         ":1.5")
        ctx.btrfs_ops.balance_cancel.assert_called_once_with(
            "/", dry_run=True
        )

    def test_scrub_and_defrag_and_compression(self) -> None:
        handler, ctx, _ = make_handler()
        handler.dispatch("ScrubStart", {"mount": "/", "dry_run": True},
                         ":1.5")
        ctx.btrfs_ops.scrub_start.assert_called_once_with(
            "/", dry_run=True
        )
        handler.dispatch("Defragment",
                         {"path": "/var", "recursive": True,
                          "dry_run": True}, ":1.5")
        ctx.btrfs_ops.defragment.assert_called_once_with(
            "/var", recursive=True, dry_run=True
        )
        handler.dispatch("SetCompression",
                         {"path": "/", "algorithm": "zstd",
                          "dry_run": True}, ":1.5")
        ctx.btrfs_ops.set_compression.assert_called_once_with(
            "/", "zstd", dry_run=True
        )
        ctx.btrfs_ops.get_compression.return_value = "zstd"
        assert handler.dispatch("GetCompression", {"path": "/"}, ":1.5") == "zstd"

    def test_subvolume_ops(self) -> None:
        handler, ctx, _ = make_handler()
        handler.dispatch("CreateSubvolume", {"path": "/mnt/vol",
                                             "dry_run": True}, ":1.5")
        ctx.btrfs_ops.create_subvolume.assert_called_once_with(
            "/mnt/vol", dry_run=True
        )
        handler.dispatch("DeleteSubvolume", {"path": "/mnt/vol",
                                             "dry_run": True}, ":1.5")
        ctx.btrfs_ops.delete_subvolume.assert_called_once_with(
            "/mnt/vol", dry_run=True
        )
        handler.dispatch("SetDefaultSubvolume",
                         {"id": 257, "mount": "/", "dry_run": True}, ":1.5")
        ctx.btrfs_ops.set_default_subvolume.assert_called_once_with(
            257, "/", dry_run=True
        )


class TestReadDelegation:
    def test_list_subvolumes_and_mounts(self) -> None:
        handler, ctx, _ = make_handler()
        ctx.btrfs_ops.list_subvolumes.return_value = [type(
            "SV", (), {"id": 5, "path": "/", "parent_id": 0, "uuid": "",
                       "parent_uuid": "", "is_top_level": True})()]
        result = handler.dispatch("ListSubvolumes", {"mount": "/"}, ":1.5")
        assert result[0]["id"] == 5
        assert result[0]["is_top_level"] is True

        ctx.btrfs_ops.btrfs_mounts.return_value = [{"target": "/"}]
        result = handler.dispatch("BtrfsMounts", {}, ":1.5")
        assert result == [{"target": "/"}]

    def test_config_and_policies(self) -> None:
        handler, ctx, _ = make_handler()
        ctx.config_store.to_dict.return_value = {"snapshot_dir": ".snapshots"}
        assert handler.dispatch("GetConfig", {}, ":1.5") == {
            "snapshot_dir": ".snapshots"
        }

    def test_setup_status_builds_payload(self) -> None:
        handler, ctx, _ = make_handler()
        with patch("manaradix.dbus.service.system_layout",
                   return_value={"root_is_btrfs": True,
                                 "grub_present": True,
                                 "btrfs_targets": ["/"]}), \
             patch("manaradix.dbus.service.detect_role",
                   return_value="desktop"), \
             patch("manaradix.dbus.service.needs_setup",
                   return_value=True), \
             patch("manaradix.dbus.service.load_profiles",
                   return_value=[type("P", (), {"id": "desktop",
                                                "name": "Desktop",
                                                "description": "",
                                                "role": "desktop"})()]), \
             patch("manaradix.dbus.service.select_default_profile",
                   return_value=type("P", (), {"id": "desktop",
                                               "name": "Desktop",
                                               "description": "",
                                               "role": "desktop"})()):
            result = handler.dispatch("SetupStatus", {}, ":1.5")
        assert result["needs_setup"] is True
        assert result["role"] == "desktop"
        assert result["default_profile"]["id"] == "desktop"
        assert result["root_is_btrfs"] is True
        assert result["grub_present"] is True

    def test_grub_is_installed_and_entries(self) -> None:
        handler, ctx, _ = make_handler()
        ctx.grub_integration.is_installed.return_value = True
        assert handler.dispatch("GrubIsInstalled", {}, ":1.5") is True
        ctx.grub_integration.list_snapshot_entries.return_value = ["entry"]
        assert handler.dispatch("GrubListEntries", {}, ":1.5") == ["entry"]


class TestGetDeviceStats:
    """handle_GetDeviceStats: btrfs counters merged with UDisks2 SMART."""

    def _handler(self, smart_map=None):
        ctx = MagicMock()
        ctx.btrfs_ops.btrfs_mounts.return_value = [
            {"target": "/", "source": "/dev/sda2", "uuid": "u1"},
            {"target": "/", "source": "/dev/sda2", "uuid": "u1"},  # dup target
            {"target": "/data", "source": "/dev/sda2", "uuid": "u1"},
        ]
        ctx.btrfs_ops.device_stats.side_effect = lambda mount: (
            [{"device": "/dev/sda2", "write_io_errs": 0, "read_io_errs": 1,
              "flush_io_errs": 0, "corruption_errs": 0, "generation_errs": 0}]
            if mount == "/"
            else [{"device": "/dev/sdb1", "write_io_errs": 0, "read_io_errs": 0,
                   "flush_io_errs": 0, "corruption_errs": 0,
                   "generation_errs": 0}]
        )
        return ManaradixHandler(
            ctx,
            auth_check=lambda pid, action: None,
            pid_resolver=lambda connection, sender: 4242,
            smart_probe_all=lambda devices: smart_map or {},
        )

    def test_merges_stats_and_smart(self) -> None:
        handler = self._handler(
            smart_map={
                "/dev/sda": {"failing": False, "temperature": 38.0,
                             "reallocated_sectors": 100,
                             "pending_sectors": 100,
                             "uncorrectable_errors": 100},
            }
        )
        result = handler.dispatch("GetDeviceStats", {}, ":1.7")
        devices = {d["device"]: d for d in result}
        assert set(devices) == {"/dev/sda2", "/dev/sdb1"}
        # SMART attaches to the whole disk backing the partition.
        assert devices["/dev/sda2"]["smart_available"] is True
        assert devices["/dev/sda2"]["smart_failing"] is False
        assert devices["/dev/sda2"]["smart_temperature"] == 38.0
        assert not any(k.startswith("smart_") for k in devices["/dev/sdb1"])

    def test_no_smart_leaves_plain_counters(self) -> None:
        handler = self._handler()
        result = handler.dispatch("GetDeviceStats", {}, ":1.7")
        assert len(result) == 2
        for device in result:
            assert not any(k.startswith("smart_") for k in device)

    def test_no_mounts_returns_empty(self) -> None:
        handler = self._handler()
        handler._ctx.btrfs_ops.btrfs_mounts.return_value = []
        result = handler.dispatch("GetDeviceStats", {}, ":1.7")
        assert result == []

    def test_requires_read_tier(self) -> None:
        handler = self._handler()
        handler._ctx.btrfs_ops.btrfs_mounts.return_value = []
        auth_calls = []
        handler._auth_check = lambda pid, action: auth_calls.append(action)
        handler.dispatch("GetDeviceStats", {}, ":1.7")
        assert auth_calls == [ACTION_READ]
