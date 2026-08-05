"""Unit tests for the first-run setup wizard (core/setup_wizard.py)."""

from unittest.mock import MagicMock, patch

import pytest

from manaradix.core.setup_wizard import (
    apply_profile,
    build_plan,
    detect_role,
    needs_setup,
    select_default_profile,
    system_layout,
)
from manaradix.core.templates import SetupProfile, TemplatePolicy
from manaradix.model.policy import SnapshotPolicy


def make_ctx(*, mounts=None, grub=False, snapshots=None, policies=None, list_error=None):
    """An AppContext stub whose services are MagicMocks.

    *mounts* accepts bare target paths (e.g. ``"/"``) or full
    ``{"target", ...}`` dicts.
    """
    ctx = MagicMock()
    if isinstance(mounts, str):
        mounts = [mounts]
    raw_mounts = list(mounts or [])
    ctx.btrfs_ops.btrfs_mounts.return_value = [
        m if isinstance(m, dict) else {"target": m, "source": "/dev/x", "uuid": "u"}
        for m in raw_mounts
    ]
    ctx.grub_integration.has_system_grub.return_value = grub
    if list_error is not None:
        ctx.snapshot_manager.list_all.side_effect = list_error
    else:
        ctx.snapshot_manager.list_all.return_value = list(snapshots or [])
    ctx.config_store.list_policies.return_value = list(policies or [])
    return ctx


def mounts(*targets: str) -> list[dict]:
    return [{"target": t, "source": "/dev/x", "uuid": "u"} for t in targets]


def make_profile(**overrides) -> SetupProfile:
    base = dict(
        id="desktop",
        name="Desktop",
        description="",
        role="desktop",
        boot_from_snapshot=True,
        snapshot_before_upgrade=True,
        snapshot_dir=".snapshots",
        policies=[
            TemplatePolicy(subvolume="/", schedules=["daily", "weekly", "boot"]),
            TemplatePolicy(subvolume="/home", schedules=["daily"], required=False),
        ],
    )
    base.update(overrides)
    return SetupProfile(**base)


# ---------------------------------------------------------------------------
# needs_setup (trigger condition)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# system_layout
# ---------------------------------------------------------------------------


class TestSystemLayout:
    def test_root_btrfs_detected(self) -> None:
        ctx = make_ctx(mounts="/", grub=True)
        layout = system_layout(ctx)
        assert layout["btrfs_targets"] == ["/"]
        assert layout["root_is_btrfs"] is True
        assert layout["grub_present"] is True

    def test_no_btrfs_mounts(self) -> None:
        ctx = make_ctx()
        layout = system_layout(ctx)
        assert layout["btrfs_targets"] == []
        assert layout["root_is_btrfs"] is False
        assert layout["grub_present"] is False

    def test_mounts_error_degrades(self) -> None:
        ctx = make_ctx()
        ctx.btrfs_ops.btrfs_mounts.side_effect = RuntimeError("findmnt broken")
        layout = system_layout(ctx)
        assert layout["btrfs_targets"] == []
        assert layout["root_is_btrfs"] is False

    def test_grub_probe_error_degrades(self) -> None:
        ctx = make_ctx(mounts="/")
        ctx.grub_integration.has_system_grub.side_effect = OSError("no grub")
        layout = system_layout(ctx)
        assert layout["root_is_btrfs"] is True
        assert layout["grub_present"] is False


# ---------------------------------------------------------------------------
# detect_role
# ---------------------------------------------------------------------------


class TestDetectRole:
    @patch("manaradix.core.setup_wizard.subprocess.run")
    def test_graphical_target(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="graphical.target\n")
        assert detect_role() == "desktop"

    @patch("manaradix.core.setup_wizard.subprocess.run")
    def test_multi_user_target(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="multi-user.target\n")
        assert detect_role() == "server"

    @patch(
        "manaradix.core.setup_wizard.subprocess.run",
        side_effect=OSError("no systemd"),
    )
    def test_systemctl_error_falls_back_to_xsessions(self, mock_run: MagicMock) -> None:
        with patch("manaradix.core.setup_wizard.os.path.isdir", return_value=True), \
             patch("manaradix.core.setup_wizard.os.listdir", return_value=["plasma.desktop"]):
            assert detect_role() == "desktop"

    @patch(
        "manaradix.core.setup_wizard.subprocess.run",
        side_effect=OSError("no systemd"),
    )
    def test_no_xsessions_means_server(self, mock_run: MagicMock) -> None:
        with patch("manaradix.core.setup_wizard.os.path.isdir", return_value=False):
            assert detect_role() == "server"


