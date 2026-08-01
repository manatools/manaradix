"""Manaradix — Overview page.

Dashboard showing filesystem health, recent snapshots, and active
policies at a glance.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from manaradix.ui.model import PageModel, Widget

if TYPE_CHECKING:
    from manaradix.module import AppContext

logger = logging.getLogger(__name__)


class OverviewPage:
    """Dashboard page showing filesystem status and recent activity."""

    page_id = "overview"
    title = "Overview"

    def __init__(self, app_context: AppContext) -> None:
        self.app = app_context

    # -- build_model --------------------------------------------------------

    def build_model(self):
        """Return a ``PageModel`` for the overview dashboard."""
        sections = [
            self._filesystem_section(),
            self._filesystems_section(),
            self._recent_snapshots_section(),
            self._active_policies_section(),
            self._quick_actions_section(),
        ]
        return PageModel(sections=[s for s in sections if s is not None])

    # -- Section builders ---------------------------------------------------

    def _filesystem_section(self) -> dict | None:
        """Filesystem usage summary."""
        try:
            usage = self.app.btrfs_ops.filesystem_usage()
        except Exception as exc:
            logger.warning("Could not read filesystem usage: %s", exc)
            usage = {}

        overall = usage.get("overall", {})
        if not overall:
            return None

        size = overall.get("device_size", 0)
        free = overall.get("free_estimated", 0)
        used = size - free if size else 0
        pct = f"{(used / size * 100):.1f}%" if size else "unknown"

        return {
            "title": "Filesystem",
            "fields": [
                Widget.label(
                    id="fs_summary",
                    text=f"Total: {self._fmt_bytes(size)}  |  "
                         f"Used: {self._fmt_bytes(used)} ({pct})  |  "
                         f"Free: {self._fmt_bytes(free)}",
                ),
            ],
        }

    def _filesystems_section(self) -> dict | None:
        """All btrfs filesystems with devices, sizes, and mount points."""
        try:
            filesystems = self.app.btrfs_ops.filesystem_show()
        except Exception as exc:
            logger.warning("Could not list btrfs filesystems: %s", exc)
            return None

        if not filesystems:
            return {
                "title": "Btrfs Filesystems",
                "fields": [
                    Widget.label(id="no_filesystems", text="No btrfs filesystems found."),
                ],
            }

        # Mount targets by device path and by uuid, so each filesystem row
        # can show where its devices are mounted.  The uuid index is a
        # fallback: btrfs may report /dev/mapper/name while findmnt shows
        # the underlying /dev/dm-N device.
        mounts_by_source: dict[str, list[str]] = {}
        mounts_by_uuid: dict[str, list[str]] = {}
        try:
            for mount in self.app.btrfs_ops.btrfs_mounts():
                mounts_by_source.setdefault(mount["source"], []).append(mount["target"])
                mounts_by_uuid.setdefault(mount["uuid"], []).append(mount["target"])
        except Exception as exc:
            logger.warning("Could not read btrfs mount points: %s", exc)

        rows = []
        for fs in filesystems:
            devices = ", ".join(d["path"] for d in fs["devices"]) or "-"
            total = sum(d["size"] for d in fs["devices"])
            mount_targets = [
                target
                for device in fs["devices"]
                for target in mounts_by_source.get(device["path"], [])
            ]
            if not mount_targets:
                mount_targets = mounts_by_uuid.get(fs["uuid"], [])
            rows.append({
                "Label": fs["label"] or "-",
                "UUID": fs["uuid"][:8],
                "Devices": devices,
                "Total": self._fmt_bytes(total) if total else "-",
                "Used": self._fmt_bytes(fs["fs_bytes_used"]) if fs["fs_bytes_used"] else "-",
                "Mount": ", ".join(dict.fromkeys(mount_targets)) or "-",
            })

        return {
            "title": "Btrfs Filesystems",
            "fields": [
                Widget.tree(id="btrfs_filesystems", rows=rows, selectable=False),
            ],
        }

    def _recent_snapshots_section(self) -> dict | None:
        """Last 5 snapshots."""
        try:
            snapshots = self.app.snapshot_manager.list_all()
        except Exception as exc:
            logger.warning("Could not list snapshots: %s", exc)
            return None

        if not snapshots:
            return {
                "title": "Recent Snapshots",
                "fields": [Widget.label(id="no_snapshots", text="No snapshots yet.")],
            }

        recent = sorted(snapshots, key=lambda s: s.date, reverse=True)[:5]
        return {
            "title": "Recent Snapshots",
            "fields": [
                Widget.tree(
                    id="recent_snapshots",
                    rows=[
                        {
                            "ID": s.id,
                            "Subvolume": s.subvolume,
                            "Type": s.type.value,
                            "Schedule": s.schedule or "-",
                        }
                        for s in recent
                    ],
                    selectable=False,
                ),
            ],
        }

    def _active_policies_section(self) -> dict | None:
        """Active snapshot policies."""
        try:
            policies = self.app.config_store.list_policies()
        except Exception as exc:
            logger.warning("Could not list policies: %s", exc)
            return None

        active = [p for p in policies if p.enabled]
        if not active:
            return {
                "title": "Active Policies",
                "fields": [Widget.label(id="no_policies", text="No active policies.")],
            }

        return {
            "title": "Active Policies",
            "fields": [
                Widget.tree(
                    id="active_policies",
                    rows=[
                        {
                            "Subvolume": p.subvolume,
                            "Schedules": ", ".join(p.active_schedules),
                            "Keep hourly": p.retention.keep_hourly,
                            "Keep daily": p.retention.keep_daily,
                        }
                        for p in active
                    ],
                    selectable=False,
                ),
            ],
        }

    def _quick_actions_section(self) -> dict:
        """Quick-action buttons."""
        return {
            "title": "Quick Actions",
            "fields": [
                Widget.button(
                    id="btn_create_snapshot",
                    label="Create Snapshot",
                    callback=self.on_create_snapshot,
                ),
                Widget.button(
                    id="btn_start_scrub",
                    label="Start Scrub",
                    callback=self.on_start_scrub,
                ),
                Widget.button(
                    id="btn_refresh",
                    label="Refresh",
                    callback=self.on_refresh,
                ),
            ],
        }

    # -- Callbacks ----------------------------------------------------------

    def on_create_snapshot(self) -> None:
        """Navigate to the Snapshots page (create tab)."""
        # The aui framework handles navigation; this is a placeholder
        # that the framework overrides with its own routing.
        pass

    def on_start_scrub(self) -> None:
        """Start a scrub of the default subvolume."""
        try:
            self.app.btrfs_ops.scrub_start()
        except Exception as exc:
            logger.error("Scrub failed: %s", exc)

    def on_refresh(self) -> None:
        """Request a UI refresh."""
        # The aui framework re-calls build_model() on refresh.
        pass

    # -- Helpers ------------------------------------------------------------

    @staticmethod
    def _fmt_bytes(value: int) -> str:
        """Format a byte count for human display."""
        if value <= 0:
            return "0 B"
        for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
            if abs(value) < 1024:
                return f"{value:.1f} {unit}"
            value /= 1024
        return f"{value:.1f} PiB"
