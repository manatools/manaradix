"""Main entry point for manaradix.

Provides ``AppContext`` (shared service bundle for UI pages), ``run_app()``
for the GUI launcher, and a ``main()`` CLI entry point with subcommands
registered as the ``manaradix`` console script.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from manaradix.core.btrfs_ops import BtrfsOps, SnapshotNotFoundError
from manaradix.core.config_store import ConfigError, ConfigStore
from manaradix.core.grub_integration import GrubIntegration
from manaradix.core.policy_engine import PolicyEngine
from manaradix.core.snapshot_manager import SnapshotManager
from manaradix.model.snapshot import SnapshotType
from manaradix.ui.navigation import build_navigation
from manaradix.i18n import _
from manaradix.utils.pkexec import (
    already_reexecuted,
    is_root,
    needs_elevation,
    reexec_with_pkexec,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schedule tier → retention-policy field name mapping
# ---------------------------------------------------------------------------

_SCHEDULE_TO_RETENTION: dict[str, str] = {
    "hourly": "keep_hourly",
    "daily": "keep_daily",
    "weekly": "keep_weekly",
    "monthly": "keep_monthly",
    "boot": "keep_boot",
}

_SCHEDULE_CHOICES = list(_SCHEDULE_TO_RETENTION)

_SNAPSHOT_TYPE_CHOICES = [t.value for t in SnapshotType]


# ---------------------------------------------------------------------------
# AppContext
# ---------------------------------------------------------------------------


class AppContext:
    """Shared application context passed to all pages.

    Bundles together the core service instances so that every page
    has access to the same managers without global state.
    """

    def __init__(
        self,
        btrfs_ops: BtrfsOps,
        snapshot_manager: SnapshotManager,
        config_store: ConfigStore,
        policy_engine: PolicyEngine,
        grub_integration: GrubIntegration,
    ) -> None:
        self.btrfs_ops = btrfs_ops
        self.snapshot_manager = snapshot_manager
        self.config_store = config_store
        self.policy_engine = policy_engine
        self.grub_integration = grub_integration


# ---------------------------------------------------------------------------
# Service initialisation
# ---------------------------------------------------------------------------


def _init_services(
    config_path: str,
    snapshot_dir: str,
) -> AppContext:
    """Create and wire up all core service instances."""
    btrfs_ops = BtrfsOps(snapshot_dir=snapshot_dir)
    snapshot_manager = SnapshotManager(btrfs_ops)
    config_store = ConfigStore(path=config_path)
    try:
        config_store.load()
    except ConfigError as exc:
        logger.warning("Could not load config: %s — using defaults", exc)
    policy_engine = PolicyEngine()
    grub_integration = GrubIntegration()

    return AppContext(
        btrfs_ops=btrfs_ops,
        snapshot_manager=snapshot_manager,
        config_store=config_store,
        policy_engine=policy_engine,
        grub_integration=grub_integration,
    )


# ---------------------------------------------------------------------------
# GUI launcher
# ---------------------------------------------------------------------------


def run_app(
    frontend: str = "qt",
    *,
    config_path: str | None = None,
    snapshot_dir: str = ".snapshots",
) -> None:
    """Launch the application.

    Args:
        frontend: One of ``qt``, ``gtk``, ``ncurses``.
        config_path: Path to the TOML configuration file.
        snapshot_dir: Snapshot storage directory (relative to mount).
    """
    if config_path is None:
        config_path = ConfigStore.DEFAULT_PATH

    # Initialize core services
    btrfs_ops = BtrfsOps(snapshot_dir=snapshot_dir)
    snapshot_manager = SnapshotManager(btrfs_ops)
    config_store = ConfigStore(path=config_path)
    config_store.load()
    policy_engine = PolicyEngine()
    grub_integration = GrubIntegration()

    app_context = AppContext(
        btrfs_ops=btrfs_ops,
        snapshot_manager=snapshot_manager,
        config_store=config_store,
        policy_engine=policy_engine,
        grub_integration=grub_integration,
    )

    nav = build_navigation(app_context)

    try:
        from manatools.aui.yui import YUI  # noqa: F401
    except ImportError:
        logger.error(
            "python-manatools is required for the UI. "
            "Install it as a system package on Mageia."
        )
        sys.exit(1)

    # The YUI backend reads MUI_BACKEND when YUI.ui() is first touched.
    os.environ["MUI_BACKEND"] = frontend

    from manaradix.ui.renderer import AuiRenderer

    try:
        AuiRenderer(nav).run()
    except ImportError as exc:
        logger.error(
            "The %s UI backend is not available (%s). "
            "Choose another with --frontend (qt, gtk, ncurses).",
            frontend, exc,
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# CLI argument parser
# ---------------------------------------------------------------------------


def _add_global_args(parser: argparse.ArgumentParser) -> None:
    """Add flags shared by the GUI mode and all subcommands."""
    parser.add_argument(
        "--config",
        default=ConfigStore.DEFAULT_PATH,
        help=_("Path to config file (default: %(default)s)"),
    )
    parser.add_argument(
        "--snapshot-dir",
        default=".snapshots",
        help="Snapshot storage directory relative to mount point (default: %(default)s)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )


def _build_parser() -> argparse.ArgumentParser:
    """Build the full argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        description="Manaradix — Btrfs snapshot, subvolume, "
        "and filesystem management utility for Mageia",
    )
    _add_global_args(parser)
    parser.add_argument(
        "--frontend",
        choices=["qt", "gtk", "ncurses"],
        default="qt",
        help="UI toolkit frontend (default: qt)",
    )

    subparsers = parser.add_subparsers(
        title="subcommands",
        dest="command",
        metavar="COMMAND",
    )

    # -- snapshot ----------------------------------------------------------
    snap_parser = subparsers.add_parser(
        "snapshot",
        help="Snapshot management",
        description="Create, list, delete, prune, and inspect snapshots.",
    )
    snap_subs = snap_parser.add_subparsers(
        title="snapshot subcommands",
        dest="snapshot_command",
        metavar="ACTION",
    )

    # snapshot create
    snap_create = snap_subs.add_parser("create", help="Create a new snapshot")
    snap_create.add_argument(
        "--subvolume", default="/",
        help="Source subvolume path (default: /)",
    )
    snap_create.add_argument(
        "--type", dest="snapshot_type",
        choices=_SNAPSHOT_TYPE_CHOICES, default="manual",
        help="Snapshot type (default: manual)",
    )
    snap_create.add_argument(
        "--schedule", choices=_SCHEDULE_CHOICES, default="",
        help="Schedule tier for retention pruning (only meaningful with --type timed)",
    )
    snap_create.add_argument(
        "--description", default="",
        help="Human-readable description",
    )
    snap_create.add_argument(
        "--read-write", action="store_true",
        help="Create a read-write snapshot (default: read-only)",
    )
    snap_create.add_argument(
        "--mount", default="/",
        help="Mount point for the subvolume (default: /)",
    )
    snap_create.add_argument(
        "--dry-run", action="store_true",
        help="Preview the operation without executing",
    )

    # snapshot list
    snap_list = snap_subs.add_parser("list", help="List snapshots")
    snap_list.add_argument(
        "--mount", default="/",
        help="Mount point to list snapshots for (default: /)",
    )
    snap_list.add_argument(
        "--subvolume",
        help="Filter snapshots by source subvolume path",
    )

    # snapshot delete
    snap_delete = snap_subs.add_parser("delete", help="Delete a snapshot")
    snap_delete.add_argument(
        "--id", dest="snapshot_id", required=True,
        help="Snapshot ID to delete",
    )
    snap_delete.add_argument(
        "--mount", default="/",
        help="Mount point (default: /)",
    )
    snap_delete.add_argument(
        "--dry-run", action="store_true",
        help="Preview the operation without executing",
    )

    # snapshot prune
    snap_prune = snap_subs.add_parser(
        "prune",
        help="Prune snapshots by schedule tier (used by systemd timers)",
    )
    snap_prune.add_argument(
        "--subvolume", required=True,
        help="Subvolume path to prune snapshots for",
    )
    snap_prune.add_argument(
        "--schedule", required=True, choices=_SCHEDULE_CHOICES,
        help="Schedule tier to prune (determines retention count from config)",
    )
    snap_prune.add_argument(
        "--mount", default="/",
        help="Mount point (default: /)",
    )
    snap_prune.add_argument(
        "--dry-run", action="store_true",
        help="Preview which snapshots would be deleted",
    )

    # snapshot info
    snap_info = snap_subs.add_parser("info", help="Show snapshot details")
    snap_info.add_argument(
        "--id", dest="snapshot_id", required=True,
        help="Snapshot ID",
    )
    snap_info.add_argument(
        "--mount", default="/",
        help="Mount point (default: /)",
    )

    return parser


