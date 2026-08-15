"""Tests for the ``tray serve`` / ``tray status`` CLI surface."""

from unittest.mock import MagicMock, patch

import pytest

from manaradix.module import (
    _build_parser,
    _cmd_tray_serve,
    _cmd_tray_status,
    main,
)


class TestParser:
    def test_tray_serve(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["tray", "serve"])
        assert args.command == "tray"
        assert args.tray_command == "serve"

    def test_tray_status(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["tray", "status"])
        assert args.command == "tray"
        assert args.tray_command == "status"

    def test_tray_without_subcommand_parses_but_dispatches_nowhere(
            self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["tray"])
        assert args.command == "tray"
        assert getattr(args, "tray_command", None) is None


class TestCmdServe:
    def test_serve_runs_tray_app(self) -> None:
        store = MagicMock()
        ctx = MagicMock(config_store=store)
        fake_app = MagicMock()
        fake_app.run.return_value = 0
        with patch("manaradix.tray.app.TrayApp", return_value=fake_app) as cls:
            exit_code = _cmd_tray_serve(ctx, MagicMock())
        assert exit_code == 0
        cls.assert_called_once_with(store)
        fake_app.run.assert_called_once()

    def test_serve_exit_code_propagated(self) -> None:
        ctx = MagicMock(config_store=MagicMock())
        fake_app = MagicMock()
        fake_app.run.return_value = 3
        with patch("manaradix.tray.app.TrayApp", return_value=fake_app):
            exit_code = _cmd_tray_serve(ctx, MagicMock())
        assert exit_code == 3


class TestCmdStatus:
    def test_running_exits_zero(self, capsys) -> None:
        with patch("manaradix.tray.app.tray_running", return_value=True):
            exit_code = _cmd_tray_status(MagicMock(), MagicMock())
        assert exit_code == 0
        assert "running" in capsys.readouterr().out

    def test_not_running_exits_two(self, capsys) -> None:
        with patch("manaradix.tray.app.tray_running", return_value=False):
            exit_code = _cmd_tray_status(MagicMock(), MagicMock())
        assert exit_code == 2
        assert "starts automatically" in capsys.readouterr().out

    def test_bus_unavailable_treated_as_not_running(self, monkeypatch) -> None:
        # tray_running swallows bus errors itself and reports False.
        from manaradix.tray.app import tray_running

        def boom():
            raise RuntimeError("no session bus")

        monkeypatch.setattr("dbus.SessionBus", boom)
        assert tray_running() is False


class TestMainFlow:
    """main() must consult needs_elevation for tray subcommands."""

    def test_tray_serve_dispatches_without_pkexec(self) -> None:
        captured = {}

        def fake_handler(ctx, args):
            captured["key"] = (args.command, args.tray_command)
            return 0

        with patch("manaradix.module._init_services"), \
             patch("manaradix.module.needs_elevation",
                   return_value=False) as mock_needs, \
             patch("manaradix.module.reexec_with_pkexec") as mock_reexec, \
             patch("manaradix.module._COMMAND_DISPATCH",
                   {("tray", "serve"): fake_handler}), \
             patch("sys.argv", ["manaradix", "tray", "serve"]):
            with pytest.raises(SystemExit) as excinfo:
                main()
        assert captured["key"] == ("tray", "serve")
        assert excinfo.value.code == 0
        mock_needs.assert_called_once_with("tray", "serve")
        mock_reexec.assert_not_called()

    def test_tray_status_dispatches_without_pkexec(self) -> None:
        captured = {}

        def fake_handler(ctx, args):
            captured["key"] = (args.command, args.tray_command)
            return 0

        with patch("manaradix.module._init_services"), \
             patch("manaradix.module.needs_elevation",
                   return_value=False), \
             patch("manaradix.module.reexec_with_pkexec") as mock_reexec, \
             patch("manaradix.module._COMMAND_DISPATCH",
                   {("tray", "status"): fake_handler}), \
             patch("sys.argv", ["manaradix", "tray", "status"]):
            with pytest.raises(SystemExit) as excinfo:
                main()
        assert captured["key"] == ("tray", "status")
        assert excinfo.value.code == 0
        mock_reexec.assert_not_called()