# ---------------------------------------------------------------------------
# select_default_profile
# ---------------------------------------------------------------------------


class TestSelectDefaultProfile:
    def _profiles(self) -> list[SetupProfile]:
        return [
            make_profile(id="desktop", role="desktop"),
            make_profile(id="server", role="server"),
            make_profile(id="kiosk", role="any"),
        ]

    def test_prefers_matching_role(self) -> None:
        assert select_default_profile(self._profiles(), "server").id == "server"

    def test_falls_back_to_any_profile(self) -> None:
        profiles = [p for p in self._profiles() if p.role != "server"]
        assert select_default_profile(profiles, "server").id == "kiosk"

    def test_any_role_matches_any(self) -> None:
        assert select_default_profile(self._profiles(), "desktop").id == "desktop"

    def test_empty_list_returns_none(self) -> None:
        assert select_default_profile([], "desktop") is None


# ---------------------------------------------------------------------------
# build_plan (layout adaptation)
# ---------------------------------------------------------------------------


class TestBuildPlan:
    def test_full_desktop_plan(self) -> None:
        ctx = make_ctx(mounts=("/", "/home"), grub=True)
        plan = build_plan(ctx, make_profile())

        assert plan["applicable"] is True
        assert plan["boot_from_snapshot_effective"] is True
        assert plan["snapshot_before_upgrade_effective"] is True
        assert plan["snapshot_dir"] == ".snapshots"
        assert plan["notes"] == []

        root, home = plan["policies"]
        assert root["subvolume"] == "/"
        assert root["available"] is True
        assert root["schedules"] == ["daily", "weekly", "boot"]
        assert root["retention"]["keep_daily"] == 7
        assert home["subvolume"] == "/home"
        assert home["available"] is True

    def test_boot_kept_when_root_is_btrfs(self) -> None:
        ctx = make_ctx(mounts="/", grub=True)
        plan = build_plan(ctx, make_profile())
        root = next(p for p in plan["policies"] if p["subvolume"] == "/")
        assert "boot" in root["schedules"]

    def test_boot_dropped_when_root_not_btrfs(self) -> None:
        # Root is ext4; only /home is btrfs.
        ctx = make_ctx(mounts="/home", grub=True)
        plan = build_plan(ctx, make_profile())

        assert plan["boot_from_snapshot_effective"] is False
        assert plan["snapshot_before_upgrade_effective"] is False
        root = next(p for p in plan["policies"] if p["subvolume"] == "/")
        assert root["available"] is False
        assert "boot" not in root["schedules"]
        assert any("Boot snapshots are not available" in n for n in plan["notes"])
        assert any("GRUB recovery menu is not available" in n for n in plan["notes"])
        assert any("Pre-upgrade snapshots are not available" in n for n in plan["notes"])

    def test_boot_only_policy_needs_btrfs_root(self) -> None:
        # A boot-only policy for a btrfs subvolume is dropped when the
        # root filesystem itself is not btrfs.
        profile = make_profile(
            policies=[TemplatePolicy(subvolume="/home", schedules=["boot"])]
        )
        ctx = make_ctx(mounts="/home", grub=True)
        plan = build_plan(ctx, profile)

        entry = plan["policies"][0]
        assert entry["available"] is False
        assert "Boot snapshots are not available" in entry["reason"]

    def test_optional_policy_dropped_silently(self) -> None:
        ctx = make_ctx(mounts="/")  # no /home mount
        plan = build_plan(ctx, make_profile())
        subvols = [p["subvolume"] for p in plan["policies"]]
        assert "/home" not in subvols

    def test_required_missing_policy_annotated(self) -> None:
        profile = make_profile(
            policies=[
                TemplatePolicy(subvolume="/", schedules=["daily"]),
                TemplatePolicy(subvolume="/srv", schedules=["daily"]),
            ]
        )
        ctx = make_ctx(mounts="/")
        plan = build_plan(ctx, profile)

        srv = next(p for p in plan["policies"] if p["subvolume"] == "/srv")
        assert srv["available"] is False
        assert srv["reason"] is not None

    def test_grub_menu_only_with_grub(self) -> None:
        ctx = make_ctx(mounts="/", grub=False)
        plan = build_plan(ctx, make_profile())
        assert plan["boot_from_snapshot_effective"] is False
        assert any("GRUB recovery menu is not available" in n for n in plan["notes"])

    def test_inapplicable_when_nothing_matches(self) -> None:
        ctx = make_ctx()  # no btrfs at all
        plan = build_plan(ctx, make_profile())
        assert plan["applicable"] is False


