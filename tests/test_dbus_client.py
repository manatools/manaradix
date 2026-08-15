"""Unit tests for the D-Bus client library (manaradix.dbus.client).

A fake bus object is injected via the constructor — no real bus, no
daemon.  Calls are asserted by inspecting the proxy method invocation
arguments.
"""

import re
from unittest.mock import MagicMock, patch

import pytest
from dbus.exceptions import DBusException

from manaradix.dbus.client import ManaradixClient
from manaradix.dbus.constants import DBUS_INTERFACE, DBUS_SERVICE_NAME, METHOD_ACTIONS
from manaradix.dbus.errors import (
    AuthorizationError,
    DaemonUnavailableError,
    InvalidArgumentError,
    NotFoundError,
    OperationError,
)


def make_client():
    """Client over a fake bus whose proxy returns canned values."""
    bus = MagicMock()
    proxy = MagicMock()
    bus.get_object.return_value = proxy
    client = ManaradixClient(bus=bus)
    return client, proxy


def _camel_to_snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


class TestMethodSymmetry:
    def test_every_method_action_has_client_method(self) -> None:
        client = ManaradixClient(bus=MagicMock())
        for method in METHOD_ACTIONS:
            assert hasattr(client, _camel_to_snake(method)), method

    def test_client_has_no_extra_public_methods(self) -> None:
        client = ManaradixClient(bus=MagicMock())
        public = {name for name in dir(client) if not name.startswith("_")}
        expected = {
            _camel_to_snake(m) for m in METHOD_ACTIONS
        } | {"daemon_running"}
        assert public == expected


class TestCallPlumbing:
    def test_uses_system_bus_and_interface(self) -> None:
        client, proxy = make_client()
        proxy.Ping.return_value = "0.1.1"
        assert client.ping() == "0.1.1"
        proxy.Ping.assert_called_once_with(
            dbus_interface=DBUS_INTERFACE, timeout=30.0
        )

    def test_creates_system_bus_lazily(self) -> None:
        # The default path builds its own SystemBus on first use; force
        # that connect to fail to prove it happens (environment-independent
        # — a live system daemon would otherwise answer the call).
        with patch("dbus.SystemBus", side_effect=Exception("no bus")), \
             pytest.raises(Exception):
            ManaradixClient().ping()
        # With an injected bus nothing is auto-created:
        client, proxy = make_client()
        proxy.BtrfsMounts.return_value = []
        assert client.btrfs_mounts() == []


class TestArgForwarding:
    def test_create_snapshot_full_args(self) -> None:
        client, proxy = make_client()
        proxy.CreateSnapshot.return_value = {"id": "x"}
        result = client.create_snapshot(
            subvolume="/home", description="d", read_only=False,
            snapshot_type="timed", schedule="daily", mount="/",
            dry_run=True,
        )
        assert result == {"id": "x"}
        proxy.CreateSnapshot.assert_called_once_with(
            "/home", "d", False, "timed", "daily", "/", True,
            dbus_interface=DBUS_INTERFACE, timeout=30.0,
        )

    def test_apply_policy_full_args(self) -> None:
        client, proxy = make_client()
        proxy.ApplyPolicy.return_value = ["unit"]
        result = client.apply_policy(
            subvolume="/", schedule_daily=True, schedule_boot=True,
            keep_daily=14, keep_boot=3, dry_run=True,
        )
        assert result == ["unit"]
        args = proxy.ApplyPolicy.call_args[0]
        assert args[0] == "/"
        assert args[1] is True      # enabled default
        assert args[2] is False     # hourly default
        assert args[3] is True      # daily
        assert args[8] == 14        # keep_daily
        assert args[12] is True     # dry_run

    def test_balance_start(self) -> None:
        client, proxy = make_client()
        proxy.BalanceStart.return_value = True
        assert client.balance_start("/", dusage=10, musage=20) is True
        proxy.BalanceStart.assert_called_once_with(
            "/", 10, 20, False, dbus_interface=DBUS_INTERFACE, timeout=30.0
        )

    def test_ping_returns_version(self) -> None:
        client, proxy = make_client()
        proxy.Ping.return_value = "0.1.1"
        assert client.ping() == "0.1.1"


class TestErrorMapping:
    def test_auth_required(self) -> None:
        client, proxy = make_client()
        proxy.CreateSnapshot.side_effect = DBusException(
            "denied", name="org.mageia.manaradix.Error.AuthRequired"
        )
        with pytest.raises(AuthorizationError):
            client.create_snapshot()

    def test_not_found(self) -> None:
        client, proxy = make_client()
        proxy.GetSnapshot.side_effect = DBusException(
            "missing", name="org.mageia.manaradix.Error.NotFound"
        )
        with pytest.raises(NotFoundError):
            client.get_snapshot("nope")

    def test_invalid_args(self) -> None:
        client, proxy = make_client()
        proxy.SetCompression.side_effect = DBusException(
            "bad", name="org.mageia.manaradix.Error.InvalidArgs"
        )
        with pytest.raises(InvalidArgumentError):
            client.set_compression("/", "bogus")

    def test_failed_operation(self) -> None:
        client, proxy = make_client()
        proxy.ScrubStart.side_effect = DBusException(
            "boom", name="org.mageia.manaradix.Error.Failed"
        )
        with pytest.raises(OperationError):
            client.scrub_start()

    def test_service_unknown_is_daemon_unavailable(self) -> None:
        client, proxy = make_client()
        proxy.Ping.side_effect = DBusException(
            "no such name", name="org.freedesktop.DBus.Error.ServiceUnknown"
        )
        with pytest.raises(DaemonUnavailableError):
            client.ping()


class TestDaemonRunning:
    def test_name_owner_check_not_activation(self) -> None:
        bus = MagicMock()
        bus.call_blocking.return_value = True
        client = ManaradixClient(bus=bus)
        assert client.daemon_running() is True
        # NameHasOwner on the bus, not a call to the service (which
        # would auto-activate it).
        bus.call_blocking.assert_called_once_with(
            "org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "NameHasOwner", "s", [DBUS_SERVICE_NAME],
        )
        bus.get_object.assert_not_called()

    def test_not_running(self) -> None:
        bus = MagicMock()
        bus.call_blocking.return_value = False
        assert ManaradixClient(bus=bus).daemon_running() is False

    def test_bus_error_treated_as_not_running(self) -> None:
        bus = MagicMock()
        bus.call_blocking.side_effect = RuntimeError("no bus")
        assert ManaradixClient(bus=bus).daemon_running() is False


class TestGetDeviceStatsClient:
    def test_no_args_and_list_return(self) -> None:
        client, proxy = make_client()
        proxy.GetDeviceStats.return_value = [{"device": "/dev/sda",
                                              "write_io_errs": 0}]
        result = client.get_device_stats()
        assert result[0]["device"] == "/dev/sda"
        proxy.GetDeviceStats.assert_called_once_with(
            dbus_interface=DBUS_INTERFACE, timeout=30.0
        )
