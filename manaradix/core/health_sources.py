"""Live data sources for the health checks (injectable).

:mod:`manaradix.core.health` stays pure; these thin wrappers bind it to
the real world — ``os.statvfs`` for space and the D-Bus daemon for
snapshots, device stats, and scrub status.  Each function takes its
data provider as a parameter or attribute so tests can substitute
fakes without touching the applet wiring.

The D-Bus connection life is managed by the caller (the tray worker
thread builds a fresh :class:`~manaradix.dbus.client.ManaradixClient`
per batch — dbus-python connections are not shared across threads).
"""

from __future__ import annotations

import os
from typing import Iterable


def statvfs_usages(mounts: Iterable[str]) -> list[dict]:
    """Space usage records for *mounts* via ``os.statvfs``.

    Returns one ``{"mount", "percent_used", "total_bytes", "free_bytes"}``
    record per mount that is reachable; unreadable or nonexistent
    mounts are skipped silently (the caller retries next cycle).
    """
    usages: list[dict] = []
    for mount in mounts:
        try:
            stats = os.statvfs(mount)
        except OSError:
            continue
        total = stats.f_blocks * stats.f_frsize
        free = stats.f_bavail * stats.f_frsize
        percent = (total - free) / total * 100 if total else 0.0
        usages.append(
            {
                "mount": mount,
                "percent_used": percent,
                "total_bytes": total,
                "free_bytes": free,
            }
        )
    return usages


def device_stats_via_client(client) -> list[dict]:
    """Per-device btrfs + SMART health records from the D-Bus daemon."""
    return list(client.get_device_stats())


def snapshots_via_client(client) -> list[dict]:
    """All snapshots as daemon-shaped dicts (``ListSnapshots``)."""
    return list(client.list_snapshots().get("snapshots", []))


def scrub_status_via_client(client) -> dict:
    """Scrub status of the root mount as a dict."""
    return dict(client.scrub_status("/"))
