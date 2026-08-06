"""Thin D-Bus client for the manaradix system service.

One method per D-Bus method, mirroring
:data:`~manaradix.dbus.constants.METHOD_ACTIONS`.  All calls raise
:class:`~manaradix.dbus.errors.ManaradixDBusError` subclasses
(:func:`~manaradix.dbus.errors.raise_for_dbus` translates raw D-Bus
errors), so callers never deal with dbus-python directly.  The bus is
created lazily on first use, which lets tests inject a fake and lets
read-only tooling import the module without a bus.
"""

from __future__ import annotations

from manaradix.core.snapshot_manager import BROWSE_MOUNT_POINT
from manaradix.dbus.constants import (
    DBUS_INTERFACE,
    DBUS_OBJECT_PATH,
    DBUS_SERVICE_NAME,
)
from manaradix.dbus.errors import raise_for_dbus


class ManaradixClient:
    """Client for the ``org.mageia.manaradix`` system service."""

    def __init__(self, bus=None, timeout: float = 30.0) -> None:
        self._bus = bus
        self._timeout = timeout

    # -- Plumbing -----------------------------------------------------------

    def _proxy(self):
        """Return the service proxy object (lazily connecting to the bus)."""
        if self._bus is None:
            import dbus

            self._bus = dbus.SystemBus()
        return self._bus.get_object(DBUS_SERVICE_NAME, DBUS_OBJECT_PATH)

    def _call(self, method_name: str, *args):
        """Invoke one D-Bus method, translating errors for the caller."""
        from dbus.exceptions import DBusException

        method = getattr(self._proxy(), method_name)
        try:
            return method(
                *args, dbus_interface=DBUS_INTERFACE, timeout=self._timeout
            )
        except DBusException as exc:
            raise_for_dbus(exc)

    def daemon_running(self) -> bool:
        """Return True if the service currently owns its bus name.

        Uses ``NameHasOwner`` on the bus itself rather than calling
        ``Ping``: a call would *activate* the daemon, while status
        checks must not.
        """
        if self._bus is None:
            import dbus

            self._bus = dbus.SystemBus()
        try:
            return bool(
                self._bus.call_blocking(
                    "org.freedesktop.DBus",
                    "/org/freedesktop/DBus",
                    "org.freedesktop.DBus",
                    "NameHasOwner",
                    "s",
                    [DBUS_SERVICE_NAME],
                )
            )
        except Exception:
            return False

    # -- Tier 1: read -------------------------------------------------------

    def ping(self) -> str:
        """Check service liveness.

        Returns:
            The service version string.

        Raises:
            AuthorizationError: The caller lacks the read polkit action.
            DaemonUnavailableError: The service is not running.

        Requires the read polkit action.
        """
        return self._call("Ping")

    def list_snapshots(self, mount: str = "/") -> list[dict]:
        """List the snapshots on a btrfs filesystem.

        Args:
            mount: Mount point of the btrfs filesystem (default "/").

        Returns:
            One dict per snapshot.

        Raises:
            AuthorizationError: The caller lacks the read polkit action.
            OperationError: The btrfs query failed.
            DaemonUnavailableError: The service is not running.

        Requires the read polkit action.
        """
        return self._call("ListSnapshots", mount)

    def get_snapshot(self, snapshot_id: str, mount: str = "/") -> dict:
        """Get one snapshot by its ID.

        Args:
            snapshot_id: The snapshot ID (timestamp directory name).
            mount: Mount point of the btrfs filesystem (default "/").

        Returns:
            The snapshot as a dict.

        Raises:
            AuthorizationError: The caller lacks the read polkit action.
            NotFoundError: No snapshot with that ID.
            OperationError: The btrfs query failed.
            DaemonUnavailableError: The service is not running.

        Requires the read polkit action.
        """
        return self._call("GetSnapshot", snapshot_id, mount)

    def list_subvolumes(self, mount: str = "/") -> list[dict]:
        """List the subvolumes on a btrfs filesystem.

        Args:
            mount: Mount point of the btrfs filesystem (default "/").

        Returns:
            One dict per subvolume.

        Raises:
            AuthorizationError: The caller lacks the read polkit action.
            OperationError: The btrfs query failed.
            DaemonUnavailableError: The service is not running.

        Requires the read polkit action.
        """
        return self._call("ListSubvolumes", mount)

    def filesystem_usage(self, mount: str = "/") -> dict:
        """Get the usage of a btrfs filesystem.

        Args:
            mount: Mount point of the btrfs filesystem (default "/").

        Returns:
            The filesystem usage dict.

        Raises:
            AuthorizationError: The caller lacks the read polkit action.
            OperationError: The btrfs query failed.
            DaemonUnavailableError: The service is not running.

        Requires the read polkit action.
        """
        return self._call("FilesystemUsage", mount)

    def filesystem_show(self) -> list[dict]:
        """List all btrfs filesystems with their devices and sizes.

        Returns:
            One dict per filesystem.

        Raises:
            AuthorizationError: The caller lacks the read polkit action.
            OperationError: The btrfs query failed.
            DaemonUnavailableError: The service is not running.

        Requires the read polkit action.
        """
        return self._call("FilesystemShow")

    def btrfs_mounts(self) -> list[dict]:
        """List the mounted btrfs filesystems.

        Returns:
            One dict per mount.

        Raises:
            AuthorizationError: The caller lacks the read polkit action.
            OperationError: The btrfs query failed.
            DaemonUnavailableError: The service is not running.

        Requires the read polkit action.
        """
        return self._call("BtrfsMounts")

    def balance_status(self, mount: str = "/") -> dict:
        """Get the balance status of a btrfs filesystem.

        Args:
            mount: Mount point of the btrfs filesystem (default "/").

        Returns:
            The balance status dict (total / relocated / running).

        Raises:
            AuthorizationError: The caller lacks the read polkit action.
            OperationError: The btrfs query failed.
            DaemonUnavailableError: The service is not running.

        Requires the read polkit action.
        """
        return self._call("BalanceStatus", mount)

    def scrub_status(self, mount: str = "/") -> dict:
        """Get the scrub status of a btrfs filesystem.

        Args:
            mount: Mount point of the btrfs filesystem (default "/").

        Returns:
            The scrub status dict (extents / errors / running).

        Raises:
            AuthorizationError: The caller lacks the read polkit action.
            OperationError: The btrfs query failed.
            DaemonUnavailableError: The service is not running.

        Requires the read polkit action.
        """
        return self._call("ScrubStatus", mount)

    def get_compression(self, path: str) -> str:
        """Get the compression algorithm of a subvolume.

        Args:
            path: Subvolume path.

        Returns:
            The compression algorithm name (empty string when unset).

        Raises:
            AuthorizationError: The caller lacks the read polkit action.
            OperationError: The btrfs query failed.
            DaemonUnavailableError: The service is not running.

        Requires the read polkit action.
        """
        return self._call("GetCompression", path)

    def list_policies(self) -> list[dict]:
        """List the configured snapshot policies.

        Returns:
            One dict per policy.

        Raises:
            AuthorizationError: The caller lacks the read polkit action.
            OperationError: The configuration could not be read.
            DaemonUnavailableError: The service is not running.

        Requires the read polkit action.
        """
        return self._call("ListPolicies")

    def get_config(self) -> dict:
        """Get the global configuration.

        Returns:
            The configuration dict.

        Raises:
            AuthorizationError: The caller lacks the read polkit action.
            OperationError: The configuration could not be read.
            DaemonUnavailableError: The service is not running.

        Requires the read polkit action.
        """
        return self._call("GetConfig")

    def setup_status(self) -> dict:
        """Report whether the setup wizard is needed and the system role.

        Returns:
            The setup status dict (needs_setup / role / default_profile /
            root_is_btrfs / grub_present).

        Raises:
            AuthorizationError: The caller lacks the read polkit action.
            OperationError: The system layout could not be determined.
            DaemonUnavailableError: The service is not running.

        Requires the read polkit action.
        """
        return self._call("SetupStatus")

    def list_setup_profiles(self) -> list[dict]:
        """List the available setup profiles.

        Returns:
            One dict per profile.

        Raises:
            AuthorizationError: The caller lacks the read polkit action.
            DaemonUnavailableError: The service is not running.

        Requires the read polkit action.
        """
        return self._call("ListSetupProfiles")

    def grub_is_installed(self) -> bool:
        """Check whether the manaradix GRUB script is installed.

        Returns:
            True when ``/etc/grub.d/41_manaradix`` exists.

        Raises:
            AuthorizationError: The caller lacks the read polkit action.
            DaemonUnavailableError: The service is not running.

        Requires the read polkit action.
        """
        return self._call("GrubIsInstalled")

    def grub_list_entries(self) -> list[str]:
        """List the snapshot entries of the manaradix GRUB script.

        Returns:
            The entry labels (empty when the script is not installed).

        Raises:
            AuthorizationError: The caller lacks the read polkit action.
            DaemonUnavailableError: The service is not running.

        Requires the read polkit action.
        """
        return self._call("GrubListEntries")

    # -- Tier 2: manage snapshots -------------------------------------------

    def create_snapshot(
        self,
        *,
        subvolume: str = "/",
        description: str = "",
        read_only: bool = True,
        snapshot_type: str = "manual",
        schedule: str = "",
        mount: str = "/",
        dry_run: bool = False,
    ) -> dict:
        """Create a snapshot of a subvolume.

        Args:
            subvolume: Source subvolume path (default "/").
            description: Snapshot description (default "").
            read_only: Create a read-only snapshot (default True).
            snapshot_type: Snapshot type name (``manual``, ``pre-upgrade``,
                ...) (default "manual").
            schedule: Schedule label (e.g. ``daily``) (default "").
            mount: Mount point of the btrfs filesystem (default "/").
            dry_run: Preview only (default False).

        Returns:
            The created snapshot as a dict.

        Raises:
            AuthorizationError: The caller lacks the manage-snapshots polkit
                action.
            InvalidArgumentError: Unknown snapshot type.
            OperationError: The btrfs operation failed.
            DaemonUnavailableError: The service is not running.

        Requires the manage-snapshots polkit action.
        """
        return self._call(
            "CreateSnapshot", subvolume, description, read_only,
            snapshot_type, schedule, mount, dry_run,
        )

    def delete_snapshot(
        self, snapshot_id: str, mount: str = "/", *, dry_run: bool = False
    ) -> bool:
        """Delete a snapshot and its metadata.

        Args:
            snapshot_id: The snapshot ID to delete.
            mount: Mount point of the btrfs filesystem (default "/").
            dry_run: Preview only (default False).

        Returns:
            True on success.

        Raises:
            AuthorizationError: The caller lacks the manage-snapshots polkit
                action.
            NotFoundError: No snapshot with that ID.
            OperationError: The btrfs operation failed.
            DaemonUnavailableError: The service is not running.

        Requires the manage-snapshots polkit action.
        """
        return self._call("DeleteSnapshot", snapshot_id, mount, dry_run)

    def prune_snapshots(
        self, subvolume: str, schedule: str, keep_count: int,
        mount: str = "/", *, dry_run: bool = False,
    ) -> list[str]:
        """Prune a subvolume/schedule tier, keeping the most recent snapshots.

        Args:
            subvolume: Subvolume path to prune.
            schedule: Schedule tier (``hourly``, ``daily``, ``weekly``,
                ``monthly``, ``boot``).
            keep_count: Number of most recent snapshots to keep.
            mount: Mount point of the btrfs filesystem (default "/").
            dry_run: Preview only (default False).

        Returns:
            The IDs of the deleted snapshots.

        Raises:
            AuthorizationError: The caller lacks the manage-snapshots polkit
                action.
            InvalidArgumentError: Negative *keep_count*.
            OperationError: The btrfs operation failed.
            DaemonUnavailableError: The service is not running.

        Requires the manage-snapshots polkit action.
        """
        return self._call(
            "PruneSnapshots", subvolume, schedule, keep_count, mount, dry_run
        )

    def rename_snapshot(
        self, snapshot_id: str, mount: str, description: str
    ) -> dict:
        """Rename a snapshot (update its description).

        Args:
            snapshot_id: The snapshot ID to rename.
            mount: Mount point of the btrfs filesystem.
            description: The new description.

        Returns:
            The renamed snapshot as a dict.

        Raises:
            AuthorizationError: The caller lacks the manage-snapshots polkit
                action.
            NotFoundError: No snapshot with that ID.
            OperationError: The btrfs operation failed.
            DaemonUnavailableError: The service is not running.

        Requires the manage-snapshots polkit action.
        """
        return self._call("RenameSnapshot", snapshot_id, mount, description)

    def restore_snapshot(
        self, snapshot_id: str, mount: str = "/", *, dry_run: bool = False
    ) -> dict:
        """Restore a snapshot (swap method).

        Args:
            snapshot_id: The snapshot ID to restore.
            mount: Mount point of the btrfs filesystem (default "/").
            dry_run: Preview only (default False).

        Returns:
            The pre-restore snapshot as a dict.

        Raises:
            AuthorizationError: The caller lacks the manage-snapshots polkit
                action.
            NotFoundError: No snapshot with that ID.
            OperationError: The btrfs operation failed.
            DaemonUnavailableError: The service is not running.

        Requires the manage-snapshots polkit action.
        """
        return self._call("RestoreSnapshot", snapshot_id, mount, dry_run)

    def browse_snapshot(
        self, snapshot_id: str, mount: str = "/",
        mount_point: str = BROWSE_MOUNT_POINT, *, dry_run: bool = False,
    ) -> bool:
        """Mount a snapshot read-only for browsing.

        Args:
            snapshot_id: The snapshot ID to browse.
            mount: Mount point of the btrfs filesystem (default "/").
            mount_point: Directory to mount the snapshot at (default
                ``BROWSE_MOUNT_POINT``).
            dry_run: Preview only (default False).

        Returns:
            True on success.

        Raises:
            AuthorizationError: The caller lacks the manage-snapshots polkit
                action.
            NotFoundError: No snapshot with that ID.
            OperationError: The btrfs operation failed.
            DaemonUnavailableError: The service is not running.

        Requires the manage-snapshots polkit action.
        """
        return self._call(
            "BrowseSnapshot", snapshot_id, mount, mount_point, dry_run
        )

    def unbrowse(self, mount_point: str = BROWSE_MOUNT_POINT, *,
                 dry_run: bool = False) -> bool:
        """Unmount a snapshot mounted for browsing.

        Args:
            mount_point: Directory the snapshot is mounted at (default
                ``BROWSE_MOUNT_POINT``).
            dry_run: Preview only (default False).

        Returns:
            True on success.

        Raises:
            AuthorizationError: The caller lacks the manage-snapshots polkit
                action.
            OperationError: The btrfs operation failed.
            DaemonUnavailableError: The service is not running.

        Requires the manage-snapshots polkit action.
        """
        return self._call("Unbrowse", mount_point, dry_run)

    def export_snapshot(
        self, snapshot_id: str, mount: str, output_file: str, *,
        dry_run: bool = False,
    ) -> bool:
        """Export a snapshot as a ``btrfs send`` stream.

        Args:
            snapshot_id: The snapshot ID to export.
            mount: Mount point of the btrfs filesystem.
            output_file: Destination path for the stream.
            dry_run: Preview only (default False).

        Returns:
            True on success.

        Raises:
            AuthorizationError: The caller lacks the manage-snapshots polkit
                action.
            NotFoundError: No snapshot with that ID.
            OperationError: The btrfs operation failed.
            DaemonUnavailableError: The service is not running.

        Requires the manage-snapshots polkit action.
        """
        return self._call(
            "ExportSnapshot", snapshot_id, mount, output_file, dry_run
        )

    def import_snapshot(
        self, input_file: str, dest_path: str, *, dry_run: bool = False
    ) -> bool:
        """Import a snapshot from a ``btrfs receive`` stream.

        Args:
            input_file: Path of the snapshot stream to import.
            dest_path: Destination subvolume directory for the import.
            dry_run: Preview only (default False).

        Returns:
            True on success.

        Raises:
            AuthorizationError: The caller lacks the manage-snapshots polkit
                action.
            OperationError: The btrfs operation failed.
            DaemonUnavailableError: The service is not running.

        Requires the manage-snapshots polkit action.
        """
        return self._call("ImportSnapshot", input_file, dest_path, dry_run)

    # -- Tier 3: manage policies ---------------------------------------------

    def apply_policy(
        self,
        *,
        subvolume: str,
        enabled: bool = True,
        schedule_hourly: bool = False,
        schedule_daily: bool = True,
        schedule_weekly: bool = False,
        schedule_monthly: bool = False,
        schedule_boot: bool = False,
        keep_hourly: int = 10,
        keep_daily: int = 7,
        keep_weekly: int = 4,
        keep_monthly: int = 3,
        keep_boot: int = 5,
        dry_run: bool = False,
    ) -> list[str]:
        """Apply a snapshot policy to a subvolume.

        Args:
            subvolume: Subvolume path the policy applies to.
            enabled: Whether the policy is enabled (default True).
            schedule_hourly: Enable the hourly schedule (default False).
            schedule_daily: Enable the daily schedule (default True).
            schedule_weekly: Enable the weekly schedule (default False).
            schedule_monthly: Enable the monthly schedule (default False).
            schedule_boot: Enable the boot schedule (default False).
            keep_hourly: Snapshots to keep in the hourly tier (default 10).
            keep_daily: Snapshots to keep in the daily tier (default 7).
            keep_weekly: Snapshots to keep in the weekly tier (default 4).
            keep_monthly: Snapshots to keep in the monthly tier (default 3).
            keep_boot: Snapshots to keep in the boot tier (default 5).
            dry_run: Preview only (default False).

        Returns:
            The unit file paths that were written.

        Raises:
            AuthorizationError: The caller lacks the manage-policies polkit
                action.
            OperationError: The policy is invalid or the systemd units could
                not be written.
            DaemonUnavailableError: The service is not running.

        Requires the manage-policies polkit action.
        """
        return self._call(
            "ApplyPolicy", subvolume, enabled, schedule_hourly,
            schedule_daily, schedule_weekly, schedule_monthly,
            schedule_boot, keep_hourly, keep_daily, keep_weekly,
            keep_monthly, keep_boot, dry_run,
        )

    def remove_policy(self, subvolume: str, *, dry_run: bool = False) -> list[str]:
        """Remove the snapshot policy of a subvolume.

        Args:
            subvolume: Subvolume path to remove the policy for.
            dry_run: Preview only (default False).

        Returns:
            The unit file paths that were removed.

        Raises:
            AuthorizationError: The caller lacks the manage-policies polkit
                action.
            OperationError: The systemd units could not be removed.
            DaemonUnavailableError: The service is not running.

        Requires the manage-policies polkit action.
        """
        return self._call("RemovePolicy", subvolume, dry_run)

    def set_config(
        self, default_subvolume: str = "", snapshot_dir: str = "",
        boot_from_snapshot: bool | None = None, *, dry_run: bool = False,
    ) -> bool:
        """Update the global configuration.

        Args:
            default_subvolume: The default subvolume path (default "").
            snapshot_dir: The snapshot directory name (default "").
            boot_from_snapshot: Enable booting from snapshots in GRUB
                (default None, leaves the value unchanged).
            dry_run: Preview only (default False).

        Returns:
            True on success.

        Raises:
            AuthorizationError: The caller lacks the manage-policies polkit
                action.
            OperationError: The configuration could not be saved.
            DaemonUnavailableError: The service is not running.

        Requires the manage-policies polkit action.
        """
        return self._call(
            "SetConfig", default_subvolume, snapshot_dir,
            bool(boot_from_snapshot), dry_run,
        )

    def setup_apply(self, profile_id: str = "", *, dry_run: bool = False) -> dict:
        """Apply a setup profile to the system.

        Args:
            profile_id: The profile ID to apply (default "", the default
                profile).
            dry_run: Preview only (default False).

        Returns:
            A dict describing what the profile applied.

        Raises:
            AuthorizationError: The caller lacks the manage-policies polkit
                action.
            InvalidArgumentError: Unknown profile ID.
            OperationError: The setup failed.
            DaemonUnavailableError: The service is not running.

        Requires the manage-policies polkit action.
        """
        return self._call("SetupApply", profile_id, dry_run)

    def grub_install(
        self, snapshot_dir: str = "/.snapshots", subvol_name: str = "root", *,
        dry_run: bool = False,
    ) -> bool:
        """Install the manaradix GRUB script.

        Args:
            snapshot_dir: Snapshot directory name used in the entries
                (default "/.snapshots").
            subvol_name: Subvolume name used in the entries (default
                "root").
            dry_run: Preview only (default False).

        Returns:
            True on success.

        Raises:
            AuthorizationError: The caller lacks the manage-policies polkit
                action.
            OperationError: The script could not be installed.
            DaemonUnavailableError: The service is not running.

        Requires the manage-policies polkit action.
        """
        return self._call("GrubInstall", snapshot_dir, subvol_name, dry_run)

    def grub_uninstall(self, *, dry_run: bool = False) -> bool:
        """Remove the manaradix GRUB script.

        Args:
            dry_run: Preview only (default False).

        Returns:
            True on success.

        Raises:
            AuthorizationError: The caller lacks the manage-policies polkit
                action.
            OperationError: The script could not be removed.
            DaemonUnavailableError: The service is not running.

        Requires the manage-policies polkit action.
        """
        return self._call("GrubUninstall", dry_run)

    def grub_update(self, *, dry_run: bool = False) -> bool:
        """Regenerate the GRUB configuration.

        Args:
            dry_run: Preview only (default False).

        Returns:
            True on success.

        Raises:
            AuthorizationError: The caller lacks the manage-policies polkit
                action.
            OperationError: GRUB regeneration failed.
            DaemonUnavailableError: The service is not running.

        Requires the manage-policies polkit action.
        """
        return self._call("GrubUpdate", dry_run)

    # -- Tier 4: manage filesystem -------------------------------------------

    def balance_start(
        self, mount: str = "/", *, dusage: int = 0, musage: int = 0,
        dry_run: bool = False,
    ) -> bool:
        """Start a balance of a btrfs filesystem.

        Args:
            mount: Mount point of the btrfs filesystem (default "/").
            dusage: Balance data usage threshold in percent (default 0).
            musage: Balance metadata usage threshold in percent (default 0).
            dry_run: Preview only (default False).

        Returns:
            True on success.

        Raises:
            AuthorizationError: The caller lacks the manage-filesystem polkit
                action.
            OperationError: The btrfs operation failed.
            DaemonUnavailableError: The service is not running.

        Requires the manage-filesystem polkit action.
        """
        return self._call("BalanceStart", mount, dusage, musage, dry_run)

    def balance_cancel(self, mount: str = "/", *, dry_run: bool = False) -> bool:
        """Cancel a running balance.

        Args:
            mount: Mount point of the btrfs filesystem (default "/").
            dry_run: Preview only (default False).

        Returns:
            True on success.

        Raises:
            AuthorizationError: The caller lacks the manage-filesystem polkit
                action.
            OperationError: The btrfs operation failed.
            DaemonUnavailableError: The service is not running.

        Requires the manage-filesystem polkit action.
        """
        return self._call("BalanceCancel", mount, dry_run)

    def scrub_start(self, mount: str = "/", *, dry_run: bool = False) -> bool:
        """Start a scrub of a btrfs filesystem.

        Args:
            mount: Mount point of the btrfs filesystem (default "/").
            dry_run: Preview only (default False).

        Returns:
            True on success.

        Raises:
            AuthorizationError: The caller lacks the manage-filesystem polkit
                action.
            OperationError: The btrfs operation failed.
            DaemonUnavailableError: The service is not running.

        Requires the manage-filesystem polkit action.
        """
        return self._call("ScrubStart", mount, dry_run)

    def scrub_cancel(self, mount: str = "/", *, dry_run: bool = False) -> bool:
        """Cancel a running scrub.

        Args:
            mount: Mount point of the btrfs filesystem (default "/").
            dry_run: Preview only (default False).

        Returns:
            True on success.

        Raises:
            AuthorizationError: The caller lacks the manage-filesystem polkit
                action.
            OperationError: The btrfs operation failed.
            DaemonUnavailableError: The service is not running.

        Requires the manage-filesystem polkit action.
        """
        return self._call("ScrubCancel", mount, dry_run)

    def defragment(
        self, path: str, *, recursive: bool = True, dry_run: bool = False
    ) -> bool:
        """Defragment a path on a btrfs filesystem.

        Args:
            path: Filesystem path to defragment.
            recursive: Defragment the directory tree recursively (default
                True).
            dry_run: Preview only (default False).

        Returns:
            True on success.

        Raises:
            AuthorizationError: The caller lacks the manage-filesystem polkit
                action.
            OperationError: The btrfs operation failed.
            DaemonUnavailableError: The service is not running.

        Requires the manage-filesystem polkit action.
        """
        return self._call("Defragment", path, recursive, dry_run)

    def set_compression(
        self, path: str, algorithm: str, *, dry_run: bool = False
    ) -> bool:
        """Set the compression algorithm of a subvolume.

        Args:
            path: Subvolume path.
            algorithm: Compression algorithm name (e.g. ``zstd``).
            dry_run: Preview only (default False).

        Returns:
            True on success.

        Raises:
            AuthorizationError: The caller lacks the manage-filesystem polkit
                action.
            OperationError: The btrfs operation failed.
            DaemonUnavailableError: The service is not running.

        Requires the manage-filesystem polkit action.
        """
        return self._call("SetCompression", path, algorithm, dry_run)

    def create_subvolume(self, path: str, *, dry_run: bool = False) -> bool:
        """Create a subvolume.

        Args:
            path: Path of the new subvolume.
            dry_run: Preview only (default False).

        Returns:
            True on success.

        Raises:
            AuthorizationError: The caller lacks the manage-filesystem polkit
                action.
            OperationError: The btrfs operation failed.
            DaemonUnavailableError: The service is not running.

        Requires the manage-filesystem polkit action.
        """
        return self._call("CreateSubvolume", path, dry_run)

    def delete_subvolume(self, path: str, *, dry_run: bool = False) -> bool:
        """Delete a subvolume.

        Args:
            path: Path of the subvolume to delete.
            dry_run: Preview only (default False).

        Returns:
            True on success.

        Raises:
            AuthorizationError: The caller lacks the manage-filesystem polkit
                action.
            OperationError: The btrfs operation failed.
            DaemonUnavailableError: The service is not running.

        Requires the manage-filesystem polkit action.
        """
        return self._call("DeleteSubvolume", path, dry_run)

    def set_default_subvolume(
        self, subvol_id: int, mount: str = "/", *, dry_run: bool = False
    ) -> bool:
        """Set the default subvolume of a btrfs filesystem.

        Args:
            subvol_id: The subvolume ID to make default.
            mount: Mount point of the btrfs filesystem (default "/").
            dry_run: Preview only (default False).

        Returns:
            True on success.

        Raises:
            AuthorizationError: The caller lacks the manage-filesystem polkit
                action.
            OperationError: The btrfs operation failed.
            DaemonUnavailableError: The service is not running.

        Requires the manage-filesystem polkit action.
        """
        return self._call("SetDefaultSubvolume", subvol_id, mount, dry_run)
