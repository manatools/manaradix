"""Unit tests for CLI subcommand dispatch in module.py."""

import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from manaradix.core.config_store import ConfigStore
from manaradix.model.policy import RetentionPolicy, SnapshotPolicy
from manaradix.module import (
    _build_parser,
    _cmd_setup_apply,
    _cmd_setup_list_profiles,
    _cmd_setup_status,
    _cmd_snapshot_create,
    _cmd_snapshot_delete,
    _cmd_snapshot_info,
    _cmd_snapshot_list,
    _cmd_snapshot_prune,
    _frontend_available,
    _frontend_from_env,
    _init_services,
    _recover_pkexec_display,
    run_app,
)


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


class TestParser:
    def test_default_gui_mode(self) -> None:
        parser = _build_parser()
        args = parser.parse_args([])
        assert args.command is None
        assert args.frontend is None  # None → YUI auto-detect

    def test_snapshot_create_minimal(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["snapshot", "create"])
        assert args.command == "snapshot"
        assert args.snapshot_command == "create"
        assert args.subvolume == "/"
        assert args.snapshot_type == "manual"
        assert args.schedule == ""

    def test_snapshot_create_full(self) -> None:
        parser = _build_parser()
        args = parser.parse_args([
            "snapshot", "create",
            "--subvolume", "/home",
            "--type", "timed",
            "--schedule", "daily",
            "--description", "auto daily",
            "--read-write",
            "--dry-run",
        ])
        assert args.subvolume == "/home"
        assert args.snapshot_type == "timed"
        assert args.schedule == "daily"
        assert args.description == "auto daily"
        assert args.read_write is True
        assert args.dry_run is True

    def test_snapshot_list(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["snapshot", "list"])
        assert args.snapshot_command == "list"
        assert args.mount == "/"

    def test_snapshot_delete(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["snapshot", "delete", "--id", "snap1"])
        assert args.snapshot_command == "delete"
        assert args.snapshot_id == "snap1"

    def test_snapshot_delete_requires_id(self) -> None:
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["snapshot", "delete"])

    def test_snapshot_prune(self) -> None:
        parser = _build_parser()
        args = parser.parse_args([
            "snapshot", "prune",
            "--subvolume", "/",
            "--schedule", "daily",
        ])
        assert args.snapshot_command == "prune"
        assert args.subvolume == "/"
        assert args.schedule == "daily"

    def test_snapshot_info(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["snapshot", "info", "--id", "snap1"])
        assert args.snapshot_command == "info"
        assert args.snapshot_id == "snap1"

    def test_setup_status(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["setup", "status"])
        assert args.command == "setup"
        assert args.setup_command == "status"

    def test_setup_apply(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["setup", "apply", "--dry-run"])
        assert args.command == "setup"
        assert args.setup_command == "apply"
        assert args.profile is None
        assert args.dry_run is True

    def test_setup_apply_with_profile(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["setup", "apply", "--profile", "kiosk"])
        assert args.setup_command == "apply"
        assert args.profile == "kiosk"

    def test_setup_list_profiles(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["setup", "list-profiles"])
        assert args.command == "setup"
        assert args.setup_command == "list-profiles"


# ---------------------------------------------------------------------------
# Frontend availability probe tests
# ---------------------------------------------------------------------------


