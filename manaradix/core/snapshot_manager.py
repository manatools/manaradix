"""Snapshot management — create, list, delete, rename, restore, browse, export.

Orchestrates ``BtrfsOps`` calls and maintains metadata sidecars.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Optional

from manaradix.core.btrfs_ops import (
    BtrfsError,
    BtrfsOps,
    SnapshotNotFoundError,
)
from manaradix.model.snapshot import Snapshot, SnapshotType

logger = logging.getLogger(__name__)


class SnapshotManager:
    """Manages btrfs snapshot lifecycle.

    Wraps ``BtrfsOps`` to provide higher-level operations with metadata
    tracking, journaling, and safety checks.

    Usage::

        ops = BtrfsOps()
        mgr = SnapshotManager(ops)
        snap = mgr.create("/", description="Before upgrade")
        for s in mgr.list_all("/"):
            print(s.id, s.date, s.type.value)
    """

    def __init__(self, btrfs_ops: BtrfsOps) -> None:
        self.ops = btrfs_ops
        self._journal: list[dict] = []

    # ------------------------------------------------------------------
    # Journal / undo log
    # ------------------------------------------------------------------

    def _journal_op(self, action: str, details: dict) -> None:
        """Record a journal entry for potential rollback."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "action": action,
            **details,
        }
        self._journal.append(entry)
        logger.debug("Journal: %s", entry)

    def get_journal(self) -> list[dict]:
        """Return a copy of the operation journal."""
        return list(self._journal)

    # ------------------------------------------------------------------
    # CRUD operations
    # ------------------------------------------------------------------

    def create(
        self,
        subvolume: str = "/",
        *,
        description: str = "",
        read_only: bool = True,
        snapshot_type: SnapshotType = SnapshotType.MANUAL,
        schedule: str = "",
        mount: str = "/",
        dry_run: bool = False,
    ) -> Snapshot:
        """Create a new snapshot of *subvolume*.

        Args:
            subvolume: Source subvolume path.
            description: Human-readable description for the sidecar.
            read_only: Whether the snapshot should be read-only.
            snapshot_type: Classification tag.
            schedule: Schedule tier (hourly, daily, weekly, monthly, boot)
                      used for retention pruning.
            mount: Mount point containing the subvolume.
            dry_run: Preview only.

        Returns:
            The newly created ``Snapshot``.
        """
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        dest = self.ops._snapshot_path(mount, subvolume, ts)

        snapshot = self.ops.create_snapshot(
            subvolume,
            dest,
            read_only=read_only,
            dry_run=dry_run,
        )
        snapshot.description = description
        snapshot.type = snapshot_type
        snapshot.schedule = schedule

        if not dry_run:
            self.ops.write_snapshot_info(snapshot)
            self._journal_op(
                "snapshot_created",
                {
                    "id": snapshot.id,
                    "subvolume": subvolume,
                    "path": snapshot.path,
                    "type": snapshot.type.value,
                    "schedule": schedule,
                },
            )

        return snapshot

    def list_all(
        self,
        mount: str = "/",
        *,
        dry_run: bool = False,
    ) -> list[Snapshot]:
        """List all snapshots on *mount*."""
        return self.ops.list_snapshots(mount, dry_run=dry_run)

    def get(
        self,
        snapshot_id: str,
        mount: str = "/",
    ) -> Optional[Snapshot]:
        """Get a single snapshot by its ID."""
        for snap in self.list_all(mount):
            if snap.id == snapshot_id:
                return snap
        return None

    def delete(
        self,
        snapshot: Snapshot,
        *,
        dry_run: bool = False,
    ) -> None:
        """Delete a snapshot and its metadata.

        Args:
            snapshot: The snapshot to delete.
            dry_run: Preview only.

        Raises:
            SnapshotNotFoundError: If the snapshot path does not exist.
        """
        import shutil

        if not dry_run and not os.path.isdir(snapshot.path):
            raise SnapshotNotFoundError(
                f"Snapshot path not found: {snapshot.path}"
            )

        self.ops.delete_snapshot(snapshot.path, dry_run=dry_run)

        if not dry_run:
            # Remove the parent timestamp directory if empty
            parent = os.path.dirname(snapshot.path)
            info_path = os.path.join(parent, "info.json")
            if os.path.isfile(info_path):
                os.remove(info_path)
            try:
                remaining = [
                    f
                    for f in os.listdir(parent)
                    if f != "info.json"
                ]
                if not remaining:
                    shutil.rmtree(parent)
            except OSError:
                pass

            self._journal_op(
                "snapshot_deleted",
                {"id": snapshot.id, "path": snapshot.path},
            )

    def prune(
        self,
        subvolume: str,
        schedule: str,
        keep_count: int,
        *,
        mount: str = "/",
        dry_run: bool = False,
    ) -> list[str]:
        """Prune snapshots for a subvolume/schedule, keeping the *keep_count* most recent.

        Args:
            subvolume: Subvolume path to prune snapshots for.
            schedule: Schedule tier (hourly, daily, weekly, monthly, boot).
            keep_count: Number of most recent snapshots to keep.
            mount: Mount point containing snapshots.
            dry_run: Preview only.

        Returns:
            List of deleted snapshot IDs.
        """
        if keep_count < 0:
            raise ValueError(f"keep_count must be non-negative, got {keep_count}")

        all_snapshots = self.list_all(mount, dry_run=dry_run)

        # Filter to matching subvolume and schedule, then sort newest-first
        matching = [
            s for s in all_snapshots
            if s.subvolume == subvolume and s.schedule == schedule
        ]
        matching.sort(key=lambda s: s.date, reverse=True)

        if len(matching) <= keep_count:
            return []

        to_delete = matching[keep_count:]
        deleted_ids: list[str] = []

        for snap in to_delete:
            logger.info(
                "Pruning snapshot %s (%s/%s, %s)",
                snap.id, snap.subvolume, snap.schedule, snap.date.isoformat(),
            )
            self.delete(snap, dry_run=dry_run)
            deleted_ids.append(snap.id)

        return deleted_ids

    def rename(
        self,
        snapshot: Snapshot,
        new_description: str,
    ) -> Snapshot:
        """Rename/update a snapshot's description in its sidecar metadata.

        The snapshot ID/path is unchanged; only metadata is updated.
        """
        snapshot.description = new_description
        self.ops.write_snapshot_info(snapshot)
        self._journal_op(
            "snapshot_renamed",
            {"id": snapshot.id, "description": new_description},
        )
        return snapshot

    def restore(
        self,
        snapshot: Snapshot,
        *,
        mount: str = "/",
        dry_run: bool = False,
    ) -> Snapshot:
        """Restore *subvolume* to the state captured in *snapshot*.

        Uses the swap method (see ``BtrfsOps.restore_snapshot``).
        Returns the pre-restore safety snapshot.

        .. warning::
           The target subvolume must not be in use (e.g. currently
           mounted root).  Boot from a rescue environment first.
        """
        target = snapshot.subvolume
        pre_restore = self.ops.restore_snapshot(
            snapshot.path,
            target,
            mount=mount,
            dry_run=dry_run,
        )
        if not dry_run:
            self.ops.write_snapshot_info(pre_restore)
            self._journal_op(
                "snapshot_restored",
                {
                    "snapshot_id": snapshot.id,
                    "subvolume": target,
                    "pre_restore_id": pre_restore.id,
                },
            )
        return pre_restore

    def browse(self, snapshot: Snapshot, mount_point: str = "/mnt/manaradix-snapshot") -> None:
        """Mount a snapshot read-only for browsing.

        Uses ``mount -o subvol=... -o ro``.

        Args:
            snapshot: The snapshot to mount.
            mount_point: Where to mount it.

        Raises:
            BtrfsError: If the mount fails.
        """
        import subprocess

        os.makedirs(mount_point, exist_ok=True)
        try:
            subprocess.run(
                [
                    "mount",
                    "-o",
                    f"subvol={snapshot.path}",
                    "-o",
                    "ro",
                    snapshot.subvolume,
                    mount_point,
                ],
                check=True,
                text=True,
                capture_output=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            stderr = getattr(exc, "stderr", None)
            msg = f"Failed to mount snapshot at {mount_point}: {stderr or exc}"
            raise BtrfsError(msg) from exc

    def unbrowse(self, mount_point: str = "/mnt/manaradix-snapshot") -> None:
        """Unmount a previously browsed snapshot."""
        import subprocess

        try:
            subprocess.run(
                ["umount", mount_point],
                check=True,
                text=True,
                capture_output=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            stderr = getattr(exc, "stderr", None)
            msg = f"Failed to unmount {mount_point}: {stderr or exc}"
            raise BtrfsError(msg) from exc

    # ------------------------------------------------------------------
    # Snapshot export (send/receive)
    # ------------------------------------------------------------------

    def export_snapshot(
        self,
        snapshot: Snapshot,
        output_file: str,
        *,
        dry_run: bool = False,
    ) -> None:
        """Export a read-only snapshot via ``btrfs send``.

        Args:
            snapshot: The snapshot to export. Must be read-only.
            output_file: Destination file for the send stream.
            dry_run: Preview only.

        Raises:
            ValueError: If the snapshot is not read-only.
        """
        if not snapshot.is_read_only:
            raise ValueError(
                "Cannot export a read-write snapshot. "
                "Create a read-only snapshot first."
            )

        if dry_run:
            logger.info(
                "[DRY RUN] btrfs send %s > %s",
                snapshot.path,
                output_file,
            )
            return

        import subprocess

        try:
            with open(output_file, "wb") as fh:
                subprocess.run(
                    ["btrfs", "send", snapshot.path],
                    stdout=fh,
                    stderr=subprocess.PIPE,
                    check=True,
                )
        except subprocess.CalledProcessError as exc:
            raise BtrfsError(
                f"btrfs send failed: {exc.stderr.decode() if exc.stderr else exc}"
            ) from exc

    def import_snapshot(
        self,
        input_file: str,
        dest_path: str,
        *,
        dry_run: bool = False,
    ) -> None:
        """Import a snapshot via ``btrfs receive``.

        Args:
            input_file: File containing a btrfs send stream.
            dest_path: Directory to receive the snapshot into.
            dry_run: Preview only.
        """
        import subprocess

        if dry_run:
            logger.info(
                "[DRY RUN] btrfs receive %s < %s",
                dest_path,
                input_file,
            )
            return

        try:
            with open(input_file, "rb") as fh:
                subprocess.run(
                    ["btrfs", "receive", dest_path],
                    stdin=fh,
                    stderr=subprocess.PIPE,
                    check=True,
                )
            self._journal_op(
                "snapshot_imported",
                {"source": input_file, "destination": dest_path},
            )
        except subprocess.CalledProcessError as exc:
            raise BtrfsError(
                f"btrfs receive failed: {exc.stderr.decode() if exc.stderr else exc}"
            ) from exc
