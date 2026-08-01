"""First-run setup wizard logic.

On a stock Mageia install with plain btrfs partitioning, manaradix finds
an empty system: no snapshots, no policies, no configuration.  This module
detects that state (``needs_setup``) and proposes a **setup profile** (see
:mod:`manaradix.core.templates`) — desktop, server, kiosk, ... — adapted
to the actual system: only the btrfs subvolumes that exist get policies,
and boot snapshots plus the GRUB recovery menu are only proposed when the
root filesystem is btrfs (and GRUB is the bootloader for the menu).  A
default profile is selected from the detected role (graphical → desktop,
headless → server).  See spec.md §15.

Pure logic: no UI imports, all destructive work supports ``dry_run`` and
is idempotent (re-running produces the same state).
"""

from __future__ import annotations

import logging
import os
import subprocess
from typing import TYPE_CHECKING, Any

from manaradix.core.templates import (
    SetupProfile,
    load_profiles,
)
from manaradix.i18n import _
from manaradix.model.policy import RetentionPolicy, SnapshotPolicy

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


def system_layout(app_context: AppContext) -> dict[str, Any]:
    """Probe the actual system layout for wizard adaptation.

    Returns:
        ``{"btrfs_targets": [...], "root_is_btrfs": bool,
        "grub_present": bool}``.  Probe errors degrade to the empty
        state ("no btrfs, no GRUB") — a wizard must never crash on an
        unreadable system.
    """
    try:
        mounts = app_context.btrfs_ops.btrfs_mounts()
    except Exception as exc:
        logger.debug("Could not query btrfs mounts: %s", exc)
        mounts = []
    targets = [m["target"] for m in mounts]

    try:
        grub_present = app_context.grub_integration.has_system_grub()
    except Exception as exc:
        logger.debug("Could not probe GRUB presence: %s", exc)
        grub_present = False

    return {
        "btrfs_targets": targets,
        "root_is_btrfs": "/" in targets,
        "grub_present": grub_present,
    }


