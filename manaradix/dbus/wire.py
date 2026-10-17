"""D-Bus wire layer: the ``dbus.service.Object`` and the daemon entry point.

:class:`ManaradixService` registers the 44 methods defined by
:data:`~manaradix.dbus.constants.METHOD_ACTIONS` on the system bus and
delegates to :class:`~manaradix.dbus.service.ManaradixHandler`; the
handler's typed errors are translated into D-Bus error names
(``org.mageia.manaradix.Error.<Kind>``).

``serve_main`` is the daemon process entry point (``manaradix dbus
serve``): root-only, GLib mainloop, clean SIGTERM shutdown.  It blocks
forever; the systemd unit and the bus-activation file start it on
demand.
"""

from __future__ import annotations

import logging
import signal
import sys

import dbus
import dbus.mainloop.glib
import dbus.service

from manaradix.dbus.constants import (
    DBUS_INTERFACE,
    DBUS_OBJECT_PATH,
    DBUS_SERVICE_NAME,
    ERROR_AUTH_REQUIRED,
    ERROR_FAILED,
    ERROR_INVALID_ARGS,
    ERROR_NOT_FOUND,
)
from manaradix.dbus.errors import (
    AuthorizationError,
    InvalidArgumentError,
    ManaradixDBusError,
    NotFoundError,
    OperationError,
)
from manaradix.dbus.service import ManaradixHandler
from manaradix.i18n import _

logger = logging.getLogger(__name__)


