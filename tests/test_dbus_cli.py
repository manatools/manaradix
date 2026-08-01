"""Unit tests for the dbus CLI subcommands (serve / status).

The daemon itself (dbus-python wire layer, GLib mainloop) is stubbed at
the import boundary — no bus, no root, no real process.
"""

from unittest.mock import MagicMock, patch

import pytest

from manaradix.module import _build_parser, main, _cmd_dbus_status
from manaradix.dbus import wire


class TestParser:
    def test_dbus_serve(self) -> None:
        args = _build_parser().parse_args(["dbus", "serve"])
        assert args.command == "dbus"
        assert args.dbus_command == "serve"

    def test_dbus_status(self) -> None:
        args = _build_parser().parse_args(["dbus", "status"])
        assert args.command == "dbus"
        assert args.dbus_command == "status"

    def test_dbus_without_subcommand_parses_but_dispatches_nowhere(self) -> None:
        # Like snapshot/setup, `dbus` alone parses with no action; main()
        # prints help and exits 1 (handler is None).
        args = _build_parser().parse_args(["dbus"])
        assert args.command == "dbus"
        assert args.dbus_command is None


class TestCmdServe:
    def test_serve_main_refuses_non_root(self, capsys) -> None:
        with patch("manaradix.utils.pkexec.is_root", return_value=False):
            exit_code = wire.serve_main("/etc/manaradix/config.toml")
        assert exit_code == 1
        assert "must run as root" in capsys.readouterr().err

    def test_serve_main_reports_name_taken(self, capsys) -> None:
        from dbus.exceptions import NameExistsException

        with patch("manaradix.utils.pkexec.is_root", return_value=True), \
             patch("dbus.service.BusName",
                   side_effect=NameExistsException("taken")), \
             patch("dbus.mainloop.glib.DBusGMainLoop"), \
             patch("dbus.SystemBus"):
            exit_code = wire.serve_main("/etc/manaradix/config.toml")
        assert exit_code == 1
        assert "already running" in capsys.readouterr().err

    def test_serve_main_runs_until_stopped(self) -> None:
        fake_loop = MagicMock()
        fake_glib = MagicMock(MainLoop=MagicMock(return_value=fake_loop))

        with patch("manaradix.utils.pkexec.is_root", return_value=True), \
             patch("dbus.service.BusName"), \
             patch("dbus.mainloop.glib.DBusGMainLoop"), \
             patch("dbus.SystemBus"), \
             patch.dict("sys.modules", {"gi.repository": MagicMock(
                 GLib=fake_glib)}), \
             patch("manaradix.module._init_services") as mock_init, \
             patch("manaradix.dbus.wire.ManaradixService") as mock_service:
            exit_code = wire.serve_main("/etc/manaradix/config.toml")
        assert exit_code == 0
        fake_loop.run.assert_called_once()
        mock_init.assert_called_once_with("/etc/manaradix/config.toml",
                                          ".snapshots")
        mock_service.assert_called_once()


class TestCmdStatus:
    def test_running_exits_zero(self, capsys) -> None:
        fake_client = MagicMock()
        fake_client.daemon_running.return_value = True
        with patch("manaradix.dbus.client.ManaradixClient",
                   return_value=fake_client):
            exit_code = _cmd_dbus_status(MagicMock(), MagicMock())
        assert exit_code == 0
        assert "running" in capsys.readouterr().out

    def test_not_running_exits_two(self, capsys) -> None:
        fake_client = MagicMock()
        fake_client.daemon_running.return_value = False
        with patch("manaradix.dbus.client.ManaradixClient",
                   return_value=fake_client):
            exit_code = _cmd_dbus_status(MagicMock(), MagicMock())
        assert exit_code == 2
        assert "starts automatically" in capsys.readouterr().out

    def test_bus_unavailable_treated_as_not_running(self, capsys) -> None:
        fake_client = MagicMock()
        fake_client.daemon_running.side_effect = RuntimeError("no bus")
        with patch("manaradix.dbus.client.ManaradixClient",
                   return_value=fake_client):
            exit_code = _cmd_dbus_status(MagicMock(), MagicMock())
        assert exit_code == 2


class TestMainFlow:
    """main() must consult needs_elevation for dbus subcommands."""

    def test_dbus_serve_dispatches_without_pkexec(self, capsys) -> None:
        captured = {}

        def fake_handler(ctx, args):
            captured["key"] = (args.command, args.dbus_command)
            return 0

        with patch("manaradix.module._init_services"), \
             patch("manaradix.module.needs_elevation",
                   return_value=False) as mock_needs, \
             patch("manaradix.module.reexec_with_pkexec") as mock_reexec, \
             patch("manaradix.module._COMMAND_DISPATCH",
                   {("dbus", "serve"): fake_handler}), \
             patch("sys.argv", ["manaradix", "dbus", "serve"]):
            with pytest.raises(SystemExit) as excinfo:
                main()
        assert captured["key"] == ("dbus", "serve")
        assert excinfo.value.code == 0
        mock_needs.assert_called_once_with("dbus", "serve")
        mock_reexec.assert_not_called()

    def test_dbus_status_dispatches_without_pkexec(self) -> None:
        # argparse fixes the dbus subcommand set; the safe-elevate default
        # for unknown dbus subcommands is covered in test_pkexec.
        captured = {}

        def fake_handler(ctx, args):
            captured["key"] = (args.command, args.dbus_command)
            return 0

        with patch("manaradix.module._init_services"), \
             patch("manaradix.module.needs_elevation",
                   return_value=False), \
             patch("manaradix.module.reexec_with_pkexec") as mock_reexec, \
             patch("manaradix.module._COMMAND_DISPATCH",
                   {("dbus", "status"): fake_handler}), \
             patch("sys.argv", ["manaradix", "dbus", "status"]):
            with pytest.raises(SystemExit) as excinfo:
                main()
        assert captured["key"] == ("dbus", "status")
        assert excinfo.value.code == 0
        mock_reexec.assert_not_called()
