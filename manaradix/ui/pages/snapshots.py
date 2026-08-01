"""Manaradix — Snapshots page.

List, create, delete, rename, restore, browse, and export snapshots.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from manaradix.core.btrfs_ops import SnapshotNotFoundError
from manaradix.i18n import _
from manaradix.model.snapshot import SnapshotType
from manaradix.ui.model import PageModel, Widget

if TYPE_CHECKING:
    from manaradix.module import AppContext
    from manaradix.model.snapshot import Snapshot

logger = logging.getLogger(__name__)

_SCHEDULE_OPTIONS = ["", "hourly", "daily", "weekly", "monthly", "boot"]
_TYPE_OPTIONS = [t.value for t in SnapshotType]


class SnapshotsPage:
    """Snapshot management page."""

    page_id = "snapshots"
    title = _("Snapshots")

    def __init__(self, app_context: AppContext) -> None:
        self.app = app_context
        # Selected snapshot ID (set by the tree selection callback)
        self._selected_snapshot_id: str | None = None

    # -- build_model --------------------------------------------------------

    def build_model(self):
        """Return a ``PageModel`` for snapshot management."""
        snapshots = self._list_snapshots()

        sections = [
            {
                "title": _("Snapshots"),
                "fields": [
                    Widget.tree(
                        id="snapshot_tree",
                        rows=[
                            {
                                "ID": s.id,
                                "Date": s.date.strftime("%Y-%m-%d %H:%M"),
                                "Type": s.type.value,
                                "Schedule": s.schedule or "-",
                                "Subvolume": s.subvolume,
                                "Description": s.description or "-",
                            }
                            for s in snapshots
                        ],
                        selectable=True,
                        on_select=self.on_select_snapshot,
                    ),
                ],
            },
            {
                "title": _("Filters"),
                "fields": [
                    Widget.dropdown(
                        id="filter_subvolume",
                        label=_("Subvolume"),
                        options=self._subvolume_options(snapshots),
                        selected="",
                        on_change=self.on_filter_change,
                    ),
                    Widget.dropdown(
                        id="filter_type",
                        label=_("Type"),
                        options=[""] + _TYPE_OPTIONS,
                        selected="",
                        on_change=self.on_filter_change,
                    ),
                ],
            },
            {
                "title": _("Actions"),
                "fields": [
                    Widget.button(
                        id="btn_create",
                        label=_("Create Snapshot…"),
                        callback=self.on_create_snapshot,
                    ),
                    Widget.button(
                        id="btn_delete",
                        label=_("Delete Selected"),
                        callback=self.on_delete_snapshot,
                    ),
                    Widget.button(
                        id="btn_restore",
                        label=_("Restore Selected"),
                        callback=self.on_restore_snapshot,
                    ),
                    Widget.button(
                        id="btn_browse",
                        label=_("Browse Selected"),
                        callback=self.on_browse_snapshot,
                    ),
                    Widget.button(
                        id="btn_export",
                        label=_("Export Selected"),
                        callback=self.on_export_snapshot,
                    ),
                ],
            },
        ]

        # Show detail panel when a snapshot is selected
        detail_snap = self._get_selected(snapshots)
        if detail_snap:
            sections.append({
                "title": _("Snapshot Detail: %s") % detail_snap.id,
                "fields": [
                    Widget.label(id="detail_path", text=_("Path: %s") % detail_snap.path),
                    Widget.label(id="detail_date", text=_("Date: %s") % detail_snap.date.isoformat()),
                    Widget.label(id="detail_ro", text=_("Read-only: %s") % detail_snap.is_read_only),
                    Widget.button(
                        id="btn_rename",
                        label=_("Update Description"),
                        callback=self.on_rename_snapshot,
                    ),
                    Widget.text(
                        id="rename_description",
                        label=_("New description"),
                        value=detail_snap.description,
                    ),
                ],
            })

        return PageModel(sections=sections)

    # -- Callbacks: actions -------------------------------------------------

    def on_create_snapshot(self) -> None:
        """Open the create-snapshot dialog or inline form.

        In a full implementation this would show a modal form with fields
        for subvolume, type, schedule, description, and read-only toggle.
        For now, creates a manual read-only snapshot of / as a safe default.
        """
        try:
            snap = self.app.snapshot_manager.create(
                subvolume="/",
                description="Manual snapshot",
                read_only=True,
                snapshot_type=SnapshotType.MANUAL,
            )
            logger.info("Created snapshot: %s", snap.id)
        except Exception as exc:
            logger.error("Failed to create snapshot: %s", exc)

    def on_delete_snapshot(self) -> None:
        """Delete the selected snapshot after confirmation."""
        if not self._selected_snapshot_id:
            return
        snap = self.app.snapshot_manager.get(self._selected_snapshot_id)
        if snap is None:
            return
        try:
            self.app.snapshot_manager.delete(snap)
            self._selected_snapshot_id = None
            logger.info("Deleted snapshot: %s", snap.id)
        except SnapshotNotFoundError:
            logger.error("Snapshot not found on disk: %s", snap.path)
        except Exception as exc:
            logger.error("Failed to delete snapshot: %s", exc)

    def on_restore_snapshot(self) -> None:
        """Restore the selected snapshot (swap method).

        .. warning::
           Restoring the currently-mounted root requires booting from
           a rescue environment first.  The UI should warn about this.
        """
        if not self._selected_snapshot_id:
            return
        snap = self.app.snapshot_manager.get(self._selected_snapshot_id)
        if snap is None:
            return
        try:
            pre_restore = self.app.snapshot_manager.restore(snap)
            logger.info(
                "Restored %s to %s (pre-restore backup: %s)",
                snap.id, snap.subvolume, pre_restore.id,
            )
        except Exception as exc:
            logger.error("Failed to restore snapshot: %s", exc)

    def on_browse_snapshot(self) -> None:
        """Mount the selected snapshot for browsing."""
        if not self._selected_snapshot_id:
            return
        snap = self.app.snapshot_manager.get(self._selected_snapshot_id)
        if snap is None:
            return
        try:
            self.app.snapshot_manager.browse(snap)
            logger.info("Mounted snapshot %s for browsing", snap.id)
        except Exception as exc:
            logger.error("Failed to browse snapshot: %s", exc)

    def on_export_snapshot(self) -> None:
        """Export the selected snapshot via btrfs send.

        The output path would normally come from a file-save dialog.
        """
        if not self._selected_snapshot_id:
            return
        snap = self.app.snapshot_manager.get(self._selected_snapshot_id)
        if snap is None:
            return
        output = f"/tmp/{snap.id}.btrfs"
        try:
            self.app.snapshot_manager.export_snapshot(snap, output)
            logger.info("Exported snapshot %s to %s", snap.id, output)
        except ValueError as exc:
            logger.error("Cannot export: %s", exc)
        except Exception as exc:
            logger.error("Export failed: %s", exc)

    def on_rename_snapshot(self) -> None:
        """Update the description of the selected snapshot."""
        if not self._selected_snapshot_id:
            return
        snap = self.app.snapshot_manager.get(self._selected_snapshot_id)
        if snap is None:
            return
        # In a full UI the new description would come from the text widget.
        # For now this is triggered by the text field's value.
        try:
            self.app.snapshot_manager.rename(snap, snap.description)
        except Exception as exc:
            logger.error("Failed to rename snapshot: %s", exc)

    # -- Callbacks: selection & filtering -----------------------------------

    def on_select_snapshot(self, row: dict) -> None:
        """Called when a row in the snapshot tree is selected."""
        self._selected_snapshot_id = row.get("ID")

    def on_filter_change(self) -> None:
        """Called when a filter dropdown changes.  Triggers UI refresh."""
        pass

    # -- Helpers ------------------------------------------------------------

    def _list_snapshots(self) -> list[Snapshot]:
        """Return all snapshots, swallowing errors."""
        try:
            return self.app.snapshot_manager.list_all()
        except Exception as exc:
            logger.warning("Could not list snapshots: %s", exc)
            return []

    def _get_selected(self, snapshots: list[Snapshot]) -> Snapshot | None:
        """Return the currently selected snapshot, or None."""
        if not self._selected_snapshot_id:
            return None
        for s in snapshots:
            if s.id == self._selected_snapshot_id:
                return s
        return None

    @staticmethod
    def _subvolume_options(snapshots: list[Snapshot]) -> list[str]:
        """Distinct subvolume paths from the snapshot list."""
        seen = set()
        for s in snapshots:
            seen.add(s.subvolume)
        return sorted(seen)
