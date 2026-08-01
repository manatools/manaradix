"""Unit tests for the first-run setup wizard (core/setup_wizard.py)."""

from unittest.mock import MagicMock

import pytest

from manaradix.core.setup_wizard import (
    apply_recommended,
    needs_setup,
    recommended_plan,
    recommended_policy,
)
from manaradix.model.policy import SnapshotPolicy


def make_ctx(*, snapshots=None, policies=None, list_error=None):
    """An AppContext stub whose services are MagicMocks."""
    ctx = MagicMock()
    if list_error is not None:
        ctx.snapshot_manager.list_all.side_effect = list_error
    else:
        ctx.snapshot_manager.list_all.return_value = list(snapshots or [])
    ctx.config_store.list_policies.return_value = list(policies or [])
    return ctx


class TestNeedsSetup:
    def test_empty_system_needs_setup(self) -> None:
        assert needs_setup(make_ctx()) is True

    def test_existing_snapshot_counts_as_configured(self) -> None:
        ctx = make_ctx(snapshots=[object()])
        assert needs_setup(ctx) is False

    def test_existing_policy_counts_as_configured(self) -> None:
        ctx = make_ctx(policies=[SnapshotPolicy(subvolume="/")])
        assert needs_setup(ctx) is False

    def test_list_error_means_no_wizard(self) -> None:
        ctx = make_ctx(list_error=RuntimeError("btrfs unavailable"))
        assert needs_setup(ctx) is False

    def test_policy_error_does_not_nag(self) -> None:
        ctx = make_ctx()
        ctx.config_store.list_policies.side_effect = RuntimeError("no config")
        assert needs_setup(ctx) is True


class TestRecommendedBaseline:
    def test_policy_covers_root_with_daily_weekly_boot(self) -> None:
        policy = recommended_policy()
        assert policy.subvolume == "/"
        assert policy.enabled is True
        assert set(policy.active_schedules) == {"daily", "weekly", "boot"}

    def test_retention_uses_model_defaults(self) -> None:
        policy = recommended_policy()
        assert policy.retention.keep_daily == 7
        assert policy.retention.keep_weekly == 4
        assert policy.retention.keep_boot == 5

    def test_plan_shape(self) -> None:
        plan = recommended_plan()
        assert plan["default_subvolume"] == "/"
        assert plan["snapshot_dir"] == ".snapshots"
        assert plan["boot_from_snapshot"] is True
        assert set(plan["schedules"]) == {"daily", "weekly", "boot"}
        assert plan["retention"]["keep_daily"] == 7


class TestApplyRecommended:
    def test_dry_run_does_not_write_or_apply(self) -> None:
        ctx = make_ctx()
        summary = apply_recommended(ctx, dry_run=True)

        ctx.config_store.save.assert_not_called()
        ctx.policy_engine.apply_policy.assert_not_called()
        ctx.grub_integration.install_script.assert_not_called()
        assert summary["config_written"] is False
        assert summary["grub_installed"] is False
        assert set(summary["schedules"]) == {"daily", "weekly", "boot"}

    def test_applies_config_policy_and_grub(self) -> None:
        ctx = make_ctx()
        summary = apply_recommended(ctx)

        assert ctx.config_store.default_subvolume == "/"
        assert ctx.config_store.snapshot_dir == ".snapshots"
        assert ctx.config_store.boot_from_snapshot is True
        ctx.config_store.set_policy.assert_called_once()
        ctx.config_store.save.assert_called_once()
        ctx.policy_engine.apply_policy.assert_called_once()
        ctx.grub_integration.install_script.assert_called_once()
        ctx.grub_integration.update_grub.assert_called_once()
        assert summary["config_written"] is True
        assert summary["grub_installed"] is True

    def test_grub_failure_is_not_fatal(self) -> None:
        ctx = make_ctx()
        ctx.grub_integration.install_script.side_effect = RuntimeError("no grub")
        summary = apply_recommended(ctx)
        assert summary["grub_installed"] is False
        # The policy itself was still applied
        ctx.policy_engine.apply_policy.assert_called_once()

    def test_policy_unit_failure_raises(self) -> None:
        ctx = make_ctx()
        ctx.policy_engine.apply_policy.side_effect = RuntimeError("systemd down")
        with pytest.raises(RuntimeError):
            apply_recommended(ctx)
