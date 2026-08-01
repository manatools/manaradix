"""First-run setup wizard logic.

On a stock Mageia install with plain btrfs partitioning, manaradix finds
an empty system: no snapshots, no policies, no configuration.  This module
detects that state (``needs_setup``) and applies a safe, recommended
baseline (``apply_recommended``) — daily/weekly/boot snapshots for ``/``
with default retention, plus the GRUB recovery menu.  See spec.md §15.

Pure logic: no UI imports, all destructive work supports ``dry_run`` and
is idempotent (re-running produces the same state).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from manaradix.model.policy import SnapshotPolicy

if TYPE_CHECKING:
    from manaradix.module import AppContext

logger = logging.getLogger(__name__)


def needs_setup(app_context: AppContext) -> bool:
    """Return True when the system has no snapshots and no policies.

    The wizard is only proposed on a truly unconfigured system: any
    snapshot or any policy (from any source) counts as configured.
    Errors are treated as "no wizard" so an unreadable btrfs state does
    not nag the user.
    """
    try:
        if app_context.snapshot_manager.list_all():
            return False
    except Exception as exc:
        logger.debug("Could not list snapshots for setup check: %s", exc)
        return False

    try:
        if app_context.config_store.list_policies():
            return False
    except Exception as exc:
        logger.debug("Could not list policies for setup check: %s", exc)
    return True


def recommended_policy() -> SnapshotPolicy:
    """The recommended baseline policy for ``/``.

    Daily + weekly + boot snapshots with the model's default retention:
    daily/weekly cover "yesterday" and "last week", boot snapshots plus
    the GRUB menu cover recovery after a bad update.  No new vocabulary —
    all values are existing model defaults.
    """
    return SnapshotPolicy(
        subvolume="/",
        schedule_daily=True,
        schedule_weekly=True,
        schedule_boot=True,
    )


def recommended_plan() -> dict[str, Any]:
    """The baseline as a data structure (used by the wizard UI table)."""
    policy = recommended_policy()
    return {
        "default_subvolume": "/",
        "snapshot_dir": ".snapshots",
        "boot_from_snapshot": True,
        "policy": policy,
        "schedules": policy.active_schedules,
        "retention": {
            "keep_daily": policy.retention.keep_daily,
            "keep_weekly": policy.retention.keep_weekly,
            "keep_boot": policy.retention.keep_boot,
        },
    }


def apply_recommended(
    app_context: AppContext,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Apply the recommended baseline configuration.

    Writes the global config (``/``, ``.snapshots``, boot-from-snapshot),
    registers the ``/`` policy, enables the systemd units via
    ``PolicyEngine``, and installs + updates the GRUB recovery script.
    A GRUB failure is logged, not fatal — a snapshot policy is still
    better than none.

    Returns a summary dict for the UI / CLI.
    """
    store = app_context.config_store
    policy = recommended_policy()

    store.default_subvolume = "/"
    store.snapshot_dir = ".snapshots"
    store.boot_from_snapshot = True
    store.set_policy(policy)

    if not dry_run:
        store.save()
        # The systemd units are the core of the baseline; a failure here
        # is fatal (unlike the GRUB script below).
        app_context.policy_engine.apply_policy(policy)

    grub_ok = False
    if store.boot_from_snapshot and not dry_run:
        try:
            app_context.grub_integration.install_script()
            app_context.grub_integration.update_grub()
            grub_ok = True
        except Exception as exc:
            logger.warning("Could not install the GRUB recovery script: %s", exc)

    summary = {
        "config_written": not dry_run,
        "schedules": list(policy.active_schedules),
        "retention": {
            "keep_daily": policy.retention.keep_daily,
            "keep_weekly": policy.retention.keep_weekly,
            "keep_boot": policy.retention.keep_boot,
        },
        "grub_installed": grub_ok,
    }
    logger.info(
        "Recommended setup %s: %s snapshots for / with %s",
        "previewed" if dry_run else "applied",
        ", ".join(summary["schedules"]),
        summary["retention"],
    )
    return summary
