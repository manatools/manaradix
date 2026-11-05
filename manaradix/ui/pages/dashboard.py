"""Manaradix — Dashboard page.

Disk and filesystem usage statistics (per physical disk, per btrfs
filesystem, and per internal Data/Metadata/System section), snapshot
usage, recent snapshots, active policies, and quick actions.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from datetime import datetime
from typing import TYPE_CHECKING

from manaradix.core.activity_status import ActivityStatus
from manaradix.core import usage_store
from manaradix.i18n import _
from manaradix.ui.model import PageModel, Widget
from manaradix.ui.pages.snapshots import SnapshotsPage

if TYPE_CHECKING:
    from manaradix.module import AppContext

logger = logging.getLogger(__name__)

#: With at most this many disks/filesystems every row fits the dialog
#: directly; beyond it a dropdown selects which one to display, keeping
#: the view compact.
_MAX_ROWS_DIRECT = 3

# Maps a btrfs device path to its physical-disk kernel name.  Partition
# suffixes differ by bus: nvme0n1p3 -> nvme0n1, sda2 -> sda, mmcblk0p1 ->
# mmcblk0; whole disks (sda) pass through.  /dev/mapper/*, /dev/loop* and
# other non-disk devices do not match and become their own group keyed by
# the full path (resolving a dm-crypt parent would need lsblk).
_DISK_DEVICE_RE = re.compile(
    r"^/dev/(?P<disk>(?:nvme\d+n\d+|mmcblk\d+|sd[a-z]+|vd[a-z]+|hd[a-z]+))"
    r"(?:p?\d+)?$"
)


def _disk_key(device_path: str) -> str:
    """Return the physical-disk kernel name for a device path."""
    m = _DISK_DEVICE_RE.match(device_path)
    return m.group("disk") if m else device_path


class DashboardPage:
    """Dashboard page showing filesystem status and recent activity."""

    page_id = "dashboard"
    title = _("Dashboard")

    def __init__(self, app_context: AppContext) -> None:
        self.app = app_context
        # Dropdown selections persist across re-renders (same pattern as
        # SnapshotsPage._selected_snapshot_id).
        self._selected_disk: str | None = None
        self._selected_fs: str | None = None
        self._selected_internal_fs: str | None = None

    # -- build_model --------------------------------------------------------

    def build_model(self):
        """Return a ``PageModel`` for the dashboard.

        Row/column grid: disks and filesystems span the full width, then
        a 50/50 row (internal usage | snapshot usage + recent snapshots),
        then a 50/50 row (active policies | quick actions).
        """
        # Cache per-mount usage for this build (one subprocess per unique
        # filesystem) and fetch snapshots/filesystems once — each shared
        # by several sections.
        self._usage_cache: dict[str, dict] = {}
        self._snapshots = self._list_snapshots()
        self._filesystems = self._list_filesystems()
        sections = [
            self._quick_actions_section(),
            self._disks_section(),
            self._filesystems_section(),
            self._internal_stats_section(),
            self._snapshot_usage_section(),
            self._recent_snapshots_section(),
            self._active_policies_section(),
            self._activity_status_section(),
        ]
        return PageModel(
            sections=[s for s in sections if s is not None],
            columns=2,
            rows=[
                ["quick"],
                ["disks"],
                ["filesystems"],
                ["internal", ["snapshot_usage", "recent"]],
                ["policies", "activity"],
            ],
        )

    # -- Section builders ---------------------------------------------------

    def _disks_section(self) -> dict | None:
        """Physical disks (grouped btrfs devices) with total/free/used."""
        totals: dict[str, dict] = {}
        for fs in self._filesystems:
            for dev in fs["devices"]:
                entry = totals.setdefault(_disk_key(dev["path"]), {"total": 0, "used": 0})
                entry["total"] += dev["size"]
                entry["used"] += dev["used"]

        if not totals:
            return {
                "title": _("Physical Disks"),
                "id": "disks",
                "column": 0,
                "fields": [Widget.label(id="no_disks", text=_("No physical disks found."))],
            }

        disks = sorted(totals.items(), key=lambda kv: (-kv[1]["total"], kv[0]))

        def row_for(key: str) -> dict:
            t = totals[key]
            free = t["total"] - t["used"] if t["total"] else 0
            pct = f"{(t['used'] / t['total'] * 100):.1f}%" if t["total"] else "-"
            return {
                "Disk": os.path.basename(key),
                "Total": self._fmt_bytes(t["total"]),
                "Used": self._fmt_bytes(t["used"]),
                "Free": self._fmt_bytes(free),
                "Used %": pct,
            }

        if len(disks) > _MAX_ROWS_DIRECT:
            labels = [os.path.basename(key) for key, _ in disks]
            selected = self._selected_disk if self._selected_disk in totals else disks[0][0]
            return {
                "title": _("Physical Disks"),
                "id": "disks",
                "column": 0,
                "fields": [
                    Widget.dropdown(
                        id="disk_selector",
                        label=_("Physical Disk"),
                        options=labels,
                        selected=os.path.basename(selected),
                        on_change=self.on_disk_selected,
                    ),
                    Widget.tree(id="disks", rows=[row_for(selected)], selectable=False),
                ],
            }

        return {
            "title": _("Physical Disks"),
            "id": "disks",
            "column": 0,
            "fields": [
                Widget.tree(id="disks", rows=[row_for(key) for key, _ in disks], selectable=False),
            ],
        }

    def _filesystems_section(self) -> dict | None:
        """All btrfs filesystems with devices, sizes, and usage percent."""
        if not self._filesystems:
            return {
                "title": _("Btrfs Filesystems"),
                "id": "filesystems",
                "column": 1,
                "fields": [
                    Widget.label(id="no_filesystems", text=_("No btrfs filesystems found.")),
                ],
            }

        # Mount targets by device path and by uuid, so each filesystem row
        # can show where its devices are mounted.  The uuid index is a
        # fallback: btrfs may report /dev/mapper/name while findmnt shows
        # the underlying /dev/dm-N device.
        mounts_by_source: dict[str, list[dict]] = {}
        mounts_by_uuid: dict[str, list[dict]] = {}
        try:
            for mount in self.app.btrfs_ops.btrfs_mounts():
                mounts_by_source.setdefault(mount["source"], []).append(mount)
                mounts_by_uuid.setdefault(mount["uuid"], []).append(mount)
        except Exception as exc:
            logger.warning("Could not read btrfs mount points: %s", exc)

        def fs_label(fs: dict) -> str:
            return fs["label"] or fs["uuid"][:8]

        def row_for(fs: dict) -> dict:
            devices = ", ".join(d["path"] for d in fs["devices"]) or "-"
            total = sum(d["size"] for d in fs["devices"])
            used = fs["fs_bytes_used"]
            free = total - used if total else 0
            pct = f"{(used / total * 100):.1f}%" if total else "-"
            fs_mounts = [
                mount
                for device in fs["devices"]
                for mount in mounts_by_source.get(device["path"], [])
            ]
            if not fs_mounts:
                fs_mounts = mounts_by_uuid.get(fs["uuid"], [])
            mount_targets = list(dict.fromkeys(m["target"] for m in fs_mounts))
            return {
                "Label": fs["label"] or "-",
                "Devices": devices,
                "Total": self._fmt_bytes(total) if total else "-",
                "Used": self._fmt_bytes(used) if used else "-",
                "Free": self._fmt_bytes(free) if total else "-",
                "Used %": pct,
                "Compression": self._compression(fs_mounts),
                "Subvolumes": self._subvolume_count(mount_targets),
                "Mount": ", ".join(mount_targets) or "-",
            }

        if len(self._filesystems) > _MAX_ROWS_DIRECT:
            options = [fs_label(fs) for fs in self._filesystems]
            selected_fs = next(
                (fs for fs in self._filesystems if fs["uuid"] == self._selected_fs),
                self._filesystems[0],
            )
            return {
                "title": _("Btrfs Filesystems"),
                "id": "filesystems",
                "column": 1,
                "fields": [
                    Widget.dropdown(
                        id="fs_selector",
                        label=_("Filesystem"),
                        options=options,
                        selected=fs_label(selected_fs),
                        on_change=self.on_fs_selected,
                    ),
                    Widget.tree(
                        id="btrfs_filesystems",
                        rows=[row_for(selected_fs)],
                        selectable=False,
                    ),
                ],
            }

        return {
            "title": _("Btrfs Filesystems"),
            "id": "filesystems",
            "column": 1,
            "fields": [
                Widget.tree(
                    id="btrfs_filesystems",
                    rows=[row_for(fs) for fs in self._filesystems],
                    selectable=False,
                ),
            ],
        }

    def _internal_stats_section(self) -> dict | None:
        """Data/Metadata/System space breakdown per btrfs filesystem.

        Rendered as a donut chart: used bytes per section, the allocated
        but unused remainder of the chunks, and the unallocated device
        space.  With more than one filesystem a dropdown selects which
        one the chart shows (half-width cell, one chart at a time).
        """
        try:
            mounts = self.app.btrfs_ops.btrfs_mounts()
        except Exception as exc:
            logger.warning("Could not read btrfs mount points: %s", exc)
            mounts = []
        if not mounts:
            mounts = [{"target": "/", "uuid": ""}]

        # One group per unique filesystem: subvolume mounts of the same fs
        # share a uuid — keep the shortest target (the most top-level
        # mount, e.g. / over /var) so filesystems are not listed twice.
        by_uuid: dict[str, dict] = {}
        for m in mounts:
            uuid = m.get("uuid", "")
            if uuid not in by_uuid or len(m["target"]) < len(by_uuid[uuid]["target"]):
                by_uuid[uuid] = m
        groups = sorted(by_uuid.values(), key=lambda m: m["target"])

        charts: dict[str, dict] = {}
        for mount in groups:
            chart = self._internal_usage_chart(mount["target"])
            if chart is not None:
                charts[mount["target"]] = chart
        if not charts:
            return None

        options = list(charts)
        fields: list[dict] = []
        selected = options[0]
        if len(options) > 1:
            if self._selected_internal_fs in charts:
                selected = self._selected_internal_fs
            fields.append(
                Widget.dropdown(
                    id="internal_fs_selector",
                    label=_("Filesystem"),
                    options=options,
                    selected=selected,
                    on_change=self.on_internal_fs_selected,
                )
            )
        fields.append(charts[selected])
        return {
            "title": _("Internal Filesystem Usage"),
            "id": "internal",
            "column": 0,
            "fields": fields,
        }

    def _internal_usage_chart(self, mount: str) -> dict | None:
        """Donut chart descriptor of *mount*'s space, or None without data."""
        overall = self._usage(mount).get("overall", {})
        device_size = overall.get("device_size")
        if not device_size:
            return None
        sections = [
            (overall[key], label, color)
            for key, label, color in (
                ("data", _("Data"), "#0072b2"),
                ("metadata", _("Metadata"), "#e69f00"),
                ("system", _("System"), "#cc79a7"),
            )
            if overall.get(key)
        ]
        if not sections:
            return None

        slices = [
            {"label": label, "value": s["used"], "color": color}
            for s, label, color in sections
        ]
        allocated = sum(s["size"] for s, _label, _color in sections)
        used = sum(s["used"] for s, _label, _color in sections)
        slices.append({
            "label": _("Allocated, unused"),
            "value": max(allocated - used, 0),
            "color": "#56b4e9",
        })
        # The parsed raw figure is exact (DUP/RAID chunks count twice on
        # the device); the logical estimate is a fallback for old output.
        unallocated = overall.get("unallocated", max(device_size - allocated, 0))
        slices.append({
            "label": _("Unallocated"),
            "value": unallocated,
            "color": "#999999",
        })
        return Widget.pie(
            id="internal_stats",
            label=mount,
            slices=slices,
            # Bare percentage: "x% used" no longer fits the hole once
            # the chart shares a half-width cell with the legend.
            center_text=f"{used / device_size * 100:.1f}%",
            donut_ratio=0.55,
            value_format=self._fmt_bytes,
        )

    def _snapshot_usage_section(self) -> dict:
        """Snapshot count and disk usage from the SQLite store.

        The usage data is collected in the background by the systemd
        timer (or manually via `usage collect`), so we never block the
        UI.  We show the last update timestamp and provide a button to
        trigger a fresh collection if needed.
        """
        count = len(self._snapshots)
        reader = usage_store.UsageReader(usage_store.DEFAULT_DB_PATH)
        status = reader.status()
        # Get aggregate for root mount (or None if not available)
        aggregates = reader.aggregates()
        aggregate = next((a for a in aggregates if a["mount"] == "/"), None)

        # Build the status line
        if status["state"] == "running":
            status_text = _("Collecting usage data...")
        elif status["state"] == "ok":
            status_text = _("Last updated: %s") % self._fmt_timestamp(
                status["last_finished"]
            )
        elif status["state"] == "failed":
            status_text = _("Last collection failed: %s") % status["last_error"]
        else:
            status_text = _("No usage data collected yet")

        # Build the data rows
        if aggregate:
            total = aggregate["total_bytes"]
            exclusive = aggregate["exclusive_bytes"]
            device_size = aggregate["device_size_bytes"]
            pct = (exclusive / device_size * 100) if device_size > 0 else 0
            rows = [{
                "Snapshots": count,
                "Total": self._fmt_bytes(total),
                "Exclusive": self._fmt_bytes(exclusive),
                "% of device": f"{pct:.1f}%",
            }]
        else:
            rows = [{
                "Snapshots": count,
                "Total": "-",
                "Exclusive": "-",
                "% of device": "-",
            }]

        return {
            "title": _("Snapshot Usage"),
            "id": "snapshot_usage",
            "column": 1,
            "fields": [
                Widget.label(text=status_text),
                Widget.tree(
                    id="snapshot_usage",
                    rows=rows,
                    selectable=False,
                ),
                Widget.button(
                    id="btn_force_update_usage",
                    label=_("Force Usage Data Update"),
                    callback=self.on_force_update_usage,
                ),
            ],
        }

    def _recent_snapshots_section(self) -> dict | None:
        """Last 5 snapshots."""
        if not self._snapshots:
            return {
                "title": _("Recent Snapshots"),
                "id": "recent",
                "column": 1,
                "fields": [Widget.label(id="no_snapshots", text=_("No snapshots yet."))],
            }

        recent = sorted(self._snapshots, key=lambda s: s.date, reverse=True)[:5]
        return {
            "title": _("Recent Snapshots"),
            "id": "recent",
            "column": 1,
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
                "title": _("Active Policies"),
                "id": "policies",
                "column": 1,
                "fields": [Widget.label(id="no_policies", text=_("No active policies."))],
            }

        return {
            "title": _("Active Policies"),
            "id": "policies",
            "column": 1,
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

    def _activity_status_section(self) -> dict | None:
        """Per-activity live status: systemd state + last run evidence.

        ``ActivityStatus`` is pure-read: systemctl state, the run-record
        file, and snapshot sidecars.  Anything unreadable degrades to
        fewer rows or no section, never a crash.
        """
        try:
            statuses = ActivityStatus(
                self.app.policy_engine,
                self.app.snapshot_manager,
                self.app.config_store,
            ).statuses()
        except Exception as exc:
            logger.warning("Could not read activity status: %s", exc)
            return None

        if not statuses:
            return None

        return {
            "title": _("Activity Status"),
            "id": "activity",
            "fields": [
                Widget.tree(
                    id="activity_status",
                    rows=[
                        {
                            "Subvolume": r["subvolume"],
                            "Schedule": r["schedule"],
                            "Status": r["state"],
                            "Last run": self._fmt_run(
                                r["last_run_ts"], r["last_run_result"]
                            ),
                        }
                        for r in statuses
                    ],
                    selectable=False,
                ),
            ],
        }

    @staticmethod
    def _fmt_run(ts: str | None, result: str | None) -> str:
        """Format a last-run timestamp, marking non-successful results."""
        if not ts:
            return _("never")
        if result not in (None, "success"):
            return _("%s (%s)") % (ts, result)
        return ts

    def _quick_actions_section(self) -> dict:
        """Quick-action buttons."""
        return {
            "title": _("Quick Actions"),
            "id": "quick",
            "column": 0,
            "horizontal": True,
            "fields": [
                Widget.button(
                    id="btn_create_snapshot",
                    label=_("Create Snapshot"),
                    callback=self.on_create_snapshot,
                ),
                Widget.button(
                    id="btn_start_scrub",
                    label=_("Start Scrub"),
                    callback=self.on_start_scrub,
                ),
                Widget.button(
                    id="btn_refresh",
                    label=_("Refresh"),
                    callback=self.on_refresh,
                ),
            ],
        }

    # -- Callbacks ----------------------------------------------------------

    def on_disk_selected(self, value: str) -> None:
        """Remember the selected physical disk across re-renders."""
        self._selected_disk = value or None

    def on_fs_selected(self, value: str) -> None:
        """Remember the selected filesystem (by uuid) across re-renders."""
        label = value or ""
        for fs in self._filesystems:
            if (fs["label"] or fs["uuid"][:8]) == label:
                self._selected_fs = fs["uuid"]
                return
        self._selected_fs = None

    def on_internal_fs_selected(self, value: str) -> None:
        """Remember the selected filesystem mount across re-renders."""
        self._selected_internal_fs = value or None

    def on_create_snapshot(self) -> None:
        """Navigate to the Snapshots page (create tab)."""
        # The renderer hands itself to every page at startup, so the
        # quick action can jump straight to the snapshot manager.
        renderer = getattr(self, "renderer", None)
        if renderer is not None:
            renderer.navigate(SnapshotsPage.page_id)

    def on_start_scrub(self) -> None:
        """Start a scrub of the default subvolume."""
        try:
            self.app.btrfs_ops.scrub_start()
        except Exception as exc:
            logger.error("Scrub failed: %s", exc)

    def on_force_update_usage(self) -> None:
        """Trigger a background usage collection.

        Spawns a detached ``manaradix usage collect`` subprocess so the
        slow ``btrfs filesystem du`` call never blocks the GUI.  Falls
        back to ``systemctl start manaradix-usage.service`` (the packaged
        oneshot) when systemd is reachable; when neither works the error
        is logged and the next section rebuild will show the failure
        state.
        """
        try:
            subprocess.Popen(
                ["systemctl", "start", "--no-block", "manaradix-usage.service"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        except Exception as exc:
            logger.debug("systemctl start failed (%s); trying direct CLI", exc)
        try:
            subprocess.Popen(
                ["manaradix", "usage", "collect"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as exc:
            logger.error("Could not start usage collection: %s", exc)

    def on_refresh(self) -> None:
        """Request a UI refresh."""
        # The aui framework re-calls build_model() on refresh.
        pass

    # -- Helpers ------------------------------------------------------------

    def _list_snapshots(self) -> list:
        try:
            return self.app.snapshot_manager.list_all()
        except Exception as exc:
            logger.warning("Could not list snapshots: %s", exc)
            return []

    def _list_filesystems(self) -> list:
        try:
            return self.app.btrfs_ops.filesystem_show()
        except Exception as exc:
            logger.warning("Could not list btrfs filesystems: %s", exc)
            return []

    def _compression(self, mounts: list[dict]) -> str:
        """Compression in use on a filesystem, for display.

        The ``compress=`` mount option wins (it applies to every write
        through that mount); otherwise the ``compression`` property of
        the first mount's root directory (inherited by new files).
        Unmounted filesystems show ``-``.
        """
        if not mounts:
            return "-"
        for mount in mounts:
            if mount.get("compression"):
                return mount["compression"]
        try:
            prop = self.app.btrfs_ops.get_compression(mounts[0]["target"])
        except Exception as exc:
            logger.warning("Could not read compression of %s: %s", mounts[0]["target"], exc)
            prop = None
        if isinstance(prop, str) and prop:
            return _("%s (property)") % prop
        return _("off")

    def _subvolume_count(self, mount_targets: list[str]) -> str:
        """Subvolume count of a mounted filesystem, for display.

        Plain subvolumes first, snapshots in parentheses (snapshots are
        subvolumes too, but would otherwise swamp the number).  ``-``
        when unmounted or unreadable.
        """
        if not mount_targets:
            return "-"
        try:
            counts = self.app.btrfs_ops.subvolume_counts(mount_targets[0])
        except Exception as exc:
            logger.warning("Could not count subvolumes of %s: %s", mount_targets[0], exc)
            return "-"
        if not isinstance(counts, dict):
            return "-"
        if counts["snapshots"]:
            return _("%d (+%d snapshots)") % (counts["subvolumes"], counts["snapshots"])
        return str(counts["subvolumes"])

    def _usage(self, mount: str) -> dict:
        """Memoized ``filesystem_usage()`` per mount (one call per fs)."""
        if mount not in self._usage_cache:
            try:
                self._usage_cache[mount] = self.app.btrfs_ops.filesystem_usage(mount)
            except Exception as exc:
                logger.warning("Could not read filesystem usage for %s: %s", mount, exc)
                self._usage_cache[mount] = {}
        return self._usage_cache[mount]

    @staticmethod
    def _fmt_timestamp(value: str | None) -> str:
        """Format a stored ISO-8601 timestamp as ``YYYY-MM-DD HH:MM``.

        Falls back to the raw text when it cannot be parsed, and to ``-``
        when there is none.
        """
        if not value:
            return "-"
        try:
            return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return value

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
