"""D-Bus service logic: polkit check + delegation to the core services.

:class:`ManaradixHandler` is the pure-logic heart of the daemon: for
every D-Bus method it resolves the caller's PID, authorizes the caller
against the method's polkit action (tier), then delegates to the
existing core services — no business logic lives here (CLI-first
design).  It imports no dbus-python, so tests exercise it directly with
fakes; the ``dbus.service.Object`` wire class is added in
:mod:`manaradix.dbus.wire`.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from manaradix.core.btrfs_ops import (
    BtrfsError,
    SnapshotNotFoundError,
    SubvolumeNotFoundError,
)
from manaradix.core.config_store import ConfigError
from manaradix.core.grub_integration import GrubIntegrationError
from manaradix.core.policy_engine import PolicyEngineError
from manaradix.core.snapshot_manager import BROWSE_MOUNT_POINT
from manaradix.core.setup_wizard import (
    detect_role,
    needs_setup,
    select_default_profile,
    system_layout,
)
from manaradix.core.templates import load_profiles
from manaradix.dbus import polkit
from manaradix.dbus.constants import METHOD_ACTIONS
from manaradix.dbus.convert import (
    _b,
    _i,
    _s,
    balance_status_to_dbus,
    policy_from_args,
    policy_to_dbus,
    profile_to_dbus,
    scrub_status_to_dbus,
    setup_status_to_dbus,
    snapshot_to_dbus,
    subvolume_to_dbus,
)
from manaradix.dbus.errors import (
    AuthorizationError,
    InvalidArgumentError,
    ManaradixDBusError,
    NotFoundError,
    OperationError,
)
from manaradix.i18n import _
from manaradix.model.snapshot import SnapshotType

logger = logging.getLogger(__name__)


class ManaradixHandler:
    """Pure per-method logic: authorization + core delegation.

    *services* is an :class:`~manaradix.module.AppContext`.  The auth
    check and PID resolver are injectable so tests never touch polkit
    or the bus.
    """

    def __init__(
        self,
        services,
        *,
        auth_check: Callable[[int, str], None] = polkit.check_authorization,
        pid_resolver: Callable[[Any, str], int] = polkit.sender_pid,
    ) -> None:
        self._ctx = services
        self._auth_check = auth_check
        self._pid_resolver = pid_resolver

    # -- Dispatch -----------------------------------------------------------

    def dispatch(self, method_name: str, kwargs: dict, sender: str,
                 connection=None) -> Any:
        """Authorize the *sender* for *method_name*, then run its handler.

        Core exceptions are mapped onto
        :class:`~manaradix.dbus.errors.ManaradixDBusError` subclasses
        (the wire class translates those into D-Bus error names).
        """
        action = METHOD_ACTIONS.get(method_name)
        if action is None:
            raise InvalidArgumentError(_("Unknown method: %s") % method_name)
        pid = self._pid_resolver(connection, sender)
        self._auth_check(pid, action)

        handler = getattr(self, f"handle_{method_name}", None)
        if handler is None:
            raise InvalidArgumentError(_("Unhandled method: %s") % method_name)
        try:
            return handler(kwargs)
        except ManaradixDBusError:
            raise
        except SnapshotNotFoundError as exc:
            raise NotFoundError(str(exc)) from exc
        except SubvolumeNotFoundError as exc:
            raise NotFoundError(str(exc)) from exc
        except (ValueError, TypeError) as exc:
            raise InvalidArgumentError(str(exc)) from exc
        except (BtrfsError, ConfigError, PolicyEngineError,
                GrubIntegrationError, OSError) as exc:
            raise OperationError(str(exc)) from exc

    # -- Helpers ------------------------------------------------------------

    def _get_snapshot(self, kwargs: dict) -> Any:
        """Resolve a snapshot by id, raising NotFoundError when missing."""
        snap = self._ctx.snapshot_manager.get(
            _s(kwargs["id"]), _s(kwargs.get("mount", "/"))
        )
        if snap is None:
            raise NotFoundError(
                _("Snapshot not found: %s") % kwargs["id"]
            )
        return snap

    def _snapshot_type(self, raw: str) -> SnapshotType:
        """Parse a snapshot type string; invalid values raise InvalidArgument."""
        try:
            return SnapshotType(raw)
        except ValueError as exc:
            raise InvalidArgumentError(
                _("Invalid snapshot type: %s") % raw
            ) from exc

    # -- Tier 1: read -------------------------------------------------------

    def handle_Ping(self, kwargs: dict) -> str:
        """Return the service version string.

        Args:
            kwargs: Unused method arguments.

        Returns:
            The ``__version__`` string.
        """
        from manaradix import __version__

        return __version__

    def handle_ListSnapshots(self, kwargs: dict) -> list[dict]:
        """List the snapshots on a btrfs filesystem.

        Args:
            kwargs: Method arguments (``mount``).

        Returns:
            One dict per snapshot.

        Raises:
            org.mageia.manaradix.Error.Failed: The btrfs query failed.
        """
        snaps = self._ctx.snapshot_manager.list_all(
            _s(kwargs.get("mount", "/"))
        )
        return [snapshot_to_dbus(s) for s in snaps]

    def handle_GetSnapshot(self, kwargs: dict) -> dict:
        """Get one snapshot by its ID.

        Args:
            kwargs: Method arguments (``id``, ``mount``).

        Returns:
            The snapshot as a dict.

        Raises:
            org.mageia.manaradix.Error.NotFound: No snapshot with that ID.
            org.mageia.manaradix.Error.Failed: The btrfs query failed.
        """
        return snapshot_to_dbus(self._get_snapshot(kwargs))

    def handle_ListSubvolumes(self, kwargs: dict) -> list[dict]:
        """List the subvolumes on a btrfs filesystem.

        Args:
            kwargs: Method arguments (``mount``).

        Returns:
            One dict per subvolume.

        Raises:
            org.mageia.manaradix.Error.Failed: The btrfs query failed.
        """
        subvols = self._ctx.btrfs_ops.list_subvolumes(
            _s(kwargs.get("mount", "/"))
        )
        return [subvolume_to_dbus(s) for s in subvols]

    def handle_FilesystemUsage(self, kwargs: dict) -> dict:
        """Get the usage of a btrfs filesystem.

        Args:
            kwargs: Method arguments (``mount``).

        Returns:
            The filesystem usage dict.

        Raises:
            org.mageia.manaradix.Error.Failed: The btrfs query failed.
        """
        return self._ctx.btrfs_ops.filesystem_usage(
            _s(kwargs.get("mount", "/"))
        )

    def handle_FilesystemShow(self, kwargs: dict) -> list[dict]:
        """List all btrfs filesystems with their devices and sizes.

        Args:
            kwargs: Unused method arguments.

        Returns:
            One dict per filesystem.

        Raises:
            org.mageia.manaradix.Error.Failed: The btrfs query failed.
        """
        return self._ctx.btrfs_ops.filesystem_show()

    def handle_BtrfsMounts(self, kwargs: dict) -> list[dict]:
        """List the mounted btrfs filesystems.

        Args:
            kwargs: Unused method arguments.

        Returns:
            One dict per mount.

        Raises:
            org.mageia.manaradix.Error.Failed: The btrfs query failed.
        """
        return self._ctx.btrfs_ops.btrfs_mounts()

    def handle_BalanceStatus(self, kwargs: dict) -> dict:
        """Get the balance status of a btrfs filesystem.

        Args:
            kwargs: Method arguments (``mount``).

        Returns:
            The balance status dict (total / relocated / running).

        Raises:
            org.mageia.manaradix.Error.Failed: The btrfs query failed.
        """
        return balance_status_to_dbus(
            self._ctx.btrfs_ops.balance_status(_s(kwargs.get("mount", "/")))
        )

    def handle_ScrubStatus(self, kwargs: dict) -> dict:
        """Get the scrub status of a btrfs filesystem.

        Args:
            kwargs: Method arguments (``mount``).

        Returns:
            The scrub status dict (extents / errors / running).

        Raises:
            org.mageia.manaradix.Error.Failed: The btrfs query failed.
        """
        return scrub_status_to_dbus(
            self._ctx.btrfs_ops.scrub_status(_s(kwargs.get("mount", "/")))
        )

    def handle_GetCompression(self, kwargs: dict) -> str:
        """Get the compression algorithm of a subvolume.

        Args:
            kwargs: Method arguments (``path``).

        Returns:
            The compression algorithm name (empty string when unset).

        Raises:
            org.mageia.manaradix.Error.Failed: The btrfs query failed.
        """
        return _s(
            self._ctx.btrfs_ops.get_compression(_s(kwargs["path"])) or ""
        )

    def handle_ListPolicies(self, kwargs: dict) -> list[dict]:
        """List the configured snapshot policies.

        Args:
            kwargs: Unused method arguments.

        Returns:
            One dict per policy.

        Raises:
            org.mageia.manaradix.Error.Failed: The configuration could not
                be read.
        """
        return [
            policy_to_dbus(p)
            for p in self._ctx.config_store.list_policies()
        ]

    def handle_GetConfig(self, kwargs: dict) -> dict:
        """Get the global configuration.

        Args:
            kwargs: Unused method arguments.

        Returns:
            The configuration dict.

        Raises:
            org.mageia.manaradix.Error.Failed: The configuration could not
                be read.
        """
        return self._ctx.config_store.to_dict()

    def handle_SetupStatus(self, kwargs: dict) -> dict:
        """Report whether the setup wizard is needed and the system role.

        Args:
            kwargs: Unused method arguments.

        Returns:
            The setup status dict (needs_setup / role / default_profile /
            root_is_btrfs / grub_present).

        Raises:
            org.mageia.manaradix.Error.Failed: The system layout could not
                be determined.
        """
        layout = system_layout(self._ctx)
        role = detect_role()
        profiles = load_profiles()
        default = select_default_profile(profiles, role)
        return setup_status_to_dbus(
            needs_setup=needs_setup(self._ctx),
            role=role,
            default_profile=(
                profile_to_dbus(default) if default is not None else {}
            ),
            root_is_btrfs=layout["root_is_btrfs"],
            grub_present=layout["grub_present"],
        )

    def handle_ListSetupProfiles(self, kwargs: dict) -> list[dict]:
        """List the available setup profiles.

        Args:
            kwargs: Unused method arguments.

        Returns:
            One dict per profile.
        """
        return [profile_to_dbus(p) for p in load_profiles()]

    def handle_GrubIsInstalled(self, kwargs: dict) -> bool:
        """Check whether the manaradix GRUB script is installed.

        Args:
            kwargs: Unused method arguments.

        Returns:
            True when ``/etc/grub.d/41_manaradix`` exists.
        """
        return self._ctx.grub_integration.is_installed()

    def handle_GrubListEntries(self, kwargs: dict) -> list[str]:
        """List the snapshot entries of the manaradix GRUB script.

        Args:
            kwargs: Unused method arguments.

        Returns:
            The entry labels (empty when the script is not installed).
        """
        return self._ctx.grub_integration.list_snapshot_entries()

    # -- Tier 2: manage snapshots -------------------------------------------

    def handle_CreateSnapshot(self, kwargs: dict) -> dict:
        """Create a snapshot of a subvolume.

        Args:
            kwargs: Method arguments (``subvolume``, ``description``,
                ``read_only``, ``snapshot_type``, ``schedule``, ``mount``,
                ``dry_run``).

        Returns:
            The created snapshot as a dict.

        Raises:
            org.mageia.manaradix.Error.InvalidArgs: Unknown snapshot type.
            org.mageia.manaradix.Error.Failed: The btrfs operation failed.
        """
        snap = self._ctx.snapshot_manager.create(
            _s(kwargs.get("subvolume", "/")),
            description=_s(kwargs.get("description", "")),
            read_only=_b(kwargs.get("read_only", True)),
            snapshot_type=self._snapshot_type(
                _s(kwargs.get("snapshot_type", "manual"))
            ),
            schedule=_s(kwargs.get("schedule", "")),
            mount=_s(kwargs.get("mount", "/")),
            dry_run=_b(kwargs.get("dry_run", False)),
        )
        return snapshot_to_dbus(snap)

    def handle_DeleteSnapshot(self, kwargs: dict) -> bool:
        """Delete a snapshot and its metadata.

        Args:
            kwargs: Method arguments (``id``, ``mount``, ``dry_run``).

        Returns:
            True on success.

        Raises:
            org.mageia.manaradix.Error.NotFound: No snapshot with that ID.
            org.mageia.manaradix.Error.Failed: The btrfs operation failed.
        """
        self._ctx.snapshot_manager.delete(
            self._get_snapshot(kwargs),
            dry_run=_b(kwargs.get("dry_run", False)),
        )
        return True

    def handle_PruneSnapshots(self, kwargs: dict) -> list[str]:
        """Prune a subvolume/schedule tier, keeping the most recent snapshots.

        Args:
            kwargs: Method arguments (``subvolume``, ``schedule``,
                ``keep_count``, ``mount``, ``dry_run``).

        Returns:
            The IDs of the deleted snapshots.

        Raises:
            org.mageia.manaradix.Error.InvalidArgs: Negative *keep_count*.
            org.mageia.manaradix.Error.Failed: The btrfs operation failed.
        """
        return self._ctx.snapshot_manager.prune(
            _s(kwargs["subvolume"]),
            _s(kwargs["schedule"]),
            _i(kwargs["keep_count"]),
            mount=_s(kwargs.get("mount", "/")),
            dry_run=_b(kwargs.get("dry_run", False)),
        )

    def handle_RenameSnapshot(self, kwargs: dict) -> dict:
        """Rename a snapshot (update its description).

        Args:
            kwargs: Method arguments (``id``, ``mount``, ``description``).

        Returns:
            The renamed snapshot as a dict.

        Raises:
            org.mageia.manaradix.Error.NotFound: No snapshot with that ID.
            org.mageia.manaradix.Error.Failed: The btrfs operation failed.
        """
        snap = self._ctx.snapshot_manager.rename(
            self._get_snapshot(kwargs),
            _s(kwargs["description"]),
        )
        return snapshot_to_dbus(snap)

    def handle_RestoreSnapshot(self, kwargs: dict) -> dict:
        """Restore a snapshot (swap method).

        Args:
            kwargs: Method arguments (``id``, ``mount``, ``dry_run``).

        Returns:
            The pre-restore snapshot as a dict.

        Raises:
            org.mageia.manaradix.Error.NotFound: No snapshot with that ID.
            org.mageia.manaradix.Error.Failed: The btrfs operation failed.
        """
        pre_restore = self._ctx.snapshot_manager.restore(
            self._get_snapshot(kwargs),
            mount=_s(kwargs.get("mount", "/")),
            dry_run=_b(kwargs.get("dry_run", False)),
        )
        return snapshot_to_dbus(pre_restore)

    def handle_BrowseSnapshot(self, kwargs: dict) -> bool:
        """Mount a snapshot read-only for browsing.

        Args:
            kwargs: Method arguments (``id``, ``mount``, ``mount_point``,
                ``dry_run``).

        Returns:
            True on success.

        Raises:
            org.mageia.manaradix.Error.NotFound: No snapshot with that ID.
            org.mageia.manaradix.Error.Failed: The btrfs operation failed.
        """
        self._ctx.snapshot_manager.browse(
            self._get_snapshot(kwargs),
            _s(kwargs.get("mount_point", BROWSE_MOUNT_POINT)),
            dry_run=_b(kwargs.get("dry_run", False)),
        )
        return True

    def handle_Unbrowse(self, kwargs: dict) -> bool:
        """Unmount a snapshot mounted for browsing.

        Args:
            kwargs: Method arguments (``mount_point``, ``dry_run``).

        Returns:
            True on success.

        Raises:
            org.mageia.manaradix.Error.Failed: The btrfs operation failed.
        """
        self._ctx.snapshot_manager.unbrowse(
            _s(kwargs.get("mount_point", BROWSE_MOUNT_POINT)),
            dry_run=_b(kwargs.get("dry_run", False)),
        )
        return True

    def handle_ExportSnapshot(self, kwargs: dict) -> bool:
        """Export a snapshot as a ``btrfs send`` stream.

        Args:
            kwargs: Method arguments (``id``, ``mount``, ``output_file``,
                ``dry_run``).

        Returns:
            True on success.

        Raises:
            org.mageia.manaradix.Error.NotFound: No snapshot with that ID.
            org.mageia.manaradix.Error.Failed: The btrfs operation failed.
        """
        self._ctx.snapshot_manager.export_snapshot(
            self._get_snapshot(kwargs),
            _s(kwargs["output_file"]),
            dry_run=_b(kwargs.get("dry_run", False)),
        )
        return True

    def handle_ImportSnapshot(self, kwargs: dict) -> bool:
        """Import a snapshot from a ``btrfs receive`` stream.

        Args:
            kwargs: Method arguments (``input_file``, ``dest_path``,
                ``dry_run``).

        Returns:
            True on success.

        Raises:
            org.mageia.manaradix.Error.Failed: The btrfs operation failed.
        """
        self._ctx.snapshot_manager.import_snapshot(
            _s(kwargs["input_file"]),
            _s(kwargs["dest_path"]),
            dry_run=_b(kwargs.get("dry_run", False)),
        )
        return True

    # -- Tier 3: manage policies ---------------------------------------------

    def handle_ApplyPolicy(self, kwargs: dict) -> list[str]:
        """Apply a snapshot policy to a subvolume.

        Args:
            kwargs: Method arguments (``subvolume``, ``enabled``,
                ``schedule_hourly``, ``schedule_daily``, ``schedule_weekly``,
                ``schedule_monthly``, ``schedule_boot``, ``keep_hourly``,
                ``keep_daily``, ``keep_weekly``, ``keep_monthly``,
                ``keep_boot``, ``dry_run``).

        Returns:
            The unit file paths that were written.

        Raises:
            org.mageia.manaradix.Error.Failed: The policy is invalid or the
                systemd units could not be written.
        """
        policy = policy_from_args(
            subvolume=kwargs["subvolume"],
            enabled=kwargs.get("enabled", True),
            schedule_hourly=kwargs.get("schedule_hourly", False),
            schedule_daily=kwargs.get("schedule_daily", True),
            schedule_weekly=kwargs.get("schedule_weekly", False),
            schedule_monthly=kwargs.get("schedule_monthly", False),
            schedule_boot=kwargs.get("schedule_boot", False),
            keep_hourly=kwargs.get("keep_hourly", 10),
            keep_daily=kwargs.get("keep_daily", 7),
            keep_weekly=kwargs.get("keep_weekly", 4),
            keep_monthly=kwargs.get("keep_monthly", 3),
            keep_boot=kwargs.get("keep_boot", 5),
        )
        dry_run = _b(kwargs.get("dry_run", False))
        written = self._ctx.policy_engine.apply_policy(policy, dry_run=dry_run)
        if not dry_run:
            self._ctx.config_store.set_policy(policy)
            self._ctx.config_store.save()
        return written

    def handle_RemovePolicy(self, kwargs: dict) -> list[str]:
        """Remove the snapshot policy of a subvolume.

        Args:
            kwargs: Method arguments (``subvolume``, ``dry_run``).

        Returns:
            The unit file paths that were removed.

        Raises:
            org.mageia.manaradix.Error.Failed: The systemd units could not
                be removed.
        """
        subvolume = _s(kwargs["subvolume"])
        dry_run = _b(kwargs.get("dry_run", False))
        removed = self._ctx.policy_engine.remove_policy(
            subvolume, dry_run=dry_run
        )
        if not dry_run:
            self._ctx.config_store.remove_policy(subvolume)
            self._ctx.config_store.save()
        return removed

    def handle_SetConfig(self, kwargs: dict) -> bool:
        """Update the global configuration.

        Args:
            kwargs: Method arguments (``default_subvolume``,
                ``snapshot_dir``, ``boot_from_snapshot``, ``dry_run``).

        Returns:
            True on success.

        Raises:
            org.mageia.manaradix.Error.Failed: The configuration could not
                be saved.
        """
        store = self._ctx.config_store
        dry_run = _b(kwargs.get("dry_run", False))
        if kwargs.get("default_subvolume"):
            store.default_subvolume = _s(kwargs["default_subvolume"])
        if kwargs.get("snapshot_dir"):
            store.snapshot_dir = _s(kwargs["snapshot_dir"])
        if kwargs.get("boot_from_snapshot") is not None:
            store.boot_from_snapshot = _b(kwargs["boot_from_snapshot"])
        if not dry_run:
            store.save()
        return True

    def handle_SetupApply(self, kwargs: dict) -> dict:
        """Apply a setup profile to the system.

        Args:
            kwargs: Method arguments (``profile_id``, ``dry_run``).

        Returns:
            A dict describing what the profile applied.

        Raises:
            org.mageia.manaradix.Error.InvalidArgs: Unknown profile ID.
            org.mageia.manaradix.Error.Failed: The setup failed.
        """
        from manaradix.core.setup_wizard import apply_profile

        profile_id = _s(kwargs.get("profile_id", "")) or None
        return apply_profile(
            self._ctx,
            profile_id,
            dry_run=_b(kwargs.get("dry_run", False)),
        )

    def handle_GrubInstall(self, kwargs: dict) -> bool:
        """Install the manaradix GRUB script.

        Args:
            kwargs: Method arguments (``snapshot_dir``, ``subvol_name``,
                ``dry_run``).

        Returns:
            True on success.

        Raises:
            org.mageia.manaradix.Error.Failed: The script could not be
                installed.
        """
        self._ctx.grub_integration.install_script(
            _s(kwargs.get("snapshot_dir", "/.snapshots")),
            _s(kwargs.get("subvol_name", "root")),
            dry_run=_b(kwargs.get("dry_run", False)),
        )
        return True

    def handle_GrubUninstall(self, kwargs: dict) -> bool:
        """Remove the manaradix GRUB script.

        Args:
            kwargs: Method arguments (``dry_run``).

        Returns:
            True on success.

        Raises:
            org.mageia.manaradix.Error.Failed: The script could not be
                removed.
        """
        self._ctx.grub_integration.uninstall_script(
            dry_run=_b(kwargs.get("dry_run", False))
        )
        return True

    def handle_GrubUpdate(self, kwargs: dict) -> bool:
        """Regenerate the GRUB configuration.

        Args:
            kwargs: Method arguments (``dry_run``).

        Returns:
            True on success.

        Raises:
            org.mageia.manaradix.Error.Failed: GRUB regeneration failed.
        """
        self._ctx.grub_integration.update_grub(
            dry_run=_b(kwargs.get("dry_run", False))
        )
        return True

    # -- Tier 4: manage filesystem -------------------------------------------

    def handle_BalanceStart(self, kwargs: dict) -> bool:
        """Start a balance of a btrfs filesystem.

        Args:
            kwargs: Method arguments (``mount``, ``dusage``, ``musage``,
                ``dry_run``).

        Returns:
            True on success.

        Raises:
            org.mageia.manaradix.Error.Failed: The btrfs operation failed.
        """
        self._ctx.btrfs_ops.balance_start(
            _s(kwargs.get("mount", "/")),
            dusage=_i(kwargs.get("dusage", 0)),
            musage=_i(kwargs.get("musage", 0)),
            dry_run=_b(kwargs.get("dry_run", False)),
        )
        return True

    def handle_BalanceCancel(self, kwargs: dict) -> bool:
        """Cancel a running balance.

        Args:
            kwargs: Method arguments (``mount``, ``dry_run``).

        Returns:
            True on success.

        Raises:
            org.mageia.manaradix.Error.Failed: The btrfs operation failed.
        """
        self._ctx.btrfs_ops.balance_cancel(
            _s(kwargs.get("mount", "/")),
            dry_run=_b(kwargs.get("dry_run", False)),
        )
        return True

    def handle_ScrubStart(self, kwargs: dict) -> bool:
        """Start a scrub of a btrfs filesystem.

        Args:
            kwargs: Method arguments (``mount``, ``dry_run``).

        Returns:
            True on success.

        Raises:
            org.mageia.manaradix.Error.Failed: The btrfs operation failed.
        """
        self._ctx.btrfs_ops.scrub_start(
            _s(kwargs.get("mount", "/")),
            dry_run=_b(kwargs.get("dry_run", False)),
        )
        return True

    def handle_ScrubCancel(self, kwargs: dict) -> bool:
        """Cancel a running scrub.

        Args:
            kwargs: Method arguments (``mount``, ``dry_run``).

        Returns:
            True on success.

        Raises:
            org.mageia.manaradix.Error.Failed: The btrfs operation failed.
        """
        self._ctx.btrfs_ops.scrub_cancel(
            _s(kwargs.get("mount", "/")),
            dry_run=_b(kwargs.get("dry_run", False)),
        )
        return True

    def handle_Defragment(self, kwargs: dict) -> bool:
        """Defragment a path on a btrfs filesystem.

        Args:
            kwargs: Method arguments (``path``, ``recursive``, ``dry_run``).

        Returns:
            True on success.

        Raises:
            org.mageia.manaradix.Error.Failed: The btrfs operation failed.
        """
        self._ctx.btrfs_ops.defragment(
            _s(kwargs["path"]),
            recursive=_b(kwargs.get("recursive", False)),
            dry_run=_b(kwargs.get("dry_run", False)),
        )
        return True

    def handle_SetCompression(self, kwargs: dict) -> bool:
        """Set the compression algorithm of a subvolume.

        Args:
            kwargs: Method arguments (``path``, ``algorithm``, ``dry_run``).

        Returns:
            True on success.

        Raises:
            org.mageia.manaradix.Error.Failed: The btrfs operation failed.
        """
        self._ctx.btrfs_ops.set_compression(
            _s(kwargs["path"]),
            _s(kwargs["algorithm"]),
            dry_run=_b(kwargs.get("dry_run", False)),
        )
        return True

    def handle_CreateSubvolume(self, kwargs: dict) -> bool:
        """Create a subvolume.

        Args:
            kwargs: Method arguments (``path``, ``dry_run``).

        Returns:
            True on success.

        Raises:
            org.mageia.manaradix.Error.Failed: The btrfs operation failed.
        """
        self._ctx.btrfs_ops.create_subvolume(
            _s(kwargs["path"]),
            dry_run=_b(kwargs.get("dry_run", False)),
        )
        return True

    def handle_DeleteSubvolume(self, kwargs: dict) -> bool:
        """Delete a subvolume.

        Args:
            kwargs: Method arguments (``path``, ``dry_run``).

        Returns:
            True on success.

        Raises:
            org.mageia.manaradix.Error.Failed: The btrfs operation failed.
        """
        self._ctx.btrfs_ops.delete_subvolume(
            _s(kwargs["path"]),
            dry_run=_b(kwargs.get("dry_run", False)),
        )
        return True

    def handle_SetDefaultSubvolume(self, kwargs: dict) -> bool:
        """Set the default subvolume of a btrfs filesystem.

        Args:
            kwargs: Method arguments (``id``, ``mount``, ``dry_run``).

        Returns:
            True on success.

        Raises:
            org.mageia.manaradix.Error.Failed: The btrfs operation failed.
        """
        self._ctx.btrfs_ops.set_default_subvolume(
            _i(kwargs["id"]),
            _s(kwargs.get("mount", "/")),
            dry_run=_b(kwargs.get("dry_run", False)),
        )
        return True
