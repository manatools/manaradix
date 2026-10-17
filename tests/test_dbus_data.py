"""Data-file sanity tests for the D-Bus service.

These parse the shipped unit/activation/policy files and cross-check
them against the code constants, so the three sources of truth cannot
drift apart silently.
"""

import configparser
from pathlib import Path
from xml.etree import ElementTree

from manaradix.dbus.constants import (
    ACTION_MANAGE_FILESYSTEM,
    ACTION_MANAGE_POLICIES,
    ACTION_MANAGE_SNAPSHOTS,
    ACTION_READ,
    DBUS_SERVICE_NAME,
    METHOD_ACTIONS,
)

_SYSTEMD_UNIT = Path("data/systemd/manaradix-dbus.service")
_SYSTEM_SERVICES = Path("data/dbus/org.mageia.manaradix.service")
_BUS_CONF = Path("data/dbus/org.mageia.manaradix.conf")
_POLKIT_POLICY = Path("data/polkit/org.mageia.manaradix.policy")


class TestSystemdUnit:
    def _parse(self) -> configparser.ConfigParser:
        config = configparser.ConfigParser()
        config.read(_SYSTEMD_UNIT)
        return config

    def test_type_simple_without_busname(self) -> None:
        # Type=simple: the bus daemon completes activation itself by
        # watching the name, so Type=dbus start-ordering adds nothing.
        service = self._parse()["Service"]
        assert service["Type"] == "simple"
        assert service.get("BusName") is None

    def test_exec_start_runs_serve(self) -> None:
        service = self._parse()["Service"]
        assert service["ExecStart"] == "/usr/bin/manaradix dbus serve"

    def test_runs_as_root(self) -> None:
        assert self._parse()["Service"]["User"] == "root"

    def test_no_install_section(self) -> None:
        # Auto-activation only: never systemctl enable'd at boot.
        assert not self._parse().has_section("Install")

    def test_hardening_present(self) -> None:
        service = self._parse()["Service"]
        assert service["NoNewPrivileges"] == "yes"

    def test_protect_system_full_with_carveouts(self) -> None:
        service = self._parse()["Service"]
        assert service["ProtectSystem"] == "full"
        assert service["ProtectHome"] == "yes"
        # Every daemon write location under the read-only zones must be
        # carved back in: config, generated units, GRUB script, /boot
        # output, and the usage cache database.  Snapshot dirs and /mnt
        # stay writable automatically.
        for path in (
            "/etc/manaradix",
            "/etc/systemd/system",
            "/etc/grub.d",
            "/boot",
            "/var/lib/manaradix",
        ):
            assert path in service["ReadWritePaths"], f"missing carve-out {path}"

    def test_no_private_devices(self) -> None:
        # Browsing encrypted-root snapshots needs /dev/mapper nodes.
        service = self._parse()["Service"]
        assert "PrivateDevices" not in service

    def test_strict_protection_not_used(self) -> None:
        # btrfs restore/balance/scrub/defrag need a writable root mount.
        assert self._parse()["Service"]["ProtectSystem"] != "strict"


class TestSystemServicesFile:
    def _parse(self) -> configparser.ConfigParser:
        config = configparser.ConfigParser()
        config.read(_SYSTEM_SERVICES)
        return config

    def test_activation_descriptor(self) -> None:
        service = self._parse()["D-BUS Service"]
        assert service["Name"] == DBUS_SERVICE_NAME
        assert service["Exec"] == "/usr/bin/manaradix dbus serve"
        assert service["User"] == "root"
        assert service["SystemdService"] == "manaradix-dbus.service"


class TestBusConf:
    def _parse(self) -> ElementTree.Element:
        return ElementTree.parse(_BUS_CONF).getroot()

    def test_root_may_own_the_name(self) -> None:
        root = self._parse()
        for policy in root.findall("policy"):
            if policy.get("user") == "root":
                allows = {a.get("own") for a in policy.findall("allow")}
                assert DBUS_SERVICE_NAME in allows

    def test_default_policy_allows_interface(self) -> None:
        root = self._parse()
        for policy in root.findall("policy"):
            if policy.get("context") == "default":
                allows = [
                    a.get("send_interface")
                    for a in policy.findall("allow")
                    if a.get("send_destination") == DBUS_SERVICE_NAME
                ]
                assert DBUS_SERVICE_NAME in allows

    def test_valid_xml(self) -> None:
        root = self._parse()
        assert root.tag == "busconfig"


class TestCrossFileConsistency:
    def test_actions_match_polkit_policy(self) -> None:
        polkit_root = ElementTree.parse(_POLKIT_POLICY).getroot()
        policy_actions = {a.get("id") for a in polkit_root.findall("action")}
        assert policy_actions == {
            ACTION_READ,
            ACTION_MANAGE_SNAPSHOTS,
            ACTION_MANAGE_POLICIES,
            ACTION_MANAGE_FILESYSTEM,
        }
        assert set(METHOD_ACTIONS.values()) == policy_actions

    def test_polkit_policy_lists_all_methods(self) -> None:
        # The polkit policy ships per-action descriptions; the daemon
        # must not reference actions the policy does not declare.
        text = _POLKIT_POLICY.read_text()
        for action in set(METHOD_ACTIONS.values()):
            assert action in text
