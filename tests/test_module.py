"""Unit tests for CLI subcommand dispatch in module.py."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from manaradix.core.config_store import ConfigStore
from manaradix.model.policy import RetentionPolicy, SnapshotPolicy
from manaradix.module import (
    _build_parser,
    _cmd_setup_apply,
    _cmd_setup_status,
    _cmd_snapshot_create,
    _cmd_snapshot_delete,
    _cmd_snapshot_info,
    _cmd_snapshot_list,
    _cmd_snapshot_prune,
    _frontend_available,
    _init_services,
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
        assert args.dry_run is True


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
    @patch("manaradix.module._init_services")
    def test_status_reports_needed(self, mock_init) -> None:
        ctx = MagicMock()
        ctx.snapshot_manager.list_all.return_value = []
        ctx.config_store.list_policies.return_value = []
        exit_code = _cmd_setup_status(ctx, MagicMock())
        assert exit_code == 0

    @patch("manaradix.module._init_services")
    def test_status_reports_configured(self, mock_init) -> None:
        ctx = MagicMock()
        ctx.snapshot_manager.list_all.return_value = [MagicMock()]
        exit_code = _cmd_setup_status(ctx, MagicMock())
        assert exit_code == 0

    def test_apply_dry_run(self) -> None:
        ctx = MagicMock()
        with patch(
            "manaradix.core.setup_wizard.apply_recommended",
            return_value={
                "schedules": ["daily"],
                "retention": {"keep_daily": 7},
                "grub_installed": False,
            },
        ):
            exit_code = _cmd_setup_apply(ctx, MagicMock(dry_run=True))
        assert exit_code == 0

    def test_apply_failure(self) -> None:
        ctx = MagicMock()
        with patch(
            "manaradix.core.setup_wizard.apply_recommended",
            side_effect=RuntimeError("boom"),
        ):
            exit_code = _cmd_setup_apply(ctx, MagicMock(dry_run=False))
        assert exit_code == 1