class ManaradixService(dbus.service.Object):
    """The D-Bus service object at ``/org/mageia/manaradix``."""

    def __init__(self, bus, handler: ManaradixHandler | None = None,
                 path: str = DBUS_OBJECT_PATH) -> None:
        super().__init__(bus, path)
        self._handler = handler or ManaradixHandler(None)

    def _dispatch(self, method_name: str, kwargs: dict, sender) -> object:
        """Authorize + delegate; map handler errors onto D-Bus errors."""
        try:
            return self._handler.dispatch(method_name, kwargs, sender,
                                          connection=self.connection)
        except AuthorizationError as exc:
            raise dbus.exceptions.DBusException(
                str(exc), name=ERROR_AUTH_REQUIRED
            )
        except NotFoundError as exc:
            raise dbus.exceptions.DBusException(str(exc), name=ERROR_NOT_FOUND)
        except InvalidArgumentError as exc:
            raise dbus.exceptions.DBusException(
                str(exc), name=ERROR_INVALID_ARGS
            )
        except OperationError as exc:
            raise dbus.exceptions.DBusException(str(exc), name=ERROR_FAILED)
        except ManaradixDBusError as exc:
            raise dbus.exceptions.DBusException(str(exc), name=ERROR_FAILED)

    # -- Tier 1: read -------------------------------------------------------

    @dbus.service.method(DBUS_INTERFACE, out_signature="s",
                         sender_keyword="sender")
    def Ping(self, sender=None) -> str:
        """Check service liveness.

        Returns:
            The service version string.

        Requires the read polkit action.
        """
        return self._dispatch("Ping", {}, sender)

    @dbus.service.method(DBUS_INTERFACE, in_signature="s", out_signature="aa{sv}",
                         sender_keyword="sender")
    def ListSnapshots(self, mount: str, sender=None) -> list[dict]:
        """List the snapshots on a btrfs filesystem.

        Args:
            mount: Mount point of the btrfs filesystem.

        Returns:
            One dict per snapshot.

        Raises:
            org.mageia.manaradix.Error.Failed: The btrfs query failed.

        Requires the read polkit action.
        """
        return self._dispatch("ListSnapshots", {"mount": mount}, sender)

    @dbus.service.method(DBUS_INTERFACE, in_signature="ss", out_signature="a{sv}",
                         sender_keyword="sender")
    def GetSnapshot(self, snapshot_id: str, mount: str,
                    sender=None) -> dict:
        """Get one snapshot by its ID.

        Args:
            snapshot_id: The snapshot ID (timestamp directory name).
            mount: Mount point of the btrfs filesystem.

        Returns:
            The snapshot as a dict.

        Raises:
            org.mageia.manaradix.Error.NotFound: No snapshot with that ID.
            org.mageia.manaradix.Error.Failed: The btrfs query failed.

        Requires the read polkit action.
        """
        return self._dispatch("GetSnapshot", {"id": snapshot_id, "mount": mount},
                              sender)

    @dbus.service.method(DBUS_INTERFACE, in_signature="s", out_signature="aa{sv}",
                         sender_keyword="sender")
    def ListSubvolumes(self, mount: str, sender=None) -> list[dict]:
        """List the subvolumes on a btrfs filesystem.

        Args:
            mount: Mount point of the btrfs filesystem.

        Returns:
            One dict per subvolume.

        Raises:
            org.mageia.manaradix.Error.Failed: The btrfs query failed.

        Requires the read polkit action.
        """
        return self._dispatch("ListSubvolumes", {"mount": mount}, sender)

    @dbus.service.method(DBUS_INTERFACE, in_signature="s", out_signature="a{sv}",
                         sender_keyword="sender")
    def FilesystemUsage(self, mount: str, sender=None) -> dict:
        """Get the usage of a btrfs filesystem.

        Args:
            mount: Mount point of the btrfs filesystem.

        Returns:
            The filesystem usage dict.

        Raises:
            org.mageia.manaradix.Error.Failed: The btrfs query failed.

        Requires the read polkit action.
        """
        return self._dispatch("FilesystemUsage", {"mount": mount}, sender)

    @dbus.service.method(DBUS_INTERFACE, out_signature="aa{sv}",
                         sender_keyword="sender")
    def FilesystemShow(self, sender=None) -> list[dict]:
        """List all btrfs filesystems with their devices and sizes.

        Returns:
            One dict per filesystem.

        Raises:
            org.mageia.manaradix.Error.Failed: The btrfs query failed.

        Requires the read polkit action.
        """
        return self._dispatch("FilesystemShow", {}, sender)

    @dbus.service.method(DBUS_INTERFACE, out_signature="aa{sv}",
                         sender_keyword="sender")
    def BtrfsMounts(self, sender=None) -> list[dict]:
        """List the mounted btrfs filesystems.

        Returns:
            One dict per mount.

        Raises:
            org.mageia.manaradix.Error.Failed: The btrfs query failed.

        Requires the read polkit action.
        """
        return self._dispatch("BtrfsMounts", {}, sender)

    @dbus.service.method(DBUS_INTERFACE, in_signature="s", out_signature="a{sv}",
                         sender_keyword="sender")
    def BalanceStatus(self, mount: str, sender=None) -> dict:
        """Get the balance status of a btrfs filesystem.

        Args:
            mount: Mount point of the btrfs filesystem.

        Returns:
            The balance status dict (total / relocated / running).

        Raises:
            org.mageia.manaradix.Error.Failed: The btrfs query failed.

        Requires the read polkit action.
        """
        return self._dispatch("BalanceStatus", {"mount": mount}, sender)

    @dbus.service.method(DBUS_INTERFACE, in_signature="s", out_signature="a{sv}",
                         sender_keyword="sender")
    def ScrubStatus(self, mount: str, sender=None) -> dict:
        """Get the scrub status of a btrfs filesystem.

        Args:
            mount: Mount point of the btrfs filesystem.

        Returns:
            The scrub status dict (extents / errors / running).

        Raises:
            org.mageia.manaradix.Error.Failed: The btrfs query failed.

        Requires the read polkit action.
        """
        return self._dispatch("ScrubStatus", {"mount": mount}, sender)

    @dbus.service.method(DBUS_INTERFACE, out_signature="aa{sv}",
                         sender_keyword="sender")
    def GetDeviceStats(self, sender=None) -> list[dict]:
        """Merged per-device health report (btrfs counters + SMART).

        Returns:
            One dict per device with the five btrfs error counters and
            optional ``smart_*`` fields (absent when UDisks2/SMART is
            unavailable, never an error).

        Raises:
            org.mageia.manaradix.Error.Failed: The btrfs query failed.

        Requires the read polkit action.
        """
        return self._dispatch("GetDeviceStats", {}, sender)

    @dbus.service.method(DBUS_INTERFACE, in_signature="s", out_signature="s",
                         sender_keyword="sender")
    def GetCompression(self, path: str, sender=None) -> str:
        """Get the compression algorithm of a subvolume.

        Args:
            path: Subvolume path.

        Returns:
            The compression algorithm name (empty string when unset).

        Raises:
            org.mageia.manaradix.Error.Failed: The btrfs query failed.

        Requires the read polkit action.
        """
        return self._dispatch("GetCompression", {"path": path}, sender)

    @dbus.service.method(DBUS_INTERFACE, out_signature="aa{sv}",
                         sender_keyword="sender")
    def ListPolicies(self, sender=None) -> list[dict]:
        """List the configured snapshot policies.

        Returns:
            One dict per policy.

        Raises:
            org.mageia.manaradix.Error.Failed: The configuration could not
                be read.

        Requires the read polkit action.
        """
        return self._dispatch("ListPolicies", {}, sender)

    @dbus.service.method(DBUS_INTERFACE, out_signature="a{sv}",
                         sender_keyword="sender")
    def GetConfig(self, sender=None) -> dict:
        """Get the global configuration.

        Returns:
            The configuration dict.

        Raises:
            org.mageia.manaradix.Error.Failed: The configuration could not
                be read.

        Requires the read polkit action.
        """
        return self._dispatch("GetConfig", {}, sender)

    @dbus.service.method(DBUS_INTERFACE, out_signature="a{sv}",
                         sender_keyword="sender")
    def SetupStatus(self, sender=None) -> dict:
        """Report whether the setup wizard is needed and the system role.

        Returns:
            The setup status dict (needs_setup / role / default_profile /
            root_is_btrfs / grub_present).

        Raises:
            org.mageia.manaradix.Error.Failed: The system layout could not
                be determined.

        Requires the read polkit action.
        """
        return self._dispatch("SetupStatus", {}, sender)

    @dbus.service.method(DBUS_INTERFACE, out_signature="aa{sv}",
                         sender_keyword="sender")
    def ListSetupProfiles(self, sender=None) -> list[dict]:
        """List the available setup profiles.

        Returns:
            One dict per profile.

        Raises:
            org.mageia.manaradix.Error.Failed: The profiles could not be
                loaded.

        Requires the read polkit action.
        """
        return self._dispatch("ListSetupProfiles", {}, sender)

    @dbus.service.method(DBUS_INTERFACE, out_signature="b",
                         sender_keyword="sender")
    def GrubIsInstalled(self, sender=None) -> bool:
        """Check whether the manaradix GRUB script is installed.

        Returns:
            True when ``/etc/grub.d/41_manaradix`` exists.

        Requires the read polkit action.
        """
        return self._dispatch("GrubIsInstalled", {}, sender)

    @dbus.service.method(DBUS_INTERFACE, out_signature="as",
                         sender_keyword="sender")
    def GrubListEntries(self, sender=None) -> list[str]:
        """List the snapshot entries of the manaradix GRUB script.

        Returns:
            The entry labels (empty when the script is not installed).

        Requires the read polkit action.
        """
        return self._dispatch("GrubListEntries", {}, sender)

    # -- Tier 2: manage snapshots -------------------------------------------

    @dbus.service.method(DBUS_INTERFACE, in_signature="ssbsssb",
                         out_signature="a{sv}", sender_keyword="sender")
    def CreateSnapshot(self, subvolume: str, description: str,
                       read_only: bool, snapshot_type: str, schedule: str,
                       mount: str, dry_run: bool, sender=None) -> dict:
        """Create a snapshot of a subvolume.

        Args:
            subvolume: Source subvolume path.
            description: Snapshot description.
            read_only: Create a read-only snapshot.
            snapshot_type: Snapshot type name (``manual``, ``pre-upgrade``,
                ...).
            schedule: Schedule label (e.g. ``daily``).
            mount: Mount point of the btrfs filesystem.
            dry_run: Preview only.

        Returns:
            The created snapshot as a dict.

        Raises:
            org.mageia.manaradix.Error.InvalidArgs: Unknown snapshot type.
            org.mageia.manaradix.Error.Failed: The btrfs operation failed.

        Requires the manage-snapshots polkit action.
        """
        return self._dispatch(
            "CreateSnapshot",
            {"subvolume": subvolume, "description": description,
             "read_only": read_only, "snapshot_type": snapshot_type,
             "schedule": schedule, "mount": mount, "dry_run": dry_run},
            sender,
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="ssb", out_signature="b",
                         sender_keyword="sender")
    def DeleteSnapshot(self, snapshot_id: str, mount: str, dry_run: bool,
                       sender=None) -> bool:
        """Delete a snapshot and its metadata.

        Args:
            snapshot_id: The snapshot ID to delete.
            mount: Mount point of the btrfs filesystem.
            dry_run: Preview only.

        Returns:
            True on success.

        Raises:
            org.mageia.manaradix.Error.NotFound: No snapshot with that ID.
            org.mageia.manaradix.Error.Failed: The btrfs operation failed.

        Requires the manage-snapshots polkit action.
        """
        return self._dispatch(
            "DeleteSnapshot", {"id": snapshot_id, "mount": mount,
                               "dry_run": dry_run}, sender
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="ssisb",
                         out_signature="as", sender_keyword="sender")
    def PruneSnapshots(self, subvolume: str, schedule: str,
                       keep_count: int, mount: str, dry_run: bool,
                       sender=None) -> list[str]:
        """Prune a subvolume/schedule tier, keeping the most recent snapshots.

        Args:
            subvolume: Subvolume path to prune.
            schedule: Schedule tier (``hourly``, ``daily``, ``weekly``,
                ``monthly``, ``boot``).
            keep_count: Number of most recent snapshots to keep.
            mount: Mount point of the btrfs filesystem.
            dry_run: Preview only.

        Returns:
            The IDs of the deleted snapshots.

        Raises:
            org.mageia.manaradix.Error.InvalidArgs: Negative *keep_count*.
            org.mageia.manaradix.Error.Failed: The btrfs operation failed.

        Requires the manage-snapshots polkit action.
        """
        return self._dispatch(
            "PruneSnapshots",
            {"subvolume": subvolume, "schedule": schedule,
             "keep_count": keep_count, "mount": mount, "dry_run": dry_run},
            sender,
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="sss", out_signature="a{sv}",
                         sender_keyword="sender")
    def RenameSnapshot(self, snapshot_id: str, mount: str,
                       description: str, sender=None) -> dict:
        """Rename a snapshot (update its description).

        Args:
            snapshot_id: The snapshot ID to rename.
            mount: Mount point of the btrfs filesystem.
            description: The new description.

        Returns:
            The renamed snapshot as a dict.

        Raises:
            org.mageia.manaradix.Error.NotFound: No snapshot with that ID.
            org.mageia.manaradix.Error.Failed: The btrfs operation failed.

        Requires the manage-snapshots polkit action.
        """
        return self._dispatch(
            "RenameSnapshot", {"id": snapshot_id, "mount": mount,
                               "description": description}, sender
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="ssb", out_signature="a{sv}",
                         sender_keyword="sender")
    def RestoreSnapshot(self, snapshot_id: str, mount: str, dry_run: bool,
                        sender=None) -> dict:
        """Restore a snapshot (swap method).

        Args:
            snapshot_id: The snapshot ID to restore.
            mount: Mount point of the btrfs filesystem.
            dry_run: Preview only.

        Returns:
            The pre-restore snapshot as a dict.

        Raises:
            org.mageia.manaradix.Error.NotFound: No snapshot with that ID.
            org.mageia.manaradix.Error.Failed: The btrfs operation failed.

        Requires the manage-snapshots polkit action.
        """
        return self._dispatch(
            "RestoreSnapshot", {"id": snapshot_id, "mount": mount,
                                "dry_run": dry_run}, sender
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="sssb", out_signature="b",
                         sender_keyword="sender")
    def BrowseSnapshot(self, snapshot_id: str, mount: str,
                       mount_point: str, dry_run: bool, sender=None) -> bool:
        """Mount a snapshot read-only for browsing.

        Args:
            snapshot_id: The snapshot ID to browse.
            mount: Mount point of the btrfs filesystem.
            mount_point: Directory to mount the snapshot at.
            dry_run: Preview only.

        Returns:
            True on success.

        Raises:
            org.mageia.manaradix.Error.NotFound: No snapshot with that ID.
            org.mageia.manaradix.Error.Failed: The btrfs operation failed.

        Requires the manage-snapshots polkit action.
        """
        return self._dispatch(
            "BrowseSnapshot",
            {"id": snapshot_id, "mount": mount, "mount_point": mount_point,
             "dry_run": dry_run},
            sender,
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="sb", out_signature="b",
                         sender_keyword="sender")
    def Unbrowse(self, mount_point: str, dry_run: bool, sender=None) -> bool:
        """Unmount a snapshot mounted for browsing.

        Args:
            mount_point: Directory the snapshot is mounted at.
            dry_run: Preview only.

        Returns:
            True on success.

        Raises:
            org.mageia.manaradix.Error.Failed: The btrfs operation failed.

        Requires the manage-snapshots polkit action.
        """
        return self._dispatch(
            "Unbrowse", {"mount_point": mount_point, "dry_run": dry_run},
            sender,
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="sssb", out_signature="b",
                         sender_keyword="sender")
    def ExportSnapshot(self, snapshot_id: str, mount: str,
                       output_file: str, dry_run: bool, sender=None) -> bool:
        """Export a snapshot as a ``btrfs send`` stream.

        Args:
            snapshot_id: The snapshot ID to export.
            mount: Mount point of the btrfs filesystem.
            output_file: Destination path for the stream.
            dry_run: Preview only.

        Returns:
            True on success.

        Raises:
            org.mageia.manaradix.Error.NotFound: No snapshot with that ID.
            org.mageia.manaradix.Error.Failed: The btrfs operation failed.

        Requires the manage-snapshots polkit action.
        """
        return self._dispatch(
            "ExportSnapshot",
            {"id": snapshot_id, "mount": mount, "output_file": output_file,
             "dry_run": dry_run},
            sender,
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="ssb", out_signature="b",
                         sender_keyword="sender")
    def ImportSnapshot(self, input_file: str, dest_path: str,
                       dry_run: bool, sender=None) -> bool:
        """Import a snapshot from a ``btrfs receive`` stream.

        Args:
            input_file: Path of the snapshot stream to import.
            dest_path: Destination subvolume directory for the import.
            dry_run: Preview only.

        Returns:
            True on success.

        Raises:
            org.mageia.manaradix.Error.Failed: The btrfs operation failed.

        Requires the manage-snapshots polkit action.
        """
        return self._dispatch(
            "ImportSnapshot", {"input_file": input_file,
                               "dest_path": dest_path, "dry_run": dry_run},
            sender,
        )

    # -- Tier 3: manage policies ---------------------------------------------

    @dbus.service.method(DBUS_INTERFACE, in_signature="sbbbbbbiiiiib",
                         out_signature="as", sender_keyword="sender")
    def ApplyPolicy(self, subvolume: str, enabled: bool,
                    schedule_hourly: bool, schedule_daily: bool,
                    schedule_weekly: bool, schedule_monthly: bool,
                    schedule_boot: bool, keep_hourly: int, keep_daily: int,
                    keep_weekly: int, keep_monthly: int, keep_boot: int,
                    dry_run: bool, sender=None) -> list[str]:
        """Apply a snapshot policy to a subvolume.

        Args:
            subvolume: Subvolume path the policy applies to.
            enabled: Whether the policy is enabled.
            schedule_hourly: Enable the hourly schedule.
            schedule_daily: Enable the daily schedule.
            schedule_weekly: Enable the weekly schedule.
            schedule_monthly: Enable the monthly schedule.
            schedule_boot: Enable the boot schedule.
            keep_hourly: Snapshots to keep in the hourly tier.
            keep_daily: Snapshots to keep in the daily tier.
            keep_weekly: Snapshots to keep in the weekly tier.
            keep_monthly: Snapshots to keep in the monthly tier.
            keep_boot: Snapshots to keep in the boot tier.
            dry_run: Preview only.

        Returns:
            The unit file paths that were written.

        Raises:
            org.mageia.manaradix.Error.Failed: The policy is invalid or the
                systemd units could not be written.

        Requires the manage-policies polkit action.
        """
        return self._dispatch(
            "ApplyPolicy",
            {"subvolume": subvolume, "enabled": enabled,
             "schedule_hourly": schedule_hourly,
             "schedule_daily": schedule_daily,
             "schedule_weekly": schedule_weekly,
             "schedule_monthly": schedule_monthly,
             "schedule_boot": schedule_boot,
             "keep_hourly": keep_hourly, "keep_daily": keep_daily,
             "keep_weekly": keep_weekly, "keep_monthly": keep_monthly,
             "keep_boot": keep_boot, "dry_run": dry_run},
            sender,
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="sb", out_signature="as",
                         sender_keyword="sender")
    def RemovePolicy(self, subvolume: str, dry_run: bool,
                     sender=None) -> list[str]:
        """Remove the snapshot policy of a subvolume.

        Args:
            subvolume: Subvolume path to remove the policy for.
            dry_run: Preview only.

        Returns:
            The unit file paths that were removed.

        Raises:
            org.mageia.manaradix.Error.Failed: The systemd units could not
                be removed.

        Requires the manage-policies polkit action.
        """
        return self._dispatch(
            "RemovePolicy", {"subvolume": subvolume, "dry_run": dry_run},
            sender,
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="ssbb", out_signature="b",
                         sender_keyword="sender")
    def SetConfig(self, default_subvolume: str, snapshot_dir: str,
                  boot_from_snapshot: bool, dry_run: bool,
                  sender=None) -> bool:
        """Update the global configuration.

        Args:
            default_subvolume: The default subvolume path.
            snapshot_dir: The snapshot directory name.
            boot_from_snapshot: Enable booting from snapshots in GRUB.
            dry_run: Preview only.

        Returns:
            True on success.

        Raises:
            org.mageia.manaradix.Error.Failed: The configuration could not
                be saved.

        Requires the manage-policies polkit action.
        """
        return self._dispatch(
            "SetConfig",
            {"default_subvolume": default_subvolume,
             "snapshot_dir": snapshot_dir,
             "boot_from_snapshot": boot_from_snapshot, "dry_run": dry_run},
            sender,
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="sb", out_signature="a{sv}",
                         sender_keyword="sender")
    def SetupApply(self, profile_id: str, dry_run: bool,
                   sender=None) -> dict:
        """Apply a setup profile to the system.

        Args:
            profile_id: The profile ID to apply (empty for the default).
            dry_run: Preview only.

        Returns:
            A dict describing what the profile applied.

        Raises:
            org.mageia.manaradix.Error.InvalidArgs: Unknown profile ID.
            org.mageia.manaradix.Error.Failed: The setup failed.

        Requires the manage-policies polkit action.
        """
        return self._dispatch(
            "SetupApply", {"profile_id": profile_id, "dry_run": dry_run},
            sender,
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="ssb", out_signature="b",
                         sender_keyword="sender")
    def GrubInstall(self, snapshot_dir: str, subvol_name: str, dry_run: bool,
                    sender=None) -> bool:
        """Install the manaradix GRUB script.

        Args:
            snapshot_dir: Snapshot directory name used in the entries.
            subvol_name: Subvolume name used in the entries.
            dry_run: Preview only.

        Returns:
            True on success.

        Raises:
            org.mageia.manaradix.Error.Failed: The script could not be
                installed.

        Requires the manage-policies polkit action.
        """
        return self._dispatch(
            "GrubInstall", {"snapshot_dir": snapshot_dir,
                            "subvol_name": subvol_name, "dry_run": dry_run},
            sender,
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="b", out_signature="b",
                         sender_keyword="sender")
    def GrubUninstall(self, dry_run: bool, sender=None) -> bool:
        """Remove the manaradix GRUB script.

        Args:
            dry_run: Preview only.

        Returns:
            True on success.

        Raises:
            org.mageia.manaradix.Error.Failed: The script could not be
                removed.

        Requires the manage-policies polkit action.
        """
        return self._dispatch(
            "GrubUninstall", {"dry_run": dry_run}, sender
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="b", out_signature="b",
                         sender_keyword="sender")
    def GrubUpdate(self, dry_run: bool, sender=None) -> bool:
        """Regenerate the GRUB configuration.

        Args:
            dry_run: Preview only.

        Returns:
            True on success.

        Raises:
            org.mageia.manaradix.Error.Failed: GRUB regeneration failed.

        Requires the manage-policies polkit action.
        """
        return self._dispatch("GrubUpdate", {"dry_run": dry_run}, sender)

    # -- Tier 4: manage filesystem -------------------------------------------

    @dbus.service.method(DBUS_INTERFACE, in_signature="siib", out_signature="b",
                         sender_keyword="sender")
    def BalanceStart(self, mount: str, dusage: int, musage: int,
                     dry_run: bool, sender=None) -> bool:
        """Start a balance of a btrfs filesystem.

        Args:
            mount: Mount point of the btrfs filesystem.
            dusage: Balance data usage threshold (percent).
            musage: Balance metadata usage threshold (percent).
            dry_run: Preview only.

        Returns:
            True on success.

        Raises:
            org.mageia.manaradix.Error.Failed: The btrfs operation failed.

        Requires the manage-filesystem polkit action.
        """
        return self._dispatch(
            "BalanceStart", {"mount": mount, "dusage": dusage,
                             "musage": musage, "dry_run": dry_run}, sender
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="sb", out_signature="b",
                         sender_keyword="sender")
    def BalanceCancel(self, mount: str, dry_run: bool, sender=None) -> bool:
        """Cancel a running balance.

        Args:
            mount: Mount point of the btrfs filesystem.
            dry_run: Preview only.

        Returns:
            True on success.

        Raises:
            org.mageia.manaradix.Error.Failed: The btrfs operation failed.

        Requires the manage-filesystem polkit action.
        """
        return self._dispatch(
            "BalanceCancel", {"mount": mount, "dry_run": dry_run}, sender
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="sb", out_signature="b",
                         sender_keyword="sender")
    def ScrubStart(self, mount: str, dry_run: bool, sender=None) -> bool:
        """Start a scrub of a btrfs filesystem.

        Args:
            mount: Mount point of the btrfs filesystem.
            dry_run: Preview only.

        Returns:
            True on success.

        Raises:
            org.mageia.manaradix.Error.Failed: The btrfs operation failed.

        Requires the manage-filesystem polkit action.
        """
        return self._dispatch(
            "ScrubStart", {"mount": mount, "dry_run": dry_run}, sender
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="sb", out_signature="b",
                         sender_keyword="sender")
    def ScrubCancel(self, mount: str, dry_run: bool, sender=None) -> bool:
        """Cancel a running scrub.

        Args:
            mount: Mount point of the btrfs filesystem.
            dry_run: Preview only.

        Returns:
            True on success.

        Raises:
            org.mageia.manaradix.Error.Failed: The btrfs operation failed.

        Requires the manage-filesystem polkit action.
        """
        return self._dispatch(
            "ScrubCancel", {"mount": mount, "dry_run": dry_run}, sender
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="sbb", out_signature="b",
                         sender_keyword="sender")
    def Defragment(self, path: str, recursive: bool, dry_run: bool,
                   sender=None) -> bool:
        """Defragment a path on a btrfs filesystem.

        Args:
            path: Filesystem path to defragment.
            recursive: Defragment the directory tree recursively.
            dry_run: Preview only.

        Returns:
            True on success.

        Raises:
            org.mageia.manaradix.Error.Failed: The btrfs operation failed.

        Requires the manage-filesystem polkit action.
        """
        return self._dispatch(
            "Defragment", {"path": path, "recursive": recursive,
                           "dry_run": dry_run}, sender
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="ssb", out_signature="b",
                         sender_keyword="sender")
    def SetCompression(self, path: str, algorithm: str, dry_run: bool,
                       sender=None) -> bool:
        """Set the compression algorithm of a subvolume.

        Args:
            path: Subvolume path.
            algorithm: Compression algorithm name (e.g. ``zstd``).
            dry_run: Preview only.

        Returns:
            True on success.

        Raises:
            org.mageia.manaradix.Error.Failed: The btrfs operation failed.

        Requires the manage-filesystem polkit action.
        """
        return self._dispatch(
            "SetCompression", {"path": path, "algorithm": algorithm,
                               "dry_run": dry_run}, sender
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="sb", out_signature="b",
                         sender_keyword="sender")
    def CreateSubvolume(self, path: str, dry_run: bool, sender=None) -> bool:
        """Create a subvolume.

        Args:
            path: Path of the new subvolume.
            dry_run: Preview only.

        Returns:
            True on success.

        Raises:
            org.mageia.manaradix.Error.Failed: The btrfs operation failed.

        Requires the manage-filesystem polkit action.
        """
        return self._dispatch(
            "CreateSubvolume", {"path": path, "dry_run": dry_run}, sender
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="sb", out_signature="b",
                         sender_keyword="sender")
    def DeleteSubvolume(self, path: str, dry_run: bool, sender=None) -> bool:
        """Delete a subvolume.

        Args:
            path: Path of the subvolume to delete.
            dry_run: Preview only.

        Returns:
            True on success.

        Raises:
            org.mageia.manaradix.Error.Failed: The btrfs operation failed.

        Requires the manage-filesystem polkit action.
        """
        return self._dispatch(
            "DeleteSubvolume", {"path": path, "dry_run": dry_run}, sender
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="isb", out_signature="b",
                         sender_keyword="sender")
    def SetDefaultSubvolume(self, subvol_id: int, mount: str, dry_run: bool,
                            sender=None) -> bool:
        """Set the default subvolume of a btrfs filesystem.

        Args:
            subvol_id: The subvolume ID to make default.
            mount: Mount point of the btrfs filesystem.
            dry_run: Preview only.

        Returns:
            True on success.

        Raises:
            org.mageia.manaradix.Error.Failed: The btrfs operation failed.

        Requires the manage-filesystem polkit action.
        """
        return self._dispatch(
            "SetDefaultSubvolume", {"id": subvol_id, "mount": mount,
                                    "dry_run": dry_run}, sender
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="", out_signature="a{sv}",
                         sender_keyword="sender")
    def GetUsage(self, sender=None) -> dict:
        """Get snapshot usage data from the SQLite store.

        Returns:
            Dict with keys: status (dict), last_update (string or None),
            aggregates (list of dicts), snapshots (list of dicts).

        Requires the read polkit action.
        """
        return self._dispatch("GetUsage", {}, sender)

    @dbus.service.method(DBUS_INTERFACE, out_signature="a{sv}",
                         sender_keyword="sender")
    def RefreshUsage(self, sender=None) -> dict:
        """Start a background snapshot-usage collection.

        Returns:
            ``{"state": "started" | "already_running" | "failed",
            "error": str}``; returns immediately.

        Requires the manage-filesystem polkit action.
        """
        return self._dispatch("RefreshUsage", {}, sender)


