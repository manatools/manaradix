"""Unit tests for the polkit authorization helpers (manaradix.dbus.polkit).

The GI Polkit bindings are imported lazily inside the functions, so the
tests patch ``gi.repository.Polkit`` at the module boundary — no polkit
daemon and no session agent are needed.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from manaradix.dbus import polkit
from manaradix.dbus.errors import AuthorizationError


class TestCheckAuthorization:
    def test_authorized_does_not_raise(self) -> None:
        result = MagicMock()
        result.get_is_authorized.return_value = True
        fake_authority = MagicMock()
        fake_authority.check_authorization_sync.return_value = result

        fake_polkit = MagicMock()
        fake_polkit.Authority.get_sync.return_value = fake_authority
        fake_polkit.CheckAuthorizationFlags.ALLOW_USER_INTERACTION = 1

        with patch.dict("sys.modules", {
            "gi": MagicMock(),
            "gi.repository": MagicMock(Polkit=fake_polkit),
        }):
            polkit.check_authorization(1234, "org.mageia.manaradix.read")

        fake_authority.check_authorization_sync.assert_called_once()
        args, kwargs = fake_authority.check_authorization_sync.call_args
        subject, action_id = args[0], args[1]
        fake_polkit.UnixProcess.new.assert_called_once_with(1234)
        assert action_id == "org.mageia.manaradix.read"

    def test_denied_raises_with_action_id(self) -> None:
        result = MagicMock()
        result.get_is_authorized.return_value = False
        fake_polkit = MagicMock()
        fake_polkit.Authority.get_sync.return_value = MagicMock(
            check_authorization_sync=MagicMock(return_value=result)
        )
        fake_polkit.CheckAuthorizationFlags.ALLOW_USER_INTERACTION = 1

        with patch.dict("sys.modules", {
            "gi": MagicMock(),
            "gi.repository": MagicMock(Polkit=fake_polkit),
        }):
            with pytest.raises(AuthorizationError) as excinfo:
                polkit.check_authorization(
                    1234, "org.mageia.manaradix.manage-snapshots"
                )
        assert excinfo.value.action_id == "org.mageia.manaradix.manage-snapshots"

    def test_denied_checks_user_interaction_flag(self) -> None:
        result = MagicMock()
        result.get_is_authorized.return_value = False
        fake_authority = MagicMock()
        fake_authority.check_authorization_sync.return_value = result
        fake_polkit = MagicMock()
        fake_polkit.Authority.get_sync.return_value = fake_authority
        fake_polkit.CheckAuthorizationFlags.ALLOW_USER_INTERACTION = 1

        with patch.dict("sys.modules", {
            "gi": MagicMock(),
            "gi.repository": MagicMock(Polkit=fake_polkit),
        }):
            with pytest.raises(AuthorizationError):
                polkit.check_authorization(
                    1, "org.mageia.manaradix.read",
                    allow_user_interaction=False,
                )
        # The flags argument (index 3) must not include interaction.
        flags = fake_authority.check_authorization_sync.call_args[0][3]
        assert flags == 0


class TestSenderPid:
    def test_resolves_from_bus(self) -> None:
        connection = MagicMock()
        connection.call_blocking.return_value = 4242
        assert polkit.sender_pid(connection, ":1.7") == 4242
        connection.call_blocking.assert_called_once_with(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "GetConnectionUnixProcessID",
            "s", [":1.7"],
        )

    def test_no_sender_falls_back_to_own_pid(self) -> None:
        connection = MagicMock()
        assert polkit.sender_pid(connection, "") == os.getpid()

    def test_bus_error_falls_back_to_own_pid(self) -> None:
        connection = MagicMock()
        connection.call_blocking.side_effect = RuntimeError("bus gone")
        assert polkit.sender_pid(connection, ":1.7") == os.getpid()
