"""Snapshot policy engine — systemd timer/service unit generation.

Generates and manages systemd unit files for automated snapshot schedules
with retention/pruning logic.

Systemd units are written to ``/etc/systemd/system/``.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import time
from datetime import datetime
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

# Properties queried with ``systemctl show --property=... --value`` and
# parsed in order.  UnitFileState reports the enable state (enabled /
# disabled / static ...); the time properties come back as unix epochs
# with ``--timestamp=unix`` ("n/a" when never fired / not scheduled).
# ExecMainStartTimestamp is the last time the service's process was
# started — None means the service has never executed, which is the
# evidence that a recorded timer fire never became a run.
_SHOW_TIMER_PROPS = (
    "ActiveState",
    "UnitFileState",
    "LastTriggerUSec",
    "NextElapseUSecRealtime",
)
_SHOW_SERVICE_PROPS = (
    "ActiveState",
    "UnitFileState",
    "ExecMainStartTimestamp",
)

# Rough fire cadence per calendar tier, used for "missed" staleness
# detection (an active timer whose last fire is far older than its
# cadence likely fired into a machine that was off).
_CADENCE_SECONDS: dict[str, int] = {
    "hourly": 3600,
    "daily": 86400,
    "weekly": 604800,
    "monthly": 2592000,
}

#: Staleness threshold: last fire older than this × cadence → missed.
_MISSED_FACTOR = 2.5


def _escape_systemd_arg(value: str) -> str:
    """Escape a value for a quoted ``ExecStart=`` argument.

    systemd's ExecStart parser (systemd.exec(5)) treats ``;`` as a command
    separator, ``%`` as a specifier, and ``"``/``\\`` as quoting syntax —
    an unquoted subvolume name could inject a second command that runs as
    root on the next timer fire.  Wrap the value in double quotes and
    escape every special character; control characters (newlines in
    particular) are stripped — they would split the unit into new lines.
    """
    value = "".join(c for c in value if c >= " " and c != "\x7f")
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("%", "%%")
        .replace(";", "\\;")
    )

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
        cli_path: Optional[str] = None,
        systemd_dir: str = _SYSTEMD_DIR,
    ) -> None:
        self._systemd_dir = systemd_dir
        self._cli_path = self._resolve_cli_path(cli_path)

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
        sub = _escape_systemd_arg(subvolume)
        return (
            f"[Unit]\n"
            f"Description=Manaradix {schedule} snapshot of {sub}\n"
            f"Documentation=man:manaradix(8)\n"
            f"After=local-fs.target\n\n"
            f"[Service]\n"
            f"Type=oneshot\n"
            f'ExecStart={self._cli_path} snapshot create --subvolume "{sub}"'
            f" --type timed --schedule {schedule}\n"
            f'ExecStartPost={self._cli_path} snapshot prune --subvolume "{sub}"'
            f" --schedule {schedule}\n"
            f"ExecStopPost={self._cli_path} activity record --subvolume \"{sub}\""
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
        sub = _escape_systemd_arg(subvolume)
        return (
            f"[Unit]\n"
            f"Description=Manaradix boot snapshot of {sub}\n"
            f"Documentation=man:manaradix(8)\n"
            f"After=local-fs.target\n\n"
            f"[Service]\n"
            f"Type=oneshot\n"
            f'ExecStart={self._cli_path} snapshot create --subvolume "{sub}"'
            f" --type timed --schedule boot\n"
            f'ExecStartPost={self._cli_path} snapshot prune --subvolume "{sub}"'
            f" --schedule boot\n"
            f"ExecStopPost={self._cli_path} activity record --subvolume \"{sub}\""
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
    # Status (read-only, unprivileged)
    # ------------------------------------------------------------------

    def unit_state(self, unit: str) -> str:
        """Return the systemd ActiveState of *unit*.

        One of ``active`` / ``inactive`` / ``failed`` (or whatever systemd
        reports), ``unknown`` when systemd cannot be queried.  Used by the
        Policies page's systemd Units table.
        """
        data = self._systemctl_show(unit, ("ActiveState",))
        if not data:
            return "unknown"
        return data.get("ActiveState") or "unknown"

    def unit_status(self, subvolume: str, schedule: str) -> dict:
        """Query the live systemd state of one activity's units.

        Read-only and unprivileged — only ``systemctl show``.  This is
        what the dashboard and ``activity status`` surface.

        The derived ``state`` is one of:

        - ``unknown`` — unit files not installed (or systemd unreachable);
        - ``failed`` — the service is in the failed state, or the timer
          fired but the service never executed (the fire never became a
          run — e.g. the CLI binary was missing);
        - ``inactive`` — installed but not enabled/active;
        - ``never`` — active and enabled, but the timer has never fired
          (freshly enabled, or the machine was always off at fire time) —
          for the boot tier: the service has never executed at any boot;
        - ``missed`` — active, but the last fire is older than ~2.5× the
          tier cadence (the machine was likely off during fires);
        - ``ok`` — healthy: the timer has fired within the tier cadence
          and the service actually ran.

        Returns a dict with ``subvolume``, ``schedule``, ``state`` and the
        raw ``last_trigger`` / ``next_trigger`` epoch strings (None when
        unknown).
        """
        service = self._service_name(subvolume, schedule)
        service_path = os.path.join(self._systemd_dir, service)
        timer = None if schedule == _BOOT_SCHEDULE else self._timer_name(subvolume, schedule)
        timer_path = None if timer is None else os.path.join(self._systemd_dir, timer)
        base = {"subvolume": subvolume, "schedule": schedule}
        if not os.path.isfile(service_path) or (
            timer is not None and not os.path.isfile(timer_path)
        ):
            return {**base, "state": "unknown", "last_trigger": None, "next_trigger": None}

        svc = self._systemctl_show(service, _SHOW_SERVICE_PROPS)
        if svc is None:
            # Files are installed but systemd does not know them (no
            # daemon-reload since install, or systemd unreachable).
            return {**base, "state": "unknown", "last_trigger": None, "next_trigger": None}
        if svc.get("ActiveState") == "failed":
            return {**base, "state": "failed", "last_trigger": None, "next_trigger": None}

        if timer is None:
            # Boot tier: enabled against multi-user.target; the oneshot
            # runs once per boot, then rests inactive.  Enabled + an
            # actual execution is "ok"; enabled but never executed at any
            # boot is "never" (there is no run evidence at all).
            enabled = svc.get("UnitFileState") in ("enabled", "enabled-runtime")
            if not enabled:
                state = "inactive"
            elif svc.get("ExecMainStartTimestamp") is None:
                state = "never"
            else:
                state = "ok"
            return {**base, "state": state, "last_trigger": None, "next_trigger": None}

        tdata = self._systemctl_show(timer, _SHOW_TIMER_PROPS)
        if tdata is None:
            return {**base, "state": "unknown", "last_trigger": None, "next_trigger": None}
        last_trigger = tdata.get("LastTriggerUSec")
        next_trigger = tdata.get("NextElapseUSecRealtime")
        active = tdata.get("ActiveState") == "active"
        enabled = tdata.get("UnitFileState") in ("enabled", "enabled-runtime")
        if not active or not enabled:
            return {
                **base,
                "state": "inactive",
                "last_trigger": last_trigger,
                "next_trigger": next_trigger,
            }

        last = self._parse_epoch(last_trigger)
        cadence = _CADENCE_SECONDS.get(schedule)
        executed = svc.get("ExecMainStartTimestamp") is not None
        if last is None:
            # Active and enabled, but never fired: "ok" would be a lie —
            # there is no evidence of a run at all.
            state = "never"
        elif not executed:
            # The timer fired (last_trigger set) but systemd never
            # executed the service — the fire never became a run (missing
            # CLI binary, unit vanished at trigger time, ...).
            state = "failed"
        elif cadence and (
            time.time() - last.timestamp() > _MISSED_FACTOR * cadence
        ):
            state = "missed"
        else:
            state = "ok"
        return {
            **base,
            "state": state,
            "last_trigger": last_trigger,
            "next_trigger": next_trigger,
        }

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

    @staticmethod
    def _resolve_cli_path(cli_path: Optional[str]) -> str:
        """Resolve the CLI path embedded in generated unit files.

        An explicit *cli_path* (packaging, tests) wins unconditionally.
        Otherwise the packaged default ``/usr/bin/manaradix`` is used
        when it exists; failing that, ``manaradix`` is looked up on
        ``PATH`` (a pip install in the invoking user's environment).
        When elevated via ``pkexec`` or ``sudo`` the environment is
        sanitized (or root-only), so the invoking user's
        ``~/.local/bin`` is also tried (``PKEXEC_UID`` / ``SUDO_UID``)
        — this makes generated units work on machines where the tool was
        installed with ``pip install --user`` instead of via the RPM.
        """
        if cli_path:
            return cli_path
        if os.path.isfile("/usr/bin/manaradix"):
            return "/usr/bin/manaradix"
        found = shutil.which("manaradix")
        if found:
            return found
        uid = os.environ.get("PKEXEC_UID") or os.environ.get("SUDO_UID")
        if uid:
            try:
                import pwd

                home = pwd.getpwuid(int(uid)).pw_dir
            except (KeyError, ValueError, OSError):
                home = None
            if home:
                user_bin = os.path.join(home, ".local", "bin", "manaradix")
                if os.path.isfile(user_bin):
                    return user_bin
        return "/usr/bin/manaradix"

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

    def _systemctl_show(self, unit: str, props: tuple[str, ...]) -> Optional[dict]:
        """Query systemd properties of *unit* via ``systemctl show``.

        Unprivileged read; returns a dict of the requested properties
        (None for values systemd reports as unset/"n/a"), or None when
        the unit is unknown or systemd is unreachable.

        ``systemctl show`` emits ``Name=value`` lines sorted
        alphabetically (not in ``--property=`` order), so the output is
        parsed by name rather than by position.
        """
        import subprocess

        cmd = [
            "systemctl", "show",
            f"--property={','.join(props)}",
            "--no-pager", "--timestamp=unix",
            unit,
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if proc.returncode != 0:
            return None
        wanted = set(props)
        out: dict[str, Optional[str]] = {}
        for line in proc.stdout.splitlines():
            name, sep, value = line.partition("=")
            if sep and name in wanted:
                out[name] = None if not value or value == "n/a" else value
        return out

    @staticmethod
    def _parse_epoch(value: Optional[str]) -> Optional[datetime]:
        """Parse a timestamp from ``systemctl show`` into a datetime.

        Accepts a unix epoch string (older systemd honors
        ``--timestamp=unix``) and the human-readable local format
        (``Mon 2026-08-03 07:42:02 CEST``) that systemd 257+ always
        emits — with a last-resort parse that drops an unrecognized
        timezone abbreviation and treats the wall time as local.
        """
        if not value:
            return None
        try:
            return datetime.fromtimestamp(int(value))
        except (ValueError, OSError):
            pass
        # Plain local wall time, no timezone suffix.
        try:
            return datetime.strptime(value, "%a %Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
        # With a timezone abbreviation — glibc's %Z typically consumes
        # the token without attaching an offset, which is fine: the
        # datetime is local either way.
        try:
            return datetime.strptime(value, "%a %Y-%m-%d %H:%M:%S %Z")
        except ValueError:
            pass
        # Unrecognized abbreviation: drop the trailing token and treat
        # the remaining wall time as local.
        try:
            return datetime.strptime(
                value.rsplit(" ", 1)[0], "%a %Y-%m-%d %H:%M:%S"
            )
        except (ValueError, IndexError):
            return None
