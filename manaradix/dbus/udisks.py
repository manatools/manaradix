"""UDisks2 SMART probe for the daemon's ``GetDeviceStats`` method.

SMART data comes from the system bus ``org.freedesktop.UDisks2`` Drive
interface: the ``SmartFailing`` / ``SmartUpdated`` / ``SmartTemperature``
properties plus the ``SmartGetAttributes`` method (normalized attribute
values — 100 means "perfect").  Drives without SMART, and systems
without a UDisks2 service, simply yield no entry — never an error, per
spec §16.4.1.

dbus-python only, the same stack as the daemon; the import is lazy so
``manaradix.dbus.service`` stays importable in environments without
python3-dbus (mirroring ``manaradix.dbus.polkit``).
"""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

_UDISKS_BUS_NAME = "org.freedesktop.UDisks2"
_UDISKS_OBJECT_MANAGER = "/org/freedesktop/UDisks2"
_DRIVE_IFACE = "org.freedesktop.UDisks2.Drive"

#: Normalized SMART attribute IDs surfaced to the health engine.
ATTR_REALLOCATED = 5
ATTR_PENDING = 197
ATTR_UNCORRECTABLE = 198

#: Strip partition suffixes: /dev/sda2 -> /dev/sda, /dev/nvme0n1p2 -> /dev/nvme0n1.
_PARTITION_RE = re.compile(r"p?\d+$")


def whole_disk(device: str) -> str:
    """The whole-disk device backing a partition path."""
    return _PARTITION_RE.sub("", device)


def smart_probe_all(
    devices: list[str], bus=None
) -> dict[str, dict]:
    """SMART reports for the whole disks backing *devices*.

    Returns ``{whole_disk: report}`` where each report has ``failing``
    (bool), ``temperature`` (°C, or None) and the normalized values of
    the reallocated/pending/uncorrectable attributes.  Missing drives,
    missing SMART, and a missing UDisks2 service all yield an empty or
    partial map.
    """
    wanted = {whole_disk(device) for device in devices}
    if not wanted:
        return {}
    try:
        import dbus  # lazy: the daemon imports this module at startup

        if bus is None:
            bus = dbus.SystemBus()
        manager = bus.get_object(
            _UDISKS_BUS_NAME, _UDISKS_OBJECT_MANAGER
        )
        objects = manager.GetManagedObjects(
            dbus_interface="org.freedesktop.DBus.ObjectManager"
        )
    except Exception as exc:  # no UDisks2 on the bus, or a dbus failure
        logger.debug("UDisks2 SMART probe unavailable: %s", exc)
        return {}

    reports: dict[str, dict] = {}
    for path, interfaces in objects.items():
        drive = interfaces.get(_DRIVE_IFACE)
        if drive is None:
            continue
        device_file = str(drive.get("DeviceFile", ""))
        if device_file not in wanted:
            continue
        report = _probe_drive(bus, path, drive)
        if report is not None:
            reports[device_file] = report
    return reports


def _probe_drive(bus, path: str, properties: dict) -> Optional[dict]:
    """SMART report for one UDisks2 drive object, or None."""
    try:
        import dbus

        updated_ms = int(properties.get("SmartUpdated", 0))
        if updated_ms <= 0:
            # SMART never returned data for this drive.
            return None
        failing = bool(properties.get("SmartFailing", False))
        temperature = properties.get("SmartTemperature")
        attributes = {}
        drive_obj = bus.get_object(_UDISKS_BUS_NAME, str(path))
        drive_iface = dbus.Interface(drive_obj, _DRIVE_IFACE)
        for attr_id, _flags, value in drive_iface.SmartGetAttributes():
            attr_id = int(attr_id)
            if attr_id in (ATTR_REALLOCATED, ATTR_PENDING, ATTR_UNCORRECTABLE):
                attributes[attr_id] = int(value)
    except Exception as exc:
        logger.debug("SMART probe failed for %s: %s", path, exc)
        return None
    return {
        "failing": failing,
        "temperature": float(temperature) if temperature > 0 else None,
        "reallocated_sectors": attributes.get(ATTR_REALLOCATED, 0),
        "pending_sectors": attributes.get(ATTR_PENDING, 0),
        "uncorrectable_errors": attributes.get(ATTR_UNCORRECTABLE, 0),
    }