def serve_main(config_path: str, snapshot_dir: str = ".snapshots", *,
               debug: bool = False) -> int:
    """Run the D-Bus daemon (blocking; the systemd unit's ``ExecStart``).

    Root-only: the service authorizes callers via polkit, so it must
    run as root.  Returns an exit code instead of raising when the
    caller is not root or the name is already taken; on success it
    never returns until SIGTERM.
    """
    from manaradix.module import _init_services
    from manaradix.utils.pkexec import is_root

    if not is_root():
        print(
            _("error: 'manaradix dbus serve' must run as root (system service)."),
            file=sys.stderr,
        )
        return 1

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    from gi.repository import GLib

    bus = dbus.SystemBus()
    try:
        # Keep the BusName referenced: dbus-python's BusName.__del__
        # releases the name, so a temporary object would be garbage
        # collected right after acquisition and the daemon would run
        # on without owning its name.
        bus_name = dbus.service.BusName(DBUS_SERVICE_NAME, bus)
    except dbus.exceptions.NameExistsException:
        print(_("error: the manaradix D-Bus service is already running."),
              file=sys.stderr)
        return 1

    loop = GLib.MainLoop()
    stopping = False

    def _stop(signum, frame) -> None:
        nonlocal stopping
        stopping = True
        logger.info("Received signal %s; shutting down", signum)
        loop.quit()

    # Install the handlers before any service initialization so a
    # SIGTERM landing during startup is not lost: GLib.MainLoop.quit()
    # on a loop run() has not started is a no-op, so the flag also
    # gates run() itself.
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    if debug:
        logging.getLogger().setLevel(logging.DEBUG)
    logger.info("manaradix D-Bus service %s ready", DBUS_SERVICE_NAME)

    handler = ManaradixHandler(_init_services(config_path, snapshot_dir))
    # Same lifetime concern as the BusName: keep the object referenced
    # so method dispatch cannot be garbage collected out from under
    # the main loop.
    service = ManaradixService(bus, handler=handler)

    if not stopping:
        loop.run()
    logger.info("manaradix D-Bus service stopped")
    return 0
