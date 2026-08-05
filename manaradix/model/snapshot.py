"""Snapshot data model.

Snapshots are stored in ``<mount>/.snapshots/<timestamp>/<subvol-name>/``
with a JSON sidecar metadata file at ``info.json``.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class SnapshotType(Enum):
    """Type of btrfs snapshot."""

    MANUAL = "manual"
    READ_ONLY = "readonly"
    TIMED = "timed"
    BOOT = "boot"
    PRE_RESTORE = "pre-restore"
    PRE_INSTALL = "pre-install"
    PRE_UPGRADE = "pre-upgrade"


@dataclass
class Snapshot:
    """Represents a single btrfs snapshot.

    Attributes:
        id: Timestamp-based identifier (e.g. ``2025-01-15_14-00-00``).
        subvolume: Source subvolume path (e.g. ``/`` or ``/home``).
        path: Full filesystem path to the snapshot directory.
        date: When the snapshot was created.
        type: Classification of the snapshot.
        description: Optional human-readable description.
    """

    id: str
    subvolume: str
    path: str
    date: datetime
    type: SnapshotType
    description: str = ""
    schedule: str = ""

    @property
    def is_read_only(self) -> bool:
        """Convenience check for read-only snapshots."""
        return self.type in (
            SnapshotType.READ_ONLY,
            SnapshotType.TIMED,
            SnapshotType.BOOT,
            SnapshotType.PRE_UPGRADE,
        )

    @property
    def snapshot_subvol_path(self) -> str:
        """The subvolume path relative to the snapshot dir (e.g. ``root``)."""
        return self.path.rstrip("/").rsplit("/", 1)[-1]