# ---------------------------------------------------------------------------
# CLI command handlers
# ---------------------------------------------------------------------------


def _cmd_snapshot_create(ctx: AppContext, args: argparse.Namespace) -> int:
    """Handle ``snapshot create``."""
    snapshot_type = SnapshotType(args.snapshot_type)
    read_only = not args.read_write

    try:
        snap = ctx.snapshot_manager.create(
            subvolume=args.subvolume,
            description=args.description,
            read_only=read_only,
            snapshot_type=snapshot_type,
            schedule=args.schedule,
            mount=args.mount,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        logger.error("Failed to create snapshot: %s", exc)
        return 1

    if args.dry_run:
        logger.info("[DRY RUN] Would create snapshot of %s", args.subvolume)
        return 0

    print(_("Created snapshot: %s") % snap.id)
    print(_("  Subvolume:   %s") % snap.subvolume)
    print(_("  Path:        %s") % snap.path)
    print(_("  Type:        %s") % snap.type.value)
    if snap.schedule:
        print(_("  Schedule:    %s") % snap.schedule)
    if snap.description:
        print(_("  Description: %s") % snap.description)
    return 0


def _cmd_snapshot_list(ctx: AppContext, args: argparse.Namespace) -> int:
    """Handle ``snapshot list``."""
    try:
        snapshots = ctx.snapshot_manager.list_all(mount=args.mount)
    except Exception as exc:
        logger.error("Failed to list snapshots: %s", exc)
        return 1

    if args.subvolume:
        snapshots = [s for s in snapshots if s.subvolume == args.subvolume]

    if not snapshots:
        print(_("No snapshots found."))
        return 0

    for snap in snapshots:
        print(
            f"{snap.id:22s}  {snap.type.value:12s}  "
            f"{snap.schedule if snap.schedule else '-':10s}  "
            f"{snap.subvolume:10s}  {snap.description}"
        )
    return 0


def _cmd_snapshot_delete(ctx: AppContext, args: argparse.Namespace) -> int:
    """Handle ``snapshot delete``."""
    snap = ctx.snapshot_manager.get(args.snapshot_id, mount=args.mount)
    if snap is None:
        logger.error("Snapshot not found: %s", args.snapshot_id)
        return 1

    try:
        ctx.snapshot_manager.delete(snap, dry_run=args.dry_run)
    except SnapshotNotFoundError:
        logger.error("Snapshot path not found on disk: %s", snap.path)
        return 1
    except Exception as exc:
        logger.error("Failed to delete snapshot: %s", exc)
        return 1

    if args.dry_run:
        logger.info("[DRY RUN] Would delete snapshot %s", snap.id)
        return 0

    print(_("Deleted snapshot: %s") % snap.id)
    return 0


def _cmd_snapshot_prune(ctx: AppContext, args: argparse.Namespace) -> int:
    """Handle ``snapshot prune``."""
    # Resolve keep_count from config
    policy = ctx.config_store.get_policy(args.subvolume)
    if policy is None:
        logger.warning(
            "No policy configured for subvolume '%s'; nothing to prune.",
            args.subvolume,
        )
        return 0

    retention_field = _SCHEDULE_TO_RETENTION[args.schedule]
    keep_count = getattr(policy.retention, retention_field, 0)

    logger.info(
        "Pruning %s snapshots for '%s': keeping %d most recent",
        args.schedule, args.subvolume, keep_count,
    )

    try:
        deleted = ctx.snapshot_manager.prune(
            subvolume=args.subvolume,
            schedule=args.schedule,
            keep_count=keep_count,
            mount=args.mount,
            dry_run=args.dry_run,
        )
    except ValueError as exc:
        logger.error("Invalid prune parameters: %s", exc)
        return 1
    except Exception as exc:
        logger.error("Prune failed: %s", exc)
        return 1

    if args.dry_run:
        if deleted:
            print(_("[DRY RUN] Would delete %d snapshot(s):") % len(deleted))
            for sid in deleted:
                print("  - %s" % sid)
        else:
            print(_("[DRY RUN] Nothing to prune (%d kept, 0 excess).") % keep_count)
    elif deleted:
        print(_("Pruned %d snapshot(s):") % len(deleted))
        for sid in deleted:
            print("  - %s" % sid)
    else:
        print(_("Nothing to prune (%d kept, no excess).") % keep_count)
    return 0


def _cmd_snapshot_info(ctx: AppContext, args: argparse.Namespace) -> int:
    """Handle ``snapshot info``."""
    snap = ctx.snapshot_manager.get(args.snapshot_id, mount=args.mount)
    if snap is None:
        logger.error("Snapshot not found: %s", args.snapshot_id)
        return 1

    print(_("ID:          %s") % snap.id)
    print(_("Subvolume:   %s") % snap.subvolume)
    print(_("Path:        %s") % snap.path)
    print(_("Date:        %s") % snap.date.isoformat())
    print(_("Type:        %s") % snap.type.value)
    print(_("Read-only:   %s") % snap.is_read_only)
    print(_("Schedule:    %s") % (snap.schedule or "-"))
    print(_("Description: %s") % (snap.description or "-"))
    return 0


# Subcommand dispatch: (command, subcommand) → handler
_COMMAND_DISPATCH: dict[tuple[str, str], object] = {
    ("snapshot", "create"): _cmd_snapshot_create,
    ("snapshot", "list"): _cmd_snapshot_list,
    ("snapshot", "delete"): _cmd_snapshot_delete,
    ("snapshot", "prune"): _cmd_snapshot_prune,
    ("snapshot", "info"): _cmd_snapshot_info,
}


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point for manaradix."""
    parser = _build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # ---- Privilege escalation ----
    # If the operation needs root and we're not already running as root
    # (and haven't already tried pkexec), re-exec via pkexec.
    subcmd = getattr(args, "snapshot_command", None)
    if needs_elevation(args.command, subcmd) and not is_root() and not already_reexecuted():
        logger.info(
            "Operation requires elevated privileges; re-executing via pkexec."
        )
        reexec_with_pkexec()
        # reexec_with_pkexec does not return
        return  # pragma: no cover

    # No subcommand → launch GUI
    if args.command is None:
        run_app(
            frontend=args.frontend,
            config_path=args.config,
            snapshot_dir=args.snapshot_dir,
        )
        return

    # Subcommand mode: initialise services and dispatch
    ctx = _init_services(
        config_path=args.config,
        snapshot_dir=args.snapshot_dir,
    )

    dispatch_key = (args.command, getattr(args, "snapshot_command", None))
    handler = _COMMAND_DISPATCH.get(dispatch_key)

    if handler is None:
        # argparse should prevent this, but guard anyway
        parser.print_help()
        sys.exit(1)

    exit_code = handler(ctx, args)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
