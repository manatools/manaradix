"""Main entry point for manaradix.

Provides ``AppContext`` (shared service bundle for UI pages), ``run_app()``
for the GUI launcher, and a ``main()`` CLI entry point with subcommands
registered as the ``manaradix`` console script.
"""

from __future__ import annotations

import argparse
import curses
import logging
import os
import subprocess
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
    "pre-upgrade": "keep_pre_upgrade",
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


def _frontend_available(frontend: str) -> bool:
    """Return True if a graphical frontend can actually initialize here.

    Qt/GTK initialization failures (missing platform plugin libraries,
    no display reachable) abort the process hard (qFatal), so probe in a
    child process with core dumps disabled and let the caller fall back
    to ncurses when the probe fails.
    """
    if frontend == "ncurses":
        return True

    probe = (
        "import resource, sys\n"
        "resource.setrlimit(resource.RLIMIT_CORE, (0, 0))\n"
        "if sys.argv[1] == 'qt':\n"
        "    from PySide6.QtWidgets import QApplication\n"
        "    QApplication([])\n"
        "elif sys.argv[1] == 'gtk':\n"
        "    import gi\n"
        "    gi.require_version('Gtk', '4.0')\n"
        "    from gi.repository import Gtk\n"
        "    if not Gtk.init_check():\n"
        "        sys.exit(1)\n"
        "else:\n"
        "    sys.exit(2)\n"
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", probe, frontend],
            timeout=15,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _recover_pkexec_display() -> None:
    """Recover the display environment stripped by a pkexec re-exec.

    pkexec drops DISPLAY/WAYLAND_DISPLAY/XDG_RUNTIME_DIR from the
    elevated child; manatools' ``YUI._detect_backend()`` rebuilds them
    from the live sockets under the PKEXEC_UID user's runtime dir (the
    same recovery the auto-detect path relies on).  We use the call for
    its environment side effect only, and only when a re-exec actually
    happened.  Failures are swallowed — the caller re-probes and falls
    back to ncurses either way.
    """
    if os.environ.get("PKEXEC_UID") is None:
        return
    try:
        from manatools.aui.yui import YUI

        YUI._detect_backend()  # noqa: SLF001 -- side effect: env recovery
    except (ImportError, RuntimeError):
        pass


def run_app(
    frontend: str | None = None,
    *,
    config_path: str | None = None,
    snapshot_dir: str = ".snapshots",
) -> None:
    """Launch the application.

    Args:
        frontend: One of ``qt``, ``gtk``, ``ncurses``, or None to let the
            manatools YUI auto-detect (which also recovers the display
            environment after a pkexec re-exec).  Graphical frontends are
            probed first and fall back to ncurses if they cannot start.
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
        from manatools.aui.yui import YUI, Backend  # noqa: F401
    except ImportError:
        logger.error(
            "python-manatools is required for the UI. "
            "Install it as a system package on Mageia."
        )
        sys.exit(1)

    # The YUI backend reads MUI_BACKEND when YUI.ui() is first touched.
    if frontend is not None:
        # Explicit --frontend choice: verify a graphical backend can
        # actually start here (missing display / plugin libraries would
        # otherwise abort the process hard), then fall back to ncurses.
        if frontend in ("qt", "gtk") and not _frontend_available(frontend):
            # A pkexec re-exec strips the display environment, which
            # makes the probe fail even though a display is reachable;
            # recover it (same logic the auto-detect path relies on)
            # before declaring the choice unusable.
            _recover_pkexec_display()
            if not _frontend_available(frontend):
                logger.warning(
                    "The %s frontend cannot initialize here; "
                    "falling back to ncurses.",
                    frontend,
                )
                frontend = "ncurses"
        os.environ["MUI_BACKEND"] = frontend
    else:
        # Auto-detect: let YUI pick the backend.  Its detection also
        # recovers the display environment lost in a pkexec re-exec
        # (PKEXEC_UID → /run/user/<uid> sockets) so the elevated GUI
        # process can still reach the user's display; then verify the
        # graphical choice actually initializes.
        try:
            detected = YUI._detect_backend()  # noqa: SLF001 -- library API
        except RuntimeError:
            detected = None
        if detected in (Backend.QT, Backend.GTK) and not _frontend_available(
            detected.value
        ):
            logger.warning(
                "The detected %s frontend cannot initialize here; "
                "falling back to ncurses.",
                detected.value,
            )
            os.environ["MUI_BACKEND"] = "ncurses"

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
    except RuntimeError as exc:
        # Auto-detection found nothing usable; retry with the last-resort
        # backend instead of dying with a traceback.
        if "ncurses" in os.environ.get("MUI_BACKEND", ""):
            raise
        logger.warning(
            "No usable UI backend (%s); retrying with ncurses.", exc
        )
        os.environ["MUI_BACKEND"] = "ncurses"
        AuiRenderer(nav).run()
    except curses.error as exc:
        # The curses backend needs a real terminal (e.g. when launched
        # from a desktop menu without a TTY).
        logger.error(
            "The ncurses UI needs a terminal; cannot start the GUI here (%s).",
            exc,
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
        default=None,
        help="UI toolkit frontend (default: auto-detect; qt/gtk fall back to ncurses)",
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
        help="Schedule tier for retention pruning (only meaningful with "
        "--type timed or --type pre-upgrade)",
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

    # -- setup --------------------------------------------------------------
    setup_parser = subparsers.add_parser(
        "setup",
        help="First-run setup wizard",
        description="Check whether a btrfs snapshot baseline is in place, "
        "and apply a setup profile (desktop, server, kiosk, ...).",
    )
    setup_subs = setup_parser.add_subparsers(
        title="setup subcommands",
        dest="setup_command",
        metavar="ACTION",
    )
    setup_status = setup_subs.add_parser(
        "status",
        help="Report whether the recommended setup is needed",
    )
    setup_apply = setup_subs.add_parser(
        "apply",
        help="Apply a setup profile (config, policies, systemd units, GRUB)",
    )
    setup_apply.add_argument(
        "--profile", default=None,
        help="Profile to apply (default: detected for this system)",
    )
    setup_apply.add_argument(
        "--dry-run", action="store_true",
        help="Preview the setup without applying anything",
    )
    setup_list = setup_subs.add_parser(
        "list-profiles",
        help="List the available setup profiles",
    )

    # -- activity ------------------------------------------------------------
    act_parser = subparsers.add_parser(
        "activity",
        help="Automated snapshot activity status and run records",
        description="Report on the automated snapshot schedules (systemd "
        "units) and record their runs — the generated units call "
        "'activity record' at the end of every fire.",
    )
    act_subs = act_parser.add_subparsers(
        title="activity subcommands",
        dest="activity_command",
        metavar="ACTION",
    )
    act_status = act_subs.add_parser(
        "status",
        help="Report per-activity status (read-only)",
    )
    act_status.add_argument(
        "--subvolume", default=None,
        help="Filter by subvolume path",
    )
    act_status.add_argument(
        "--schedule", choices=_SCHEDULE_CHOICES, default=None,
        help="Filter by schedule tier",
    )
    act_record = act_subs.add_parser(
        "record",
        help="Record one activity run (called by the generated units)",
    )
    act_record.add_argument(
        "--subvolume", required=True,
        help="Subvolume path the activity runs on",
    )
    act_record.add_argument(
        "--schedule", required=True, choices=_SCHEDULE_CHOICES,
        help="Schedule tier of the activity",
    )
    act_record.add_argument(
        "--result",
        choices=("success", "failed", "cancelled", "unknown"),
        default=None,
        help="Run result (default: from $SERVICE_RESULT set by systemd)",
    )
    act_record.add_argument(
        "--exit-status", default=None,
        help="Exit status of the unit (default: from $EXIT_STATUS)",
    )

    # -- dbus ----------------------------------------------------------------
    dbus_parser = subparsers.add_parser(
        "dbus",
        help="D-Bus daemon and status",
        description="Manage the system D-Bus service: run the daemon "
        "(normally started on demand by D-Bus activation) or report "
        "whether it is running.",
    )
    dbus_subs = dbus_parser.add_subparsers(
        title="dbus subcommands",
        dest="dbus_command",
        metavar="ACTION",
    )
    dbus_subs.add_parser(
        "serve",
        help="Run the D-Bus service (root only; the systemd unit calls this)",
    )
    dbus_subs.add_parser(
        "status",
        help="Report whether the D-Bus service is running",
    )

    # -- tray ----------------------------------------------------------------
    tray_parser = subparsers.add_parser(
        "tray",
        help="Tray applet: health checks, icon and notifications",
        description="Manage the status-notifier tray applet: run it "
        "(normally started by the autostart entry) or report whether "
        "it is running.",
    )
    tray_subs = tray_parser.add_subparsers(
        title="tray subcommands",
        dest="tray_command",
        metavar="ACTION",
    )
    tray_subs.add_parser(
        "serve",
        help="Run the tray applet in the user session",
    )
    tray_subs.add_parser(
        "status",
        help="Report whether the tray applet is running",
    )

    return parser


# ---------------------------------------------------------------------------
# CLI command handlers
# ---------------------------------------------------------------------------


def _cmd_snapshot_create(ctx: AppContext, args: argparse.Namespace) -> int:
    """Handle ``snapshot create``."""
    snapshot_type = SnapshotType(args.snapshot_type)
    read_only = not args.read_write

    # Pre-upgrade snapshots are gated on the global config flag: the rpm
    # file trigger invokes this CLI at the start of every package
    # transaction, and a disabled feature (or a non-btrfs root) must be
    # a silent no-op — a snapshot failure must never fail an update.
    if snapshot_type is SnapshotType.PRE_UPGRADE:
        if not ctx.config_store.snapshot_before_upgrade:
            logger.info(
                "Pre-upgrade snapshots are disabled "
                "(snapshot_before_upgrade is false); skipping."
            )
            return 0

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

    # The pre-upgrade tier is event-driven (no systemd unit with an
    # ExecStartPost prune), so prune right after creating — like the
    # timer units do.
    if snapshot_type is SnapshotType.PRE_UPGRADE:
        _prune_pre_upgrade(ctx, args.subvolume, mount=args.mount)

    return 0


def _prune_pre_upgrade(
    ctx: AppContext,
    subvolume: str,
    *,
    mount: str = "/",
) -> None:
    """Prune the pre-upgrade tier for *subvolume* to its retention count.

    Mirrors what the generated systemd units do via ``ExecStartPost``
    (``snapshot prune --schedule <tier>``); the pre-upgrade tier has no
    systemd unit, so pruning happens right after creation.  Failures are
    logged, never fatal — pruning must not fail the snapshot.
    """
    policy = ctx.config_store.get_policy(subvolume)
    if policy is None:
        logger.info(
            "No policy configured for %s; pre-upgrade snapshots are not pruned.",
            subvolume,
        )
        return
    try:
        deleted = ctx.snapshot_manager.prune(
            subvolume=subvolume,
            schedule="pre-upgrade",
            keep_count=policy.retention.keep_pre_upgrade,
            mount=mount,
        )
    except Exception as exc:
        logger.warning("Could not prune pre-upgrade snapshots: %s", exc)
        return
    if deleted:
        logger.info("Pruned pre-upgrade snapshots: %s", ", ".join(deleted))


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


def _cmd_setup_status(ctx: AppContext, args: argparse.Namespace) -> int:
    """Handle ``setup status``."""
    from manaradix.core.setup_wizard import (
        detect_role,
        needs_setup,
        select_default_profile,
        system_layout,
    )
    from manaradix.core.templates import load_profiles

    if not needs_setup(ctx):
        print(_("Setup not needed: snapshots or policies already exist."))
        return 0

    layout = system_layout(ctx)
    if not layout["btrfs_targets"]:
        print(_("Setup needed, but no btrfs filesystem was found on this system."))
        return 0

    role = detect_role()
    default = select_default_profile(load_profiles(), role)
    print(_("Setup needed: no snapshots and no snapshot policies are configured."))
    print(_("Detected role: %s") % role)
    if default is None:
        print(_("No setup profiles are available on this system."))
    else:
        print(_("Default profile: %s") % _(default.name))
        print(_("Run 'manaradix setup apply' to apply it."))
    return 0


def _cmd_setup_apply(ctx: AppContext, args: argparse.Namespace) -> int:
    """Handle ``setup apply``."""
    from manaradix.core.setup_wizard import apply_profile

    try:
        summary = apply_profile(ctx, args.profile, dry_run=args.dry_run)
    except Exception as exc:
        logger.error("Setup failed: %s", exc)
        return 1

    if args.dry_run:
        print(_("[DRY RUN] Would apply the setup profile %s:") % summary["profile_id"])
    else:
        print(_("Applied the setup profile %s:") % summary["profile_id"])
    for entry in summary.get("policies", []):
        schedules = ", ".join(entry.get("schedules", []))
        retention = ", ".join(f"{k}={v}" for k, v in entry.get("retention", {}).items())
        print(
            _("  %s: %s (retention: %s)")
            % (entry["subvolume"], schedules, retention)
        )
    if args.dry_run:
        # Preview mode: report what the effective layout allows, not what
        # was (nothing) actually installed.
        grub = (
            _("would be installed")
            if summary.get("grub_effective")
            else _("not available")
        )
    else:
        grub = _("installed") if summary.get("grub_installed") else _("not installed")
    print(_("  GRUB recovery menu: %s") % grub)
    return 0


def _cmd_setup_list_profiles(ctx: AppContext, args: argparse.Namespace) -> int:
    """Handle ``setup list-profiles``."""
    from manaradix.core.templates import load_profiles

    profiles = load_profiles()
    if not profiles:
        print(_("No setup profiles are available on this system."))
        return 0

    print(_("Available setup profiles:"))
    for profile in profiles:
        print(f"  {profile.id:10s}  {_(profile.name)} ({profile.role})")
        if profile.description:
            print(f"      {_(profile.description)}")
    return 0


def _cmd_activity_status(ctx: AppContext, args: argparse.Namespace) -> int:
    """Handle ``activity status`` — per-activity status table (read-only)."""
    from manaradix.core.activity_status import ActivityStatus

    rows = ActivityStatus(
        ctx.policy_engine,
        ctx.snapshot_manager,
        ctx.config_store,
    ).statuses()
    if args.subvolume:
        rows = [r for r in rows if r["subvolume"] == args.subvolume]
    if args.schedule:
        rows = [r for r in rows if r["schedule"] == args.schedule]

    if not rows:
        print(_("No activities are configured."))
        return 0

    print(_("Activity status:"))
    for r in rows:
        last = r["last_run_ts"]
        if last is None:
            last = _("never")
        elif r["last_run_result"] not in (None, "success"):
            last = _("%s (%s)") % (last, r["last_run_result"])
        print(
            f"  {r['subvolume']:10s}  {r['schedule']:8s}  "
            f"{r['state']:8s}  {last}"
        )
    return 0


def _cmd_activity_record(ctx: AppContext, args: argparse.Namespace) -> int:
    """Handle ``activity record`` — append one run record.

    Invoked by the generated units as ``ExecStopPost``; runs as root by
    construction.  A manual unprivileged call fails with a clear write
    error instead of an auth prompt.
    """
    from manaradix.core.activity_status import record_activity_run

    try:
        record = record_activity_run(
            args.subvolume,
            args.schedule,
            result=args.result,
            exit_status=args.exit_status,
        )
    except OSError as exc:
        logger.error("Could not write activity run record: %s", exc)
        return 1
    except ValueError as exc:
        logger.error("Invalid activity record: %s", exc)
        return 1

    logger.info(
        "Recorded %s run of %s (%s)",
        record["result"], args.subvolume, args.schedule,
    )
    return 0


def _cmd_dbus_serve(ctx: AppContext, args: argparse.Namespace) -> int:
    """Handle ``dbus serve`` — run the daemon (root only)."""
    from manaradix.dbus.wire import serve_main

    return serve_main(
        config_path=args.config,
        snapshot_dir=args.snapshot_dir,
        debug=args.debug,
    )


def _cmd_dbus_status(ctx: AppContext, args: argparse.Namespace) -> int:
    """Handle ``dbus status`` — is the service running?"""
    from manaradix.dbus.client import ManaradixClient

    try:
        running = ManaradixClient().daemon_running()
    except Exception:
        running = False
    if running:
        print(_("The manaradix D-Bus service is running."))
        return 0
    print(
        _(
            "The manaradix D-Bus service is not running; "
            "it starts automatically on first use."
        )
    )
    return 2


def _cmd_tray_serve(ctx: AppContext, args: argparse.Namespace) -> int:
    """Handle ``tray serve`` — run the tray applet (user session)."""
    from manaradix.tray.app import TrayApp

    return TrayApp(ctx.config_store).run()


def _cmd_tray_status(ctx: AppContext, args: argparse.Namespace) -> int:
    """Handle ``tray status`` — is the tray applet running?"""
    from manaradix.tray.app import tray_running

    if tray_running():
        print(_("The manaradix tray applet is running."))
        return 0
    print(
        _(
            "The manaradix tray applet is not running; "
            "it starts automatically at login."
        )
    )
    return 2


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
    ("setup", "status"): _cmd_setup_status,
    ("setup", "apply"): _cmd_setup_apply,
    ("setup", "list-profiles"): _cmd_setup_list_profiles,
    ("activity", "status"): _cmd_activity_status,
    ("activity", "record"): _cmd_activity_record,
    ("dbus", "serve"): _cmd_dbus_serve,
    ("dbus", "status"): _cmd_dbus_status,
    ("tray", "serve"): _cmd_tray_serve,
    ("tray", "status"): _cmd_tray_status,
}


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


_KNOWN_BACKENDS = ("qt", "gtk", "ncurses")


def _frontend_from_env() -> str | None:
    """Return a known backend name from ``MUI_BACKEND``, or None.

    pkexec strips the environment on re-exec (``MUI_BACKEND`` included),
    so the env choice must be folded into argv before elevation — an
    elevated child would otherwise auto-detect a different backend.
    """
    choice = os.environ.get("MUI_BACKEND", "").strip().lower()
    return choice if choice in _KNOWN_BACKENDS else None


def main() -> None:
    """CLI entry point for manaradix."""
    parser = _build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # A backend chosen via MUI_BACKEND is lost when pkexec strips the
    # environment; fold it into argv (which survives) unless --frontend
    # was already explicit.
    if (
        args.command is None
        and args.frontend is None
        and not any(a.startswith("--frontend") for a in sys.argv)
    ):
        from_env = _frontend_from_env()
        if from_env:
            sys.argv += ["--frontend", from_env]
            args.frontend = from_env

    # ---- Privilege escalation ----
    # If the operation needs root and we're not already running as root
    # (and haven't already tried pkexec), re-exec via pkexec.
    subcmd = (
        getattr(args, "snapshot_command", None)
        or getattr(args, "setup_command", None)
        or getattr(args, "dbus_command", None)
        or getattr(args, "activity_command", None)
        or getattr(args, "tray_command", None)
    )
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

    dispatch_key = (
        args.command,
        getattr(args, "snapshot_command", None)
        or getattr(args, "setup_command", None)
        or getattr(args, "dbus_command", None)
        or getattr(args, "activity_command", None)
        or getattr(args, "tray_command", None),
    )
    handler = _COMMAND_DISPATCH.get(dispatch_key)

    if handler is None:
        # argparse should prevent this, but guard anyway
        parser.print_help()
        sys.exit(1)

    exit_code = handler(ctx, args)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
