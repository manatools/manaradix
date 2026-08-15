"""Checker registry: which health checks run, how often, and with what.

The registry maps the four checks (space / devices / retention / scrub)
to the pure findings functions in :mod:`manaradix.core.health`, bound
to live sources.  Each :class:`CheckSpec` carries its interval and
enabled flag from the ``[tray]`` config; the runner receives a
:class:`TraySources` built per batch by the applet's worker thread
(each batch uses a fresh D-Bus client — dbus-python connections are
not shared across threads).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from manaradix.core.config_store import ConfigStore
from manaradix.core.health import (
    compute_retention_suggestions,
    device_findings,
    infer_profile,
    scrub_findings,
    space_findings,
)
from manaradix.core.health_sources import (
    device_stats_via_client,
    scrub_status_via_client,
    snapshots_via_client,
    statvfs_usages,
)

logger = logging.getLogger(__name__)


class TraySources:
    """Live data sources bound to one D-Bus client (one per batch)."""

    def __init__(self, client) -> None:
        self._client = client

    def mounts(self) -> list[dict]:
        """The mounted btrfs filesystems."""
        return list(self._client.btrfs_mounts())

    def devices(self) -> list[dict]:
        """Per-device btrfs + SMART health records."""
        return device_stats_via_client(self._client)

    def snapshots(self) -> list[dict]:
        """All snapshots as daemon-shaped dicts."""
        return snapshots_via_client(self._client)

    def scrub(self) -> dict:
        """Scrub status of the root mount."""
        return scrub_status_via_client(self._client)


@dataclass
class CheckSpec:
    """One health check: timing, enablement, and its runner.

    Attributes:
        name: ``"space"``, ``"devices"``, ``"retention"`` or ``"scrub"``.
        interval_min: How often the check runs (minutes).
        enabled: Whether the check is active.
        run: Runs the check; receives the batch's :class:`TraySources`
            and returns findings.
    """

    name: str
    interval_min: int
    enabled: bool
    run: Callable[[TraySources], list]


def resolve_profile(store: ConfigStore) -> str:
    """The retention-suggestion flavor for this system.

    ``[global] applied_profile`` wins (recorded by ``setup apply``);
    otherwise infer from the first policy's retention numbers;
    desktop is the fallback.
    """
    profile = store.applied_profile
    if profile:
        return profile
    for policy in store.list_policies():
        return infer_profile(policy)
    return "desktop"


def _mount_targets(sources: TraySources) -> list[str]:
    return [mount["target"] for mount in sources.mounts()]


def build_check_registry(store: ConfigStore) -> list[CheckSpec]:
    """The enabled/configured checks for the applet.

    Args:
        store: The config store (``[tray]`` section + policies).

    Returns:
        The four checks in display order; each runner catches its own
        exceptions and degrades to no findings (a broken check must
        never crash the applet or spam notifications).
    """
    tray = store.tray
    profile = resolve_profile(store)

    def run_space(sources: TraySources) -> list:
        try:
            usages = statvfs_usages(_mount_targets(sources))
        except Exception as exc:
            logger.warning("space check failed: %s", exc)
            return []
        return space_findings(
            usages, warn=tray.space_warn_pct,
            critical=tray.space_critical_pct,
        )

    def run_devices(sources: TraySources) -> list:
        try:
            return device_findings(sources.devices())
        except Exception as exc:
            logger.warning("device check failed: %s", exc)
            return []

    def run_retention(sources: TraySources) -> list:
        try:
            snapshots = sources.snapshots()
            findings: list = []
            free_pct = None
            try:
                usages = statvfs_usages(_mount_targets(sources))
                if usages:
                    free_pct = min(
                        (1 - u["percent_used"] / 100) * 100 for u in usages
                    )
            except Exception:
                pass
            for policy in store.list_policies():
                if not policy.enabled:
                    continue
                findings.extend(
                    compute_retention_suggestions(
                        snapshots, policy, profile, free_pct=free_pct
                    )
                )
            return findings
        except Exception as exc:
            logger.warning("retention check failed: %s", exc)
            return []

    def run_scrub(sources: TraySources) -> list:
        try:
            return scrub_findings(sources.scrub(), profile=profile)
        except Exception as exc:
            logger.warning("scrub check failed: %s", exc)
            return []

    return [
        CheckSpec("space", tray.space_interval_min, tray.space_enabled,
                  run_space),
        CheckSpec("devices", tray.device_interval_min, tray.device_enabled,
                  run_devices),
        CheckSpec("retention", tray.retention_interval_min,
                  tray.retention_enabled, run_retention),
        CheckSpec("scrub", tray.scrub_interval_min, tray.scrub_enabled,
                  run_scrub),
    ]