# ---------------------------------------------------------------------------
# apply_profile
# ---------------------------------------------------------------------------


class TestApplyProfile:
    def _desktop_ctx(self):
        return make_ctx(mounts=("/", "/home"), grub=True)

    def test_unknown_profile_raises(self) -> None:
        with patch(
            "manaradix.core.setup_wizard.load_profiles",
            return_value=[make_profile()],
        ):
            with pytest.raises(ValueError, match="Unknown setup profile"):
                apply_profile(self._desktop_ctx(), "nope")

    def test_inapplicable_profile_raises(self) -> None:
        with patch(
            "manaradix.core.setup_wizard.load_profiles",
            return_value=[make_profile()],
        ):
            with pytest.raises(ValueError, match="does not apply"):
                apply_profile(make_ctx(), "desktop")

    def test_no_profiles_raises(self) -> None:
        with patch(
            "manaradix.core.setup_wizard.load_profiles", return_value=[]
        ):
            with pytest.raises(ValueError, match="No setup profiles"):
                apply_profile(self._desktop_ctx())

    def test_dry_run_writes_nothing(self) -> None:
        ctx = self._desktop_ctx()
        with patch(
            "manaradix.core.setup_wizard.load_profiles",
            return_value=[make_profile()],
        ):
            summary = apply_profile(ctx, "desktop", dry_run=True)

        ctx.config_store.save.assert_not_called()
        ctx.policy_engine.apply_policy.assert_not_called()
        ctx.grub_integration.install_script.assert_not_called()
        assert summary["config_written"] is False
        assert summary["grub_installed"] is False
        assert summary["profile_id"] == "desktop"
        assert {p["subvolume"] for p in summary["policies"]} == {"/", "/home"}

    def test_applies_config_policies_and_grub(self) -> None:
        ctx = self._desktop_ctx()
        with patch(
            "manaradix.core.setup_wizard.load_profiles",
            return_value=[make_profile()],
        ):
            summary = apply_profile(ctx, "desktop")

        assert ctx.config_store.default_subvolume == "/"
        assert ctx.config_store.snapshot_dir == ".snapshots"
        assert ctx.config_store.boot_from_snapshot is True
        assert ctx.config_store.snapshot_before_upgrade is True
        assert ctx.config_store.set_policy.call_count == 2
        ctx.config_store.save.assert_called_once()
        assert ctx.policy_engine.apply_policy.call_count == 2
        ctx.grub_integration.install_script.assert_called_once()
        ctx.grub_integration.update_grub.assert_called_once()
        assert summary["config_written"] is True
        assert summary["grub_installed"] is True

    def test_default_profile_selected_from_detected_role(self) -> None:
        ctx = self._desktop_ctx()
        profiles = [
            make_profile(id="desktop", role="desktop"),
            make_profile(id="server", role="server"),
        ]
        with patch(
            "manaradix.core.setup_wizard.load_profiles", return_value=profiles
        ), patch("manaradix.core.setup_wizard.detect_role", return_value="server"):
            summary = apply_profile(ctx)
        assert summary["profile_id"] == "server"

    def test_no_grub_when_ineffective(self) -> None:
        ctx = make_ctx(mounts="/home")  # root not btrfs
        with patch(
            "manaradix.core.setup_wizard.load_profiles",
            return_value=[make_profile()],
        ):
            summary = apply_profile(ctx, "desktop")

        assert ctx.config_store.boot_from_snapshot is False
        assert ctx.config_store.snapshot_before_upgrade is False
        ctx.grub_integration.install_script.assert_not_called()
        assert summary["grub_installed"] is False

    def test_grub_failure_is_not_fatal(self) -> None:
        ctx = self._desktop_ctx()
        ctx.grub_integration.install_script.side_effect = RuntimeError("no grub")
        with patch(
            "manaradix.core.setup_wizard.load_profiles",
            return_value=[make_profile()],
        ):
            summary = apply_profile(ctx, "desktop")

        assert summary["grub_installed"] is False
        # The policies themselves were still applied
        assert ctx.policy_engine.apply_policy.call_count == 2

    def test_policy_unit_failure_raises(self) -> None:
        ctx = self._desktop_ctx()
        ctx.policy_engine.apply_policy.side_effect = RuntimeError("systemd down")
        with patch(
            "manaradix.core.setup_wizard.load_profiles",
            return_value=[make_profile()],
        ):
            with pytest.raises(RuntimeError):
                apply_profile(ctx, "desktop")