def detect_role() -> str:
    """Detect the typical usage role of this machine: "desktop" or "server".

    Uses the systemd default target (readable without privileges); falls
    back to the presence of installed X sessions when systemd cannot be
    queried.  Kiosk-style profiles (``role="any"``) are never auto-selected.
    """
    try:
        proc = subprocess.run(
            ["systemctl", "get-default"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        proc = None
    if proc is not None and proc.returncode == 0:
        target = proc.stdout.strip()
        return "desktop" if target == "graphical.target" else "server"

    try:
        xsessions = "/usr/share/xsessions"
        if os.path.isdir(xsessions) and os.listdir(xsessions):
            return "desktop"
    except OSError:
        pass
    return "server"


def select_default_profile(
    profiles: list[SetupProfile],
    role: str,
) -> SetupProfile | None:
    """Pick the profile to propose by default for *role*.

    Prefers a profile whose ``role`` matches, then any ``role="any"``
    profile, then the first profile; None when no profile exists.
    """
    for profile in profiles:
        if profile.role == role:
            return profile
    for profile in profiles:
        if profile.role == "any":
            return profile
    return profiles[0] if profiles else None


def build_plan(app_context: AppContext, profile: SetupProfile) -> dict[str, Any]:
    """Adapt a profile to the actual system layout.

    Rules:
    - a policy applies iff its subvolume is a mounted btrfs target;
    - optional policies on missing subvolumes are dropped silently,
      required ones are kept and annotated as unavailable;
    - ``boot`` schedules are kept only for the ``/`` policy when the
      root filesystem is btrfs;
    - ``boot_from_snapshot`` is effective only when the root is btrfs
      and GRUB is present.

    Returns the plan dict consumed by the wizard UI and by
    :func:`apply_profile`:

    ``{"profile_id", "snapshot_dir", "boot_from_snapshot_effective",
    "applicable", "layout", "policies", "notes"}``
    """
    layout = system_layout(app_context)
    targets = set(layout["btrfs_targets"])
    root_is_btrfs = layout["root_is_btrfs"]

    policies: list[dict[str, Any]] = []
    notes: list[str] = []
    boot_dropped = False

    for tp in profile.policies:
        if "boot" in tp.schedules and (tp.subvolume != "/" or not root_is_btrfs):
            boot_dropped = True
        schedules = [
            s for s in tp.schedules
            if s != "boot" or (tp.subvolume == "/" and root_is_btrfs)
        ]

        available = tp.subvolume in targets and bool(schedules)
        reason = None
        if not available:
            if boot_dropped and not schedules and tp.subvolume in targets:
                # The subvolume exists but its only schedule was boot.
                reason = _("Boot snapshots are not available: the root filesystem is not btrfs.")
            elif not tp.required:
                # Optional policies on missing subvolumes are dropped
                # without a trace.
                continue
            else:
                reason = _("Not available on this system")

        policies.append(
            {
                "subvolume": tp.subvolume,
                "schedules": schedules,
                "retention": {
                    field: getattr(tp.retention, field)
                    for field in (
                        "keep_hourly", "keep_daily", "keep_weekly",
                        "keep_monthly", "keep_boot",
                    )
                },
                "available": available,
                "reason": reason,
            }
        )

    if boot_dropped:
        notes.append(
            _("Boot snapshots are not available: the root filesystem is not btrfs.")
        )

    grub_effective = profile.boot_from_snapshot and root_is_btrfs and layout["grub_present"]
    if profile.boot_from_snapshot and not grub_effective:
        notes.append(
            _("The GRUB recovery menu is not available: it needs a btrfs root filesystem and GRUB.")
        )

    return {
        "profile_id": profile.id,
        "snapshot_dir": profile.snapshot_dir,
        "boot_from_snapshot_effective": grub_effective,
        "applicable": any(p["available"] for p in policies),
        "layout": layout,
        "policies": policies,
        "notes": notes,
    }


def apply_profile(
    app_context: AppContext,
    profile_id: str | None = None,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Apply a setup profile, adapted to the actual system layout.

    Args:
        profile_id: Profile id to apply; None selects the default profile
            for the detected role.
        dry_run: If True, build and log the plan without writing anything.

    Returns:
        Summary dict: ``{"config_written", "profile_id", "policies",
        "grub_installed"}`` — ``policies`` lists the actually applied
        per-subvolume ``{"subvolume", "schedules", "retention"}`` entries.

    Raises:
        ValueError: Unknown profile id, no profiles available, or the
            profile does not apply to this system.
    """
    profiles = load_profiles()
    if profile_id is not None:
        profile = next((p for p in profiles if p.id == profile_id), None)
        if profile is None:
            raise ValueError(_("Unknown setup profile: %s") % profile_id)
    else:
        role = detect_role()
        profile = select_default_profile(profiles, role)
        if profile is None:
            raise ValueError(_("No setup profiles are available on this system."))
        logger.info("No profile given; using %s (detected role: %s)", profile.id, role)

    plan = build_plan(app_context, profile)
    if not plan["applicable"]:
        raise ValueError(
            _(
                "The %s profile does not apply to this system: no btrfs "
                "subvolume matches its policies."
            )
            % profile.id
        )

    store = app_context.config_store
    store.snapshot_dir = plan["snapshot_dir"]
    store.boot_from_snapshot = plan["boot_from_snapshot_effective"]

    available = [p for p in plan["policies"] if p["available"]]
    store.default_subvolume = (
        "/" if plan["layout"]["root_is_btrfs"] else available[0]["subvolume"]
    )

    policies: list[SnapshotPolicy] = []
    for entry in available:
        policy = SnapshotPolicy(
            subvolume=entry["subvolume"],
            schedule_hourly="hourly" in entry["schedules"],
            schedule_daily="daily" in entry["schedules"],
            schedule_weekly="weekly" in entry["schedules"],
            schedule_monthly="monthly" in entry["schedules"],
            schedule_boot="boot" in entry["schedules"],
            retention=RetentionPolicy(**entry["retention"]),
        )
        store.set_policy(policy)
        policies.append(policy)

    if not dry_run:
        store.save()
        # The systemd units are the core of the setup; a failure here is
        # fatal (unlike the GRUB script below).
        for policy in policies:
            app_context.policy_engine.apply_policy(policy)

    grub_ok = False
    if plan["boot_from_snapshot_effective"] and not dry_run:
        try:
            app_context.grub_integration.install_script()
            app_context.grub_integration.update_grub()
            grub_ok = True
        except Exception as exc:
            logger.warning("Could not install the GRUB recovery script: %s", exc)

    summary = {
        "config_written": not dry_run,
        "profile_id": plan["profile_id"],
        "grub_effective": plan["boot_from_snapshot_effective"],
        "policies": [
            {
                "subvolume": e["subvolume"],
                "schedules": e["schedules"],
                "retention": e["retention"],
            }
            for e in available
        ],
        "grub_installed": grub_ok,
    }
    logger.info(
        "Setup %s for profile %s: %s",
        "previewed" if dry_run else "applied",
        profile.id,
        ", ".join(e["subvolume"] for e in available),
    )
    return summary