class TestFrontendAvailable:
    def test_ncurses_always_available(self) -> None:
        assert _frontend_available("ncurses") is True

    @patch("subprocess.run")
    def test_qt_probe_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        assert _frontend_available("qt") is True
        mock_run.assert_called_once()

    @patch("subprocess.run")
    def test_qt_probe_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1)
        assert _frontend_available("qt") is False

    @patch("subprocess.run")
    def test_probe_timeout(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="probe", timeout=15)
        assert _frontend_available("qt") is False

    @patch("subprocess.run")
    def test_probe_oserror(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = OSError("no python")
        assert _frontend_available("gtk") is False


# ---------------------------------------------------------------------------
# Explicit frontend: pkexec display-environment recovery
# ---------------------------------------------------------------------------
# A pkexec re-exec strips DISPLAY/WAYLAND_DISPLAY/XDG_RUNTIME_DIR, so the
# explicit --frontend path must recover them (like YUI's auto-detect) or
# its graphical probe fails and the app wrongly falls back to ncurses.


class TestRecoverPkexecDisplay:
    pytest.importorskip("manatools.aui.yui")

    @patch("manatools.aui.yui.YUI._detect_backend")
    def test_recovers_when_pkexec_uid_set(self, mock_detect: MagicMock) -> None:
        with patch.dict(os.environ, {"PKEXEC_UID": "1000"}, clear=False):
            _recover_pkexec_display()
        mock_detect.assert_called_once()

    @patch("manatools.aui.yui.YUI._detect_backend")
    def test_noop_without_pkexec_uid(self, mock_detect: MagicMock) -> None:
        with patch.dict(os.environ, {}, clear=True):
            _recover_pkexec_display()
        mock_detect.assert_not_called()

    @patch(
        "manatools.aui.yui.YUI._detect_backend",
        side_effect=RuntimeError("no backend"),
    )
    def test_swallows_detection_errors(self, mock_detect: MagicMock) -> None:
        with patch.dict(os.environ, {"PKEXEC_UID": "1000"}, clear=False):
            _recover_pkexec_display()  # must not raise


class TestRunAppExplicitFrontend:
    pytest.importorskip("manatools.aui.yui")

    def _run(self, tmp_path, *args, **kwargs):
        kwargs.setdefault("config_path", str(tmp_path / "config.toml"))
        return run_app(*args, **kwargs)

    @patch("manaradix.module._recover_pkexec_display")
    @patch("manaradix.module._frontend_available", side_effect=[False, True])
    @patch("manaradix.module.build_navigation")
    @patch("manaradix.ui.renderer.AuiRenderer")
    def test_recovers_display_and_keeps_frontend(
        self,
        mock_renderer: MagicMock,
        mock_nav: MagicMock,
        mock_probe: MagicMock,
        mock_recover: MagicMock,
        tmp_path,
    ) -> None:
        # First probe fails (env stripped by pkexec), recovery runs, then
        # the re-probe succeeds: the explicit qt choice must survive.
        with patch.dict(os.environ, {"PKEXEC_UID": "1000"}, clear=False):
            self._run(tmp_path, frontend="qt")
            assert os.environ["MUI_BACKEND"] == "qt"
        mock_recover.assert_called_once()
        mock_renderer.return_value.run.assert_called_once()

    @patch("manaradix.module._recover_pkexec_display")
    @patch("manaradix.module._frontend_available", return_value=False)
    @patch("manaradix.module.build_navigation")
    @patch("manaradix.ui.renderer.AuiRenderer")
    def test_falls_back_to_ncurses_when_recovery_fails(
        self,
        mock_renderer: MagicMock,
        mock_nav: MagicMock,
        mock_probe: MagicMock,
        mock_recover: MagicMock,
        tmp_path,
    ) -> None:
        with patch.dict(os.environ, {"PKEXEC_UID": "1000"}, clear=False):
            self._run(tmp_path, frontend="gtk")
            assert os.environ["MUI_BACKEND"] == "ncurses"
        mock_recover.assert_called_once()

    @patch("manaradix.module._recover_pkexec_display")
    @patch("manaradix.module._frontend_available", return_value=True)
    @patch("manaradix.module.build_navigation")
    @patch("manaradix.ui.renderer.AuiRenderer")
    def test_no_recovery_when_probe_succeeds(
        self,
        mock_renderer: MagicMock,
        mock_nav: MagicMock,
        mock_probe: MagicMock,
        mock_recover: MagicMock,
        tmp_path,
    ) -> None:
        with patch.dict(os.environ, {"PKEXEC_UID": "1000"}, clear=False):
            self._run(tmp_path, frontend="qt")
            assert os.environ["MUI_BACKEND"] == "qt"
        mock_recover.assert_not_called()


# ---------------------------------------------------------------------------
# Service init tests
# ---------------------------------------------------------------------------


class TestInitServices:
    def test_creates_app_context(self, tmp_path) -> None:
        config = tmp_path / "config.toml"
        ctx = _init_services(str(config), ".snapshots")
        assert ctx.btrfs_ops is not None
        assert ctx.snapshot_manager is not None
        assert ctx.config_store is not None
        assert ctx.policy_engine is not None
        assert ctx.grub_integration is not None


# ---------------------------------------------------------------------------
# CLI handler tests
# ---------------------------------------------------------------------------


class TestCmdSnapshotCreate:
    def _ctx(self):
        return _init_services("/nonexistent/config.toml", ".snapshots")

    @patch("subprocess.run")
    @patch("os.makedirs")
    @patch("builtins.open", new_callable=MagicMock)
    def test_create_success(
        self, mock_file: MagicMock, mock_makedirs: MagicMock, mock_run: MagicMock
    ) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        ctx = self._ctx()
        args = MagicMock(
            subvolume="/", snapshot_type="manual", schedule="",
            description="test", read_write=False, mount="/", dry_run=False,
        )
        exit_code = _cmd_snapshot_create(ctx, args)
        assert exit_code == 0

    def test_create_dry_run(self) -> None:
        ctx = self._ctx()
        args = MagicMock(
            subvolume="/", snapshot_type="manual", schedule="",
            description="", read_write=False, mount="/", dry_run=True,
        )
        exit_code = _cmd_snapshot_create(ctx, args)
        assert exit_code == 0


class TestCmdSnapshotList:
    def _ctx(self):
        return _init_services("/nonexistent/config.toml", ".snapshots")

    @patch.object(ConfigStore, "load", return_value=None)
    def test_list_empty(self, mock_load: MagicMock) -> None:
        ctx = self._ctx()
        args = MagicMock(mount="/", subvolume=None)
        exit_code = _cmd_snapshot_list(ctx, args)
        assert exit_code == 0


class TestCmdSnapshotDelete:
    def _ctx(self):
        return _init_services("/nonexistent/config.toml", ".snapshots")

    def test_delete_not_found(self) -> None:
        ctx = self._ctx()
        args = MagicMock(snapshot_id="nonexistent", mount="/", dry_run=False)
        exit_code = _cmd_snapshot_delete(ctx, args)
        assert exit_code == 1


class TestCmdSnapshotPrune:
    def _make_ctx(self):
        ctx = _init_services("/nonexistent/config.toml", ".snapshots")
        # Register a policy so prune has retention config
        ctx.config_store.set_policy(
            SnapshotPolicy(
                subvolume="/",
                schedule_daily=True,
                retention=RetentionPolicy(keep_daily=3),
            )
        )
        return ctx

    def test_prune_no_policy(self) -> None:
        ctx = _init_services("/nonexistent/config.toml", ".snapshots")
        args = MagicMock(subvolume="/nonexistent", schedule="daily", mount="/", dry_run=False)
        exit_code = _cmd_snapshot_prune(ctx, args)
        assert exit_code == 0  # Warns and exits cleanly

    @patch.object(ConfigStore, "load", return_value=None)
    def test_prune_with_policy_dry_run(self, mock_load: MagicMock) -> None:
        ctx = self._make_ctx()
        args = MagicMock(subvolume="/", schedule="daily", mount="/", dry_run=True)
        exit_code = _cmd_snapshot_prune(ctx, args)
        assert exit_code == 0


class TestCmdSnapshotInfo:
    def _ctx(self):
        return _init_services("/nonexistent/config.toml", ".snapshots")

    def test_info_not_found(self) -> None:
        ctx = self._ctx()
        args = MagicMock(snapshot_id="nonexistent", mount="/")
        exit_code = _cmd_snapshot_info(ctx, args)
        assert exit_code == 1


class TestCmdSetup:
    _LAYOUT = {"btrfs_targets": ["/"], "root_is_btrfs": True, "grub_present": True}

    @patch("manaradix.module._init_services")
    def test_status_reports_needed(self, mock_init) -> None:
        ctx = MagicMock()
        ctx.snapshot_manager.list_all.return_value = []
        ctx.config_store.list_policies.return_value = []
        with patch(
            "manaradix.core.setup_wizard.system_layout", return_value=self._LAYOUT
        ), patch("manaradix.core.setup_wizard.detect_role", return_value="desktop"), \
             patch("manaradix.core.templates.load_profiles", return_value=[]):
            exit_code = _cmd_setup_status(ctx, MagicMock())
        assert exit_code == 0

    @patch("manaradix.module._init_services")
    def test_status_reports_configured(self, mock_init) -> None:
        ctx = MagicMock()
        ctx.snapshot_manager.list_all.return_value = [MagicMock()]
        exit_code = _cmd_setup_status(ctx, MagicMock())
        assert exit_code == 0

    def test_status_without_btrfs(self, capsys) -> None:
        ctx = MagicMock()
        ctx.snapshot_manager.list_all.return_value = []
        ctx.config_store.list_policies.return_value = []
        with patch(
            "manaradix.core.setup_wizard.system_layout",
            return_value={"btrfs_targets": [], "root_is_btrfs": False,
                          "grub_present": False},
        ):
            exit_code = _cmd_setup_status(ctx, MagicMock())
        assert exit_code == 0
        assert "no btrfs filesystem" in capsys.readouterr().out

    def test_apply_dry_run(self) -> None:
        ctx = MagicMock()
        summary = {
            "config_written": False,
            "profile_id": "desktop",
            "policies": [
                {"subvolume": "/", "schedules": ["daily"],
                 "retention": {"keep_daily": 7}},
            ],
            "grub_installed": False,
        }
        with patch(
            "manaradix.core.setup_wizard.apply_profile",
            return_value=summary,
        ):
            exit_code = _cmd_setup_apply(ctx, MagicMock(dry_run=True, profile=None))
        assert exit_code == 0

    def test_apply_forwards_profile(self) -> None:
        ctx = MagicMock()
        with patch(
            "manaradix.core.setup_wizard.apply_profile",
            return_value={"config_written": False, "profile_id": "kiosk",
                          "policies": [], "grub_installed": False},
        ) as mock_apply:
            _cmd_setup_apply(ctx, MagicMock(dry_run=True, profile="kiosk"))
        mock_apply.assert_called_once_with(ctx, "kiosk", dry_run=True)

    def test_apply_failure(self) -> None:
        ctx = MagicMock()
        with patch(
            "manaradix.core.setup_wizard.apply_profile",
            side_effect=RuntimeError("boom"),
        ):
            exit_code = _cmd_setup_apply(ctx, MagicMock(dry_run=False, profile=None))
        assert exit_code == 1

    def test_list_profiles(self, capsys) -> None:
        from manaradix.core.templates import SetupProfile

        ctx = MagicMock()
        profile = SetupProfile(
            id="desktop", name="Desktop", description="desc", role="desktop"
        )
        with patch("manaradix.core.templates.load_profiles", return_value=[profile]):
            exit_code = _cmd_setup_list_profiles(ctx, MagicMock())
        assert exit_code == 0
        assert "desktop" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# MUI_BACKEND → --frontend folding (pkexec strips the environment)
# ---------------------------------------------------------------------------


class TestFrontendFromEnv:
    def test_known_backend(self) -> None:
        with patch.dict(os.environ, {"MUI_BACKEND": "ncurses"}, clear=False):
            assert _frontend_from_env() == "ncurses"

    def test_unknown_backend_ignored(self) -> None:
        with patch.dict(os.environ, {"MUI_BACKEND": "web"}, clear=False):
            assert _frontend_from_env() is None

    def test_unset(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            assert _frontend_from_env() is None


class TestMainBackendFolding:
    """The env-chosen backend must survive the pkexec re-exec via argv."""

    def test_gui_reexec_folds_env_backend_into_argv(self) -> None:
        argv = ["manaradix"]
        with patch.dict(os.environ, {"MUI_BACKEND": "ncurses"}, clear=False), \
             patch("sys.argv", argv), \
             patch("manaradix.module.needs_elevation", return_value=True), \
             patch("manaradix.module.is_root", return_value=False), \
             patch("manaradix.module.already_reexecuted", return_value=False), \
             patch("manaradix.module.reexec_with_pkexec") as mock_reexec:
            from manaradix import module
            module.main()
        mock_reexec.assert_called_once()
        assert argv == ["manaradix", "--frontend", "ncurses"]

    def test_explicit_frontend_not_duplicated(self) -> None:
        argv = ["manaradix", "--frontend", "qt"]
        with patch.dict(os.environ, {"MUI_BACKEND": "ncurses"}, clear=False), \
             patch("sys.argv", argv), \
             patch("manaradix.module.needs_elevation", return_value=True), \
             patch("manaradix.module.is_root", return_value=False), \
             patch("manaradix.module.already_reexecuted", return_value=False), \
             patch("manaradix.module.reexec_with_pkexec"):
            from manaradix import module
            module.main()
        assert argv == ["manaradix", "--frontend", "qt"]

    def test_subcommand_not_folded(self) -> None:
        argv = ["manaradix", "snapshot", "create"]
        with patch.dict(os.environ, {"MUI_BACKEND": "ncurses"}, clear=False), \
             patch("sys.argv", argv), \
             patch("manaradix.module.needs_elevation", return_value=True), \
             patch("manaradix.module.is_root", return_value=False), \
             patch("manaradix.module.already_reexecuted", return_value=False), \
             patch("manaradix.module.reexec_with_pkexec"):
            from manaradix import module
            module.main()
        assert argv == ["manaradix", "snapshot", "create"]

    def test_env_backend_reaches_run_app_without_reexec(self) -> None:
        with patch.dict(os.environ, {"MUI_BACKEND": "gtk"}, clear=False), \
             patch("sys.argv", ["manaradix"]), \
             patch("manaradix.module.needs_elevation", return_value=False), \
             patch("manaradix.module.run_app") as mock_run_app:
            from manaradix import module
            module.main()
        mock_run_app.assert_called_once()
        assert mock_run_app.call_args.kwargs["frontend"] == "gtk"
