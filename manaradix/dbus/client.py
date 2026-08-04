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
        return self._call("Ping")

    def list_snapshots(self, mount: str = "/") -> list[dict]:
        return self._call("ListSnapshots", mount)

    def get_snapshot(self, snapshot_id: str, mount: str = "/") -> dict:
        return self._call("GetSnapshot", snapshot_id, mount)

    def list_subvolumes(self, mount: str = "/") -> list[dict]:
        return self._call("ListSubvolumes", mount)

    def filesystem_usage(self, mount: str = "/") -> dict:
        return self._call("FilesystemUsage", mount)

    def filesystem_show(self) -> list[dict]:
        return self._call("FilesystemShow")

    def btrfs_mounts(self) -> list[dict]:
        return self._call("BtrfsMounts")

    def balance_status(self, mount: str = "/") -> dict:
        return self._call("BalanceStatus", mount)

    def scrub_status(self, mount: str = "/") -> dict:
        return self._call("ScrubStatus", mount)

    def get_compression(self, path: str) -> str:
        return self._call("GetCompression", path)

    def list_policies(self) -> list[dict]:
        return self._call("ListPolicies")

    def get_config(self) -> dict:
        return self._call("GetConfig")

    def setup_status(self) -> dict:
        return self._call("SetupStatus")

    def list_setup_profiles(self) -> list[dict]:
        return self._call("ListSetupProfiles")

    def grub_is_installed(self) -> bool:
        return self._call("GrubIsInstalled")

    def grub_list_entries(self) -> list[str]:
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
        return self._call(
            "CreateSnapshot", subvolume, description, read_only,
            snapshot_type, schedule, mount, dry_run,
        )

    def delete_snapshot(
        self, snapshot_id: str, mount: str = "/", *, dry_run: bool = False
    ) -> bool:
        return self._call("DeleteSnapshot", snapshot_id, mount, dry_run)

    def prune_snapshots(
        self, subvolume: str, schedule: str, keep_count: int,
        mount: str = "/", *, dry_run: bool = False,
    ) -> list[str]:
        return self._call(
            "PruneSnapshots", subvolume, schedule, keep_count, mount, dry_run
        )

    def rename_snapshot(
        self, snapshot_id: str, mount: str, description: str
    ) -> dict:
        return self._call("RenameSnapshot", snapshot_id, mount, description)

    def restore_snapshot(
        self, snapshot_id: str, mount: str = "/", *, dry_run: bool = False
    ) -> dict:
        return self._call("RestoreSnapshot", snapshot_id, mount, dry_run)

    def browse_snapshot(
        self, snapshot_id: str, mount: str = "/",
        mount_point: str = BROWSE_MOUNT_POINT, *, dry_run: bool = False,
    ) -> bool:
        return self._call(
            "BrowseSnapshot", snapshot_id, mount, mount_point, dry_run
        )

    def unbrowse(self, mount_point: str = BROWSE_MOUNT_POINT, *,
                 dry_run: bool = False) -> bool:
        return self._call("Unbrowse", mount_point, dry_run)

    def export_snapshot(
        self, snapshot_id: str, mount: str, output_file: str, *,
        dry_run: bool = False,
    ) -> bool:
        return self._call(
            "ExportSnapshot", snapshot_id, mount, output_file, dry_run
        )

    def import_snapshot(
        self, input_file: str, dest_path: str, *, dry_run: bool = False
    ) -> bool:
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
        return self._call(
            "ApplyPolicy", subvolume, enabled, schedule_hourly,
            schedule_daily, schedule_weekly, schedule_monthly,
            schedule_boot, keep_hourly, keep_daily, keep_weekly,
            keep_monthly, keep_boot, dry_run,
        )

    def remove_policy(self, subvolume: str, *, dry_run: bool = False) -> list[str]:
        return self._call("RemovePolicy", subvolume, dry_run)

    def set_config(
        self, default_subvolume: str = "", snapshot_dir: str = "",
        boot_from_snapshot: bool | None = None, *, dry_run: bool = False,
    ) -> bool:
        return self._call(
            "SetConfig", default_subvolume, snapshot_dir,
            bool(boot_from_snapshot), dry_run,
        )

    def setup_apply(self, profile_id: str = "", *, dry_run: bool = False) -> dict:
        return self._call("SetupApply", profile_id, dry_run)

    def grub_install(
        self, snapshot_dir: str = "/.snapshots", subvol_name: str = "root", *,
        dry_run: bool = False,
    ) -> bool:
        return self._call("GrubInstall", snapshot_dir, subvol_name, dry_run)

    def grub_uninstall(self, *, dry_run: bool = False) -> bool:
        return self._call("GrubUninstall", dry_run)

    def grub_update(self, *, dry_run: bool = False) -> bool:
        return self._call("GrubUpdate", dry_run)

    # -- Tier 4: manage filesystem -------------------------------------------

    def balance_start(
        self, mount: str = "/", *, dusage: int = 0, musage: int = 0,
        dry_run: bool = False,
    ) -> bool:
        return self._call("BalanceStart", mount, dusage, musage, dry_run)

    def balance_cancel(self, mount: str = "/", *, dry_run: bool = False) -> bool:
        return self._call("BalanceCancel", mount, dry_run)

    def scrub_start(self, mount: str = "/", *, dry_run: bool = False) -> bool:
        return self._call("ScrubStart", mount, dry_run)

    def scrub_cancel(self, mount: str = "/", *, dry_run: bool = False) -> bool:
        return self._call("ScrubCancel", mount, dry_run)

    def defragment(
        self, path: str, *, recursive: bool = True, dry_run: bool = False
    ) -> bool:
        return self._call("Defragment", path, recursive, dry_run)

    def set_compression(
        self, path: str, algorithm: str, *, dry_run: bool = False
    ) -> bool:
        return self._call("SetCompression", path, algorithm, dry_run)

    def create_subvolume(self, path: str, *, dry_run: bool = False) -> bool:
        return self._call("CreateSubvolume", path, dry_run)

    def delete_subvolume(self, path: str, *, dry_run: bool = False) -> bool:
        return self._call("DeleteSubvolume", path, dry_run)

    def set_default_subvolume(
        self, subvol_id: int, mount: str = "/", *, dry_run: bool = False
    ) -> bool:
        return self._call("SetDefaultSubvolume", subvol_id, mount, dry_run)
