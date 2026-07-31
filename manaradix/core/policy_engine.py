"""Snapshot policy engine — systemd timer/service unit generation.

Generates and manages systemd unit files for automated snapshot schedules
with retention/pruning logic.

Systemd units are written to ``/etc/systemd/system/``.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Optional

from manaradix.model.policy import (
    RetentionPolicy,
    SnapshotPolicy,
)

logger = logging.getLogger(__name__)

# OnCalendar expressions for timer-based schedules
_TIMER_SCHEDULES: dict[str, str] = {
    "hourly": "*-*-* *:00:00",
    "daily": "*-*-* 00:00:00",
    "weekly": "Mon *-*-* 00:00:00",
    "monthly": "*-*-01 00:00:00",
}

# Boot schedule is trigger-based (oneshot at boot), not timer-based
_BOOT_SCHEDULE = "boot"

# All schedule tiers the engine knows about
_ALL_SCHEDULES = list(_TIMER_SCHEDULES) + [_BOOT_SCHEDULE]

# systemd unit output directory
_SYSTEMD_DIR = "/etc/systemd/system"

# Valid characters for systemd unit names (alphanumeric, dashes, underscores)
_UNIT_NAME_RE = re.compile(r"[^a-zA-Z0-9_.:@-]+")

# Suffix order for retention pruning (hourly trimmed first)
_RETENTION_ORDER = ["hourly", "daily", "weekly", "monthly", "boot"]


class PolicyEngineError(Exception):
    """Raised when policy application fails."""


class PolicyEngine:
    """Generates systemd timer/service units for snapshot policies.

    Usage::

        engine = PolicyEngine(cli_path="/usr/bin/manaradix")
        policy = SnapshotPolicy(subvolume="/", schedule_daily=True)
        engine.apply_policy(policy)
        units = engine.list_generated_units()
    """

    def __init__(
        self,
        cli_path: str = "/usr/bin/manaradix",
        systemd_dir: str = _SYSTEMD_DIR,
    ) -> None:
        self._cli_path = cli_path
        self._systemd_dir = systemd_dir

    def _sanitize_name(self, subvolume: str) -> str:
        """Turn a subvolume path into a safe systemd unit name fragment.

        ``/`` becomes ``-``, ``/home`` becomes ``home``.
        """
        name = subvolume.strip("/").replace("/", "-") or "root"
        return _UNIT_NAME_RE.sub("-", name)

    def _service_name(self, subvolume: str, schedule: str) -> str:
        """Generate a systemd service unit name."""
        name = self._sanitize_name(subvolume)
        return f"manaradix-snapshot-{name}-{schedule}.service"

    def _timer_name(self, subvolume: str, schedule: str) -> str:
        """Generate a systemd timer unit name."""
        name = self._sanitize_name(subvolume)
        return f"manaradix-snapshot-{name}-{schedule}.timer"

    def _generate_service_unit(self, subvolume: str, schedule: str) -> str:
        """Generate a systemd service unit that invokes the CLI."""
        return (
            f"[Unit]\n"
            f"Description=Manaradix {schedule} snapshot of {subvolume}\n"
            f"Documentation=man:manaradix(8)\n"
            f"After=local-fs.target\n\n"
            f"[Service]\n"
            f"Type=oneshot\n"
            f"ExecStart={self._cli_path} snapshot create --subvolume {subvolume}"
            f" --type timed --schedule {schedule}\n"
            f"ExecStartPost={self._cli_path} snapshot prune --subvolume {subvolume}"
            f" --schedule {schedule}\n"
            f"Nice=19\n"
            f"IOSchedulingClass=idle\n"
        )

    def _generate_timer_unit(
        self,
        subvolume: str,
        schedule: str,
    ) -> str:
        """Generate a systemd timer unit for a schedule tier."""
        calendar = _TIMER_SCHEDULES.get(schedule, "*-*-* 00:00:00")
        service = self._service_name(subvolume, schedule)
        return (
            f"[Unit]\n"
            f"Description=Manaradix {schedule} snapshot timer for {subvolume}\n"
            f"Documentation=man:manaradix(8)\n\n"
            f"[Timer]\n"
            f"OnCalendar={calendar}\n"
            f"Persistent=true\n"
            f"RandomizedDelaySec=300\n\n"
            f"[Install]\n"
            f"WantedBy=timers.target\n"
        )

    def _generate_boot_service_unit(self, subvolume: str) -> str:
        """Generate a oneshot systemd service for boot-time snapshots.

        This is NOT paired with a timer — it fires once per boot via
        ``WantedBy=multi-user.target``.
        """
        return (
            f"[Unit]\n"
            f"Description=Manaradix boot snapshot of {subvolume}\n"
            f"Documentation=man:manaradix(8)\n"
            f"After=local-fs.target\n\n"
            f"[Service]\n"
            f"Type=oneshot\n"
            f"ExecStart={self._cli_path} snapshot create --subvolume {subvolume}"
            f" --type timed --schedule boot\n"
            f"ExecStartPost={self._cli_path} snapshot prune --subvolume {subvolume}"
            f" --schedule boot\n"
            f"Nice=19\n"
            f"IOSchedulingClass=idle\n\n"
            f"[Install]\n"
            f"WantedBy=multi-user.target\n"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def apply_policy(
        self,
        policy: SnapshotPolicy,
        *,
        dry_run: bool = False,
    ) -> list[str]:
        """Generate and enable/disable systemd units for a policy.

        Timer-based schedules (hourly/daily/weekly/monthly) get a
        service+timer pair.  The boot schedule gets a oneshot service
        enabled via ``multi-user.target`` (no timer).

        Args:
            policy: The snapshot policy to apply.
            dry_run: Preview unit files without writing.

        Returns:
            List of unit file paths that were written.
        """
        errors = policy.validate()
        if errors:
            raise PolicyEngineError("Invalid policy: " + "; ".join(errors))

        written: list[str] = []

        # ---- Timer-based schedules ----
        for schedule in _TIMER_SCHEDULES:
            service_unit = self._service_name(policy.subvolume, schedule)
            timer_unit = self._timer_name(policy.subvolume, schedule)
            service_path = os.path.join(self._systemd_dir, service_unit)
            timer_path = os.path.join(self._systemd_dir, timer_unit)
            active = schedule in policy.active_schedules and policy.enabled

            if dry_run:
                logger.info(
                    "[DRY RUN] Would %s %s/%s for %s (%s)",
                    "enable" if active else "disable",
                    service_unit,
                    timer_unit,
                    policy.subvolume,
                    schedule,
                )
                written.append(timer_path)
                continue

            if active:
                self._write_unit_file(
                    service_path,
                    self._generate_service_unit(policy.subvolume, schedule),
                )
                written.append(service_path)

                self._write_unit_file(
                    timer_path,
                    self._generate_timer_unit(policy.subvolume, schedule),
                )
                written.append(timer_path)

                self._systemctl("enable", timer_unit)
                self._systemctl("start", timer_unit)
            else:
                self._systemctl("stop", timer_unit, check=False)
                self._systemctl("disable", timer_unit, check=False)
                self._remove_unit_file(service_path)
                self._remove_unit_file(timer_path)
                written.append(f"-{timer_path}")

        # ---- Boot schedule (oneshot, no timer) ----
        boot_active = _BOOT_SCHEDULE in policy.active_schedules and policy.enabled
        boot_service_unit = self._service_name(policy.subvolume, _BOOT_SCHEDULE)
        boot_service_path = os.path.join(self._systemd_dir, boot_service_unit)

        if dry_run:
            logger.info(
                "[DRY RUN] Would %s %s for %s (boot)",
                "enable" if boot_active else "disable",
                boot_service_unit,
                policy.subvolume,
            )
            written.append(boot_service_path if boot_active else f"-{boot_service_path}")
        elif boot_active:
            self._write_unit_file(
                boot_service_path,
                self._generate_boot_service_unit(policy.subvolume),
            )
            written.append(boot_service_path)
            self._systemctl("enable", boot_service_unit)
        else:
            self._systemctl("stop", boot_service_unit, check=False)
            self._systemctl("disable", boot_service_unit, check=False)
            self._remove_unit_file(boot_service_path)
            written.append(f"-{boot_service_path}")

        return written

    def remove_policy(
        self,
        subvolume: str,
        *,
        dry_run: bool = False,
    ) -> list[str]:
        """Remove all systemd units for a subvolume's policies."""
        removed: list[str] = []

        # Timer schedules
        for schedule in _TIMER_SCHEDULES:
            timer_unit = self._timer_name(subvolume, schedule)
            timer_path = os.path.join(self._systemd_dir, timer_unit)
            service_unit = self._service_name(subvolume, schedule)
            service_path = os.path.join(self._systemd_dir, service_unit)

            if not dry_run:
                self._systemctl("stop", timer_unit, check=False)
                self._systemctl("disable", timer_unit, check=False)
                self._remove_unit_file(timer_path)
                self._remove_unit_file(service_path)
            removed.append(timer_path)

        # Boot schedule
        boot_service_unit = self._service_name(subvolume, _BOOT_SCHEDULE)
        boot_service_path = os.path.join(self._systemd_dir, boot_service_unit)
        if not dry_run:
            self._systemctl("stop", boot_service_unit, check=False)
            self._systemctl("disable", boot_service_unit, check=False)
            self._remove_unit_file(boot_service_path)
        removed.append(boot_service_path)

        return removed

    def list_generated_units(self) -> list[str]:
        """Return all manaradix units installed on the system.

        Includes both timer units (for calendar schedules) and service
        units (for boot schedule).
        """
        try:
            files = os.listdir(self._systemd_dir)
        except OSError:
            return []

        return sorted(
            f
            for f in files
            if f.startswith("manaradix-snapshot-")
            and (f.endswith(".timer") or f.endswith(".service"))
        )

    # ------------------------------------------------------------------
    # Retention / pruning
    # ------------------------------------------------------------------

    def get_prune_commands(
        self,
        subvolume: str,
        retention: RetentionPolicy,
        mount: str = "/",
    ) -> list[str]:
        """Validate retention configuration.

        Note:
            Actual snapshot pruning is performed by
            ``SnapshotManager.prune()`` via the ``snapshot prune``
            CLI subcommand.  This method only validates that the
            retention policy is well-formed.

        Returns:
            An empty list (retention policy is valid).
        """
        errors = retention.validate()
        if errors:
            raise PolicyEngineError("Invalid retention: " + "; ".join(errors))
        return []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write_unit_file(self, path: str, content: str) -> None:
        """Write a systemd unit file."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(content)
        logger.info("Wrote unit file: %s", path)

    def _remove_unit_file(self, path: str) -> None:
        """Remove a systemd unit file if it exists."""
        try:
            os.remove(path)
            logger.info("Removed unit file: %s", path)
        except OSError:
            pass

    def _systemctl(
        self,
        action: str,
        unit: str,
        *,
        check: bool = True,
    ) -> None:
        """Run a systemctl command."""
        import subprocess

        cmd = ["systemctl", action, unit]
        logger.debug("Running: %s", " ".join(cmd))
        try:
            subprocess.run(
                cmd,
                check=check,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.CalledProcessError as exc:
            logger.warning("systemctl %s %s failed: %s", action, unit, exc.stderr)
