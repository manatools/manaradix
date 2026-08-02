"""D-Bus wire layer: the ``dbus.service.Object`` and the daemon entry point.

:class:`ManaradixService` registers the 41 methods defined by
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
    def Ping(self, sender=None):
        return self._dispatch("Ping", {}, sender)

    @dbus.service.method(DBUS_INTERFACE, in_signature="s", out_signature="aa{sv}",
                         sender_keyword="sender")
    def ListSnapshots(self, mount, sender=None):
        return self._dispatch("ListSnapshots", {"mount": mount}, sender)

    @dbus.service.method(DBUS_INTERFACE, in_signature="ss", out_signature="a{sv}",
                         sender_keyword="sender")
    def GetSnapshot(self, snapshot_id, mount, sender=None):
        return self._dispatch("GetSnapshot", {"id": snapshot_id, "mount": mount},
                              sender)

    @dbus.service.method(DBUS_INTERFACE, in_signature="s", out_signature="aa{sv}",
                         sender_keyword="sender")
    def ListSubvolumes(self, mount, sender=None):
        return self._dispatch("ListSubvolumes", {"mount": mount}, sender)

    @dbus.service.method(DBUS_INTERFACE, in_signature="s", out_signature="a{sv}",
                         sender_keyword="sender")
    def FilesystemUsage(self, mount, sender=None):
        return self._dispatch("FilesystemUsage", {"mount": mount}, sender)

    @dbus.service.method(DBUS_INTERFACE, out_signature="aa{sv}",
                         sender_keyword="sender")
    def FilesystemShow(self, sender=None):
        return self._dispatch("FilesystemShow", {}, sender)

    @dbus.service.method(DBUS_INTERFACE, out_signature="aa{sv}",
                         sender_keyword="sender")
    def BtrfsMounts(self, sender=None):
        return self._dispatch("BtrfsMounts", {}, sender)

    @dbus.service.method(DBUS_INTERFACE, in_signature="s", out_signature="a{sv}",
                         sender_keyword="sender")
    def BalanceStatus(self, mount, sender=None):
        return self._dispatch("BalanceStatus", {"mount": mount}, sender)

    @dbus.service.method(DBUS_INTERFACE, in_signature="s", out_signature="a{sv}",
                         sender_keyword="sender")
    def ScrubStatus(self, mount, sender=None):
        return self._dispatch("ScrubStatus", {"mount": mount}, sender)

    @dbus.service.method(DBUS_INTERFACE, in_signature="s", out_signature="s",
                         sender_keyword="sender")
    def GetCompression(self, path, sender=None):
        return self._dispatch("GetCompression", {"path": path}, sender)

    @dbus.service.method(DBUS_INTERFACE, out_signature="aa{sv}",
                         sender_keyword="sender")
    def ListPolicies(self, sender=None):
        return self._dispatch("ListPolicies", {}, sender)

    @dbus.service.method(DBUS_INTERFACE, out_signature="a{sv}",
                         sender_keyword="sender")
    def GetConfig(self, sender=None):
        return self._dispatch("GetConfig", {}, sender)

    @dbus.service.method(DBUS_INTERFACE, out_signature="a{sv}",
                         sender_keyword="sender")
    def SetupStatus(self, sender=None):
        return self._dispatch("SetupStatus", {}, sender)

    @dbus.service.method(DBUS_INTERFACE, out_signature="aa{sv}",
                         sender_keyword="sender")
    def ListSetupProfiles(self, sender=None):
        return self._dispatch("ListSetupProfiles", {}, sender)

    @dbus.service.method(DBUS_INTERFACE, out_signature="b",
                         sender_keyword="sender")
    def GrubIsInstalled(self, sender=None):
        return self._dispatch("GrubIsInstalled", {}, sender)

    @dbus.service.method(DBUS_INTERFACE, out_signature="as",
                         sender_keyword="sender")
    def GrubListEntries(self, sender=None):
        return self._dispatch("GrubListEntries", {}, sender)

    # -- Tier 2: manage snapshots -------------------------------------------

    @dbus.service.method(DBUS_INTERFACE, in_signature="ssbsssb",
                         out_signature="a{sv}", sender_keyword="sender")
    def CreateSnapshot(self, subvolume, description, read_only, snapshot_type,
                       schedule, mount, dry_run, sender=None):
        return self._dispatch(
            "CreateSnapshot",
            {"subvolume": subvolume, "description": description,
             "read_only": read_only, "snapshot_type": snapshot_type,
             "schedule": schedule, "mount": mount, "dry_run": dry_run},
            sender,
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="ssb", out_signature="b",
                         sender_keyword="sender")
    def DeleteSnapshot(self, snapshot_id, mount, dry_run, sender=None):
        return self._dispatch(
            "DeleteSnapshot", {"id": snapshot_id, "mount": mount,
                               "dry_run": dry_run}, sender
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="ssisb",
                         out_signature="as", sender_keyword="sender")
    def PruneSnapshots(self, subvolume, schedule, keep_count, mount, dry_run,
                       sender=None):
        return self._dispatch(
            "PruneSnapshots",
            {"subvolume": subvolume, "schedule": schedule,
             "keep_count": keep_count, "mount": mount, "dry_run": dry_run},
            sender,
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="sss", out_signature="a{sv}",
                         sender_keyword="sender")
    def RenameSnapshot(self, snapshot_id, mount, description, sender=None):
        return self._dispatch(
            "RenameSnapshot", {"id": snapshot_id, "mount": mount,
                               "description": description}, sender
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="ssb", out_signature="a{sv}",
                         sender_keyword="sender")
    def RestoreSnapshot(self, snapshot_id, mount, dry_run, sender=None):
        return self._dispatch(
            "RestoreSnapshot", {"id": snapshot_id, "mount": mount,
                                "dry_run": dry_run}, sender
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="sssb", out_signature="b",
                         sender_keyword="sender")
    def BrowseSnapshot(self, snapshot_id, mount, mount_point, dry_run,
                       sender=None):
        return self._dispatch(
            "BrowseSnapshot",
            {"id": snapshot_id, "mount": mount, "mount_point": mount_point,
             "dry_run": dry_run},
            sender,
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="sb", out_signature="b",
                         sender_keyword="sender")
    def Unbrowse(self, mount_point, dry_run, sender=None):
        return self._dispatch(
            "Unbrowse", {"mount_point": mount_point, "dry_run": dry_run},
            sender,
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="sssb", out_signature="b",
                         sender_keyword="sender")
    def ExportSnapshot(self, snapshot_id, mount, output_file, dry_run,
                       sender=None):
        return self._dispatch(
            "ExportSnapshot",
            {"id": snapshot_id, "mount": mount, "output_file": output_file,
             "dry_run": dry_run},
            sender,
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="ssb", out_signature="b",
                         sender_keyword="sender")
    def ImportSnapshot(self, input_file, dest_path, dry_run, sender=None):
        return self._dispatch(
            "ImportSnapshot", {"input_file": input_file,
                               "dest_path": dest_path, "dry_run": dry_run},
            sender,
        )

    # -- Tier 3: manage policies ---------------------------------------------

    @dbus.service.method(DBUS_INTERFACE, in_signature="sbbbbbbiiiiib",
                         out_signature="as", sender_keyword="sender")
    def ApplyPolicy(self, subvolume, enabled, schedule_hourly, schedule_daily,
                    schedule_weekly, schedule_monthly, schedule_boot,
                    keep_hourly, keep_daily, keep_weekly, keep_monthly,
                    keep_boot, dry_run, sender=None):
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
    def RemovePolicy(self, subvolume, dry_run, sender=None):
        return self._dispatch(
            "RemovePolicy", {"subvolume": subvolume, "dry_run": dry_run},
            sender,
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="ssbb", out_signature="b",
                         sender_keyword="sender")
    def SetConfig(self, default_subvolume, snapshot_dir, boot_from_snapshot,
                  dry_run, sender=None):
        return self._dispatch(
            "SetConfig",
            {"default_subvolume": default_subvolume,
             "snapshot_dir": snapshot_dir,
             "boot_from_snapshot": boot_from_snapshot, "dry_run": dry_run},
            sender,
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="sb", out_signature="a{sv}",
                         sender_keyword="sender")
    def SetupApply(self, profile_id, dry_run, sender=None):
        return self._dispatch(
            "SetupApply", {"profile_id": profile_id, "dry_run": dry_run},
            sender,
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="ssb", out_signature="b",
                         sender_keyword="sender")
    def GrubInstall(self, snapshot_dir, subvol_name, dry_run, sender=None):
        return self._dispatch(
            "GrubInstall", {"snapshot_dir": snapshot_dir,
                            "subvol_name": subvol_name, "dry_run": dry_run},
            sender,
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="b", out_signature="b",
                         sender_keyword="sender")
    def GrubUninstall(self, dry_run, sender=None):
        return self._dispatch(
            "GrubUninstall", {"dry_run": dry_run}, sender
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="b", out_signature="b",
                         sender_keyword="sender")
    def GrubUpdate(self, dry_run, sender=None):
        return self._dispatch("GrubUpdate", {"dry_run": dry_run}, sender)

    # -- Tier 4: manage filesystem -------------------------------------------

    @dbus.service.method(DBUS_INTERFACE, in_signature="siib", out_signature="b",
                         sender_keyword="sender")
    def BalanceStart(self, mount, dusage, musage, dry_run, sender=None):
        return self._dispatch(
            "BalanceStart", {"mount": mount, "dusage": dusage,
                             "musage": musage, "dry_run": dry_run}, sender
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="sb", out_signature="b",
                         sender_keyword="sender")
    def BalanceCancel(self, mount, dry_run, sender=None):
        return self._dispatch(
            "BalanceCancel", {"mount": mount, "dry_run": dry_run}, sender
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="sb", out_signature="b",
                         sender_keyword="sender")
    def ScrubStart(self, mount, dry_run, sender=None):
        return self._dispatch(
            "ScrubStart", {"mount": mount, "dry_run": dry_run}, sender
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="sb", out_signature="b",
                         sender_keyword="sender")
    def ScrubCancel(self, mount, dry_run, sender=None):
        return self._dispatch(
            "ScrubCancel", {"mount": mount, "dry_run": dry_run}, sender
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="sbb", out_signature="b",
                         sender_keyword="sender")
    def Defragment(self, path, recursive, dry_run, sender=None):
        return self._dispatch(
            "Defragment", {"path": path, "recursive": recursive,
                           "dry_run": dry_run}, sender
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="ssb", out_signature="b",
                         sender_keyword="sender")
    def SetCompression(self, path, algorithm, dry_run, sender=None):
        return self._dispatch(
            "SetCompression", {"path": path, "algorithm": algorithm,
                               "dry_run": dry_run}, sender
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="sb", out_signature="b",
                         sender_keyword="sender")
    def CreateSubvolume(self, path, dry_run, sender=None):
        return self._dispatch(
            "CreateSubvolume", {"path": path, "dry_run": dry_run}, sender
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="sb", out_signature="b",
                         sender_keyword="sender")
    def DeleteSubvolume(self, path, dry_run, sender=None):
        return self._dispatch(
            "DeleteSubvolume", {"path": path, "dry_run": dry_run}, sender
        )

    @dbus.service.method(DBUS_INTERFACE, in_signature="isb", out_signature="b",
                         sender_keyword="sender")
    def SetDefaultSubvolume(self, subvol_id, mount, dry_run, sender=None):
        return self._dispatch(
            "SetDefaultSubvolume", {"id": subvol_id, "mount": mount,
                                    "dry_run": dry_run}, sender
        )


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
