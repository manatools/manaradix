"""Subvolume data model."""

from dataclasses import dataclass, field


@dataclass
class Subvolume:
    """Represents a btrfs subvolume.

    Attributes:
        id: Subvolume ID reported by ``btrfs subvolume list``.
        path: Absolute path of the subvolume (e.g. ``/``, ``/home``).
        parent_id: ID of the parent subvolume (0 for top-level).
        uuid: Btrfs UUID of the subvolume.
        parent_uuid: UUID of the parent subvolume.
    """

    id: int
    path: str
    parent_id: int = 0
    uuid: str = ""
    parent_uuid: str = ""

    @property
    def is_top_level(self) -> bool:
        """True if this is the top-level (id 5) subvolume."""
        return self.parent_id == 0
