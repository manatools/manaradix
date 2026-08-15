"""Configuration persistence for manaradix.

Reads and writes ``/etc/manaradix/config.toml`` (TOML format).
Supports per-subvolume snapshot policies and global settings.
"""

from __future__ import annotations

import logging
import os
from copy import deepcopy
from dataclasses import dataclass
from typing import Optional

import tomlkit

from manaradix.model.policy import (
    RetentionPolicy,
    SnapshotPolicy,
)

logger = logging.getLogger(__name__)


@dataclass
class TraySettings:
    """The ``[tray]`` section: the notification-area applet behavior.

    All checks can be tuned per-check (thresholds in percent of used
    space, intervals in minutes); ``quiet_hours`` is an optional
    ``"HH:MM-HH:MM"`` window (start > end crosses midnight, empty
    string disables it).  The applet reads this section only — it is
    session-bound and never writes the system config.
    """

    enabled: bool = True
    autostart: bool = True
    hide_when_ok: bool = True
    quiet_hours: str = ""
    space_enabled: bool = True
    device_enabled: bool = True
    retention_enabled: bool = True
    scrub_enabled: bool = True
    space_warn_pct: int = 85
    space_critical_pct: int = 95
    space_interval_min: int = 10
    device_interval_min: int = 60
    retention_interval_min: int = 1440
    scrub_interval_min: int = 60


class ConfigError(Exception):
    """Raised when configuration is invalid or unreadable."""


class ConfigStore:
    """Persists and loads manaradix configuration.

    Usage::

        store = ConfigStore()
        store.load()
        print(store.default_subvolume)
        policy = store.get_policy("/")
        policy.schedule_hourly = True
        store.set_policy(policy)
        store.save()
    """

    DEFAULT_PATH = "/etc/manaradix/config.toml"

    def __init__(self, path: str = DEFAULT_PATH) -> None:
        self._path = path
        self._doc: Optional[tomlkit.TOMLDocument] = None

        # Global settings
        self._default_subvolume: str = "/"
        self._snapshot_dir: str = ".snapshots"
        self._boot_from_snapshot: bool = False
        self._snapshot_before_upgrade: bool = False
        self._applied_profile: str = ""

        # Tray applet settings ([tray] section)
        self._tray = TraySettings()

        # Per-subvolume policies, keyed by subvolume path
        self._policies: dict[str, SnapshotPolicy] = {}

    # ------------------------------------------------------------------
    # Global property accessors
    # ------------------------------------------------------------------

    @property
    def default_subvolume(self) -> str:
        """The ``[global] default_subvolume`` value: the subvolume used
        as the default target for operations.

        Returns:
            The default subvolume path (default ``"/"``).
        """
        return self._default_subvolume

    @default_subvolume.setter
    def default_subvolume(self, value: str) -> None:
        """Set the ``[global] default_subvolume`` value.

        Args:
            value: The default subvolume path.
        """
        self._default_subvolume = value

    @property
    def snapshot_dir(self) -> str:
        """The ``[global] snapshot_dir`` value: the directory, relative
        to each btrfs mount, where snapshots are stored.

        Returns:
            The snapshot directory (default ``".snapshots"``).
        """
        return self._snapshot_dir

    @snapshot_dir.setter
    def snapshot_dir(self, value: str) -> None:
        """Set the ``[global] snapshot_dir`` value.

        Args:
            value: The snapshot directory relative to the btrfs mount.
        """
        self._snapshot_dir = value

    @property
    def boot_from_snapshot(self) -> bool:
        """The ``[global] boot_from_snapshot`` value: whether snapshots
        appear as bootable entries in the GRUB menu.

        Returns:
            True when snapshots are listed in GRUB (default False).
        """
        return self._boot_from_snapshot

    @boot_from_snapshot.setter
    def boot_from_snapshot(self, value: bool) -> None:
        """Set the ``[global] boot_from_snapshot`` value.

        Args:
            value: Whether snapshots appear as bootable GRUB entries.
        """
        self._boot_from_snapshot = value

    @property
    def snapshot_before_upgrade(self) -> bool:
        """Whether a pre-upgrade snapshot is taken before package updates
        (the ``[global] snapshot_before_upgrade`` value).

        Returns:
            True when pre-upgrade snapshots are enabled (default False).
        """
        return self._snapshot_before_upgrade

    @snapshot_before_upgrade.setter
    def snapshot_before_upgrade(self, value: bool) -> None:
        """Set the ``[global] snapshot_before_upgrade`` value.

        Args:
            value: Whether a pre-upgrade snapshot is taken before
                package updates.
        """
        self._snapshot_before_upgrade = value

    @property
    def applied_profile(self) -> str:
        """The ``[global] applied_profile`` value: the id of the setup
        profile most recently applied by the setup wizard ("" when never
        applied).

        The tray applet uses it to pick the retention-suggestion
        flavor (desktop / kiosk / server); when absent it falls back to
        :func:`manaradix.core.health.infer_profile`.

        Returns:
            The applied profile id (default "").
        """
        return self._applied_profile

    @applied_profile.setter
    def applied_profile(self, value: str) -> None:
        """Set the ``[global] applied_profile`` value.

        Args:
            value: The profile id applied by the setup wizard.
        """
        self._applied_profile = value

    @property
    def tray(self) -> TraySettings:
        """The ``[tray]`` section: the notification-area applet settings."""
        return self._tray

    @tray.setter
    def tray(self, settings: TraySettings) -> None:
        """Replace the whole ``[tray]`` settings object.

        Args:
            settings: The new tray settings.
        """
        self._tray = settings

    # ------------------------------------------------------------------
    # Policy accessors
    # ------------------------------------------------------------------

    def get_policy(self, subvolume: str) -> Optional[SnapshotPolicy]:
        """Return the policy for *subvolume*, or None."""
        return self._policies.get(subvolume)

    def list_policies(self) -> list[SnapshotPolicy]:
        """Return all configured policies."""
        return list(self._policies.values())

    def set_policy(self, policy: SnapshotPolicy) -> None:
        """Add or replace a policy for its subvolume."""
        if not policy.subvolume:
            raise ConfigError("Policy must have a non-empty subvolume")
        self._policies[policy.subvolume] = policy

    def remove_policy(self, subvolume: str) -> None:
        """Remove the policy for *subvolume*."""
        self._policies.pop(subvolume, None)

    def toggle_schedule(
        self, subvolume: str, schedule: str, enabled: bool
    ) -> Optional[SnapshotPolicy]:
        """Set one schedule flag on a subvolume's policy.

        Enabling a schedule re-activates the policy; disabling its last
        schedule disables the policy (a policy with no schedules is
        invalid — :meth:`SnapshotPolicy.validate` requires at least one).

        Args:
            subvolume: The policy's subvolume.
            schedule: One of ``hourly``/``daily``/``weekly``/``monthly``/
                ``boot`` (``pre-upgrade`` is a global flag, not a policy
                schedule).
            enabled: The new schedule state.

        Returns:
            The updated policy, or None when the subvolume has no policy.
        """
        policy = self._policies.get(subvolume)
        if policy is None:
            return None
        flag = f"schedule_{schedule}"
        if not hasattr(policy, flag):
            return policy
        setattr(policy, flag, enabled)
        if enabled:
            policy.enabled = True
        elif not policy.active_schedules:
            policy.enabled = False
        return policy

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load configuration from disk.

        If the file does not exist, defaults are used (no error raised).
        """
        if not os.path.isfile(self._path):
            logger.info("Config file not found at %s, using defaults", self._path)
            return

        try:
            with open(self._path) as fh:
                self._doc = tomlkit.parse(fh.read())
        except (OSError, tomlkit.exceptions.ParseError) as exc:
            raise ConfigError(f"Failed to read config: {exc}") from exc

        self._parse_document()

    def _parse_document(self) -> None:
        """Extract values from the parsed TOML document."""
        doc = self._doc
        if doc is None:
            return

        # Global section
        global_section = doc.get("global", {})
        if isinstance(global_section, dict):
            self._default_subvolume = str(
                global_section.get("default_subvolume", self._default_subvolume)
            )
            self._snapshot_dir = str(
                global_section.get("snapshot_dir", self._snapshot_dir)
            )
            self._boot_from_snapshot = bool(
                global_section.get("boot_from_snapshot", self._boot_from_snapshot)
            )
            self._snapshot_before_upgrade = bool(
                global_section.get(
                    "snapshot_before_upgrade", self._snapshot_before_upgrade
                )
            )
            self._applied_profile = str(
                global_section.get("applied_profile", self._applied_profile)
            )

        # Tray section
        tray_section = doc.get("tray", {})
        if isinstance(tray_section, dict):
            self._tray = TraySettings(
                enabled=bool(tray_section.get("enabled", self._tray.enabled)),
                autostart=bool(tray_section.get("autostart", self._tray.autostart)),
                hide_when_ok=bool(
                    tray_section.get("hide_when_ok", self._tray.hide_when_ok)
                ),
                space_enabled=bool(
                    tray_section.get("space_enabled", self._tray.space_enabled)
                ),
                device_enabled=bool(
                    tray_section.get("device_enabled", self._tray.device_enabled)
                ),
                retention_enabled=bool(
                    tray_section.get(
                        "retention_enabled", self._tray.retention_enabled
                    )
                ),
                scrub_enabled=bool(
                    tray_section.get("scrub_enabled", self._tray.scrub_enabled)
                ),
                quiet_hours=str(
                    tray_section.get("quiet_hours", self._tray.quiet_hours)
                ),
                space_warn_pct=int(
                    tray_section.get("space_warn_pct", self._tray.space_warn_pct)
                ),
                space_critical_pct=int(
                    tray_section.get(
                        "space_critical_pct", self._tray.space_critical_pct
                    )
                ),
                space_interval_min=int(
                    tray_section.get(
                        "space_interval_min", self._tray.space_interval_min
                    )
                ),
                device_interval_min=int(
                    tray_section.get(
                        "device_interval_min", self._tray.device_interval_min
                    )
                ),
                retention_interval_min=int(
                    tray_section.get(
                        "retention_interval_min", self._tray.retention_interval_min
                    )
                ),
                scrub_interval_min=int(
                    tray_section.get(
                        "scrub_interval_min", self._tray.scrub_interval_min
                    )
                ),
            )

        # Policies section
        policies_section = doc.get("policies", {})
        if isinstance(policies_section, dict):
            for subvol, raw in policies_section.items():
                if not isinstance(raw, dict):
                    continue
                retention_raw = raw.get("retention", {}) if isinstance(raw, dict) else {}
                retention = RetentionPolicy(
                    keep_hourly=int(retention_raw.get("keep_hourly", 10)),
                    keep_daily=int(retention_raw.get("keep_daily", 7)),
                    keep_weekly=int(retention_raw.get("keep_weekly", 4)),
                    keep_monthly=int(retention_raw.get("keep_monthly", 3)),
                    keep_boot=int(retention_raw.get("keep_boot", 5)),
                    keep_pre_upgrade=int(
                        retention_raw.get("keep_pre_upgrade", 3)
                    ),
                )
                policy = SnapshotPolicy(
                    subvolume=str(subvol),
                    enabled=bool(raw.get("enabled", True)),
                    schedule_hourly=bool(raw.get("schedule_hourly", False)),
                    schedule_daily=bool(raw.get("schedule_daily", True)),
                    schedule_weekly=bool(raw.get("schedule_weekly", False)),
                    schedule_monthly=bool(raw.get("schedule_monthly", False)),
                    schedule_boot=bool(raw.get("schedule_boot", False)),
                    retention=retention,
                )
                self._policies[str(subvol)] = policy

    def save(self) -> None:
        """Write configuration to disk.

        Creates parent directories if needed.
        """
        doc = tomlkit.document()
        doc.add(tomlkit.comment("manaradix configuration"))

        # Global section
        global_table = tomlkit.table()
        global_table.add("default_subvolume", self._default_subvolume)
        global_table.add("snapshot_dir", self._snapshot_dir)
        global_table.add("boot_from_snapshot", self._boot_from_snapshot)
        global_table.add("snapshot_before_upgrade", self._snapshot_before_upgrade)
        global_table.add("applied_profile", self._applied_profile)
        doc.add("global", global_table)

        # Tray section
        tray_table = tomlkit.table()
        tray_table.add("enabled", self._tray.enabled)
        tray_table.add("autostart", self._tray.autostart)
        tray_table.add("hide_when_ok", self._tray.hide_when_ok)
        tray_table.add("space_enabled", self._tray.space_enabled)
        tray_table.add("device_enabled", self._tray.device_enabled)
        tray_table.add("retention_enabled", self._tray.retention_enabled)
        tray_table.add("scrub_enabled", self._tray.scrub_enabled)
        tray_table.add("quiet_hours", self._tray.quiet_hours)
        tray_table.add("space_warn_pct", self._tray.space_warn_pct)
        tray_table.add("space_critical_pct", self._tray.space_critical_pct)
        tray_table.add("space_interval_min", self._tray.space_interval_min)
        tray_table.add("device_interval_min", self._tray.device_interval_min)
        tray_table.add("retention_interval_min", self._tray.retention_interval_min)
        tray_table.add("scrub_interval_min", self._tray.scrub_interval_min)
        doc.add("tray", tray_table)

        # Policies section
        if self._policies:
            policies_table = tomlkit.table()
            for subvol, policy in sorted(self._policies.items()):
                policy_table = tomlkit.table()
                policy_table.add("enabled", policy.enabled)
                policy_table.add("schedule_hourly", policy.schedule_hourly)
                policy_table.add("schedule_daily", policy.schedule_daily)
                policy_table.add("schedule_weekly", policy.schedule_weekly)
                policy_table.add("schedule_monthly", policy.schedule_monthly)
                policy_table.add("schedule_boot", policy.schedule_boot)

                retention_table = tomlkit.table()
                retention_table.add("keep_hourly", policy.retention.keep_hourly)
                retention_table.add("keep_daily", policy.retention.keep_daily)
                retention_table.add("keep_weekly", policy.retention.keep_weekly)
                retention_table.add("keep_monthly", policy.retention.keep_monthly)
                retention_table.add("keep_boot", policy.retention.keep_boot)
                retention_table.add(
                    "keep_pre_upgrade", policy.retention.keep_pre_upgrade
                )
                policy_table.add("retention", retention_table)

                policies_table.add(subvol, policy_table)
            doc.add("policies", policies_table)

        # Write
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        with open(self._path, "w") as fh:
            fh.write(tomlkit.dumps(doc))

        self._doc = doc
        logger.info("Configuration saved to %s", self._path)

    def to_dict(self) -> dict:
        """Export all settings as a plain dict (useful for UI display)."""
        return {
            "global": {
                "default_subvolume": self._default_subvolume,
                "snapshot_dir": self._snapshot_dir,
                "boot_from_snapshot": self._boot_from_snapshot,
                "snapshot_before_upgrade": self._snapshot_before_upgrade,
                "applied_profile": self._applied_profile,
            },
            "tray": {
                "enabled": self._tray.enabled,
                "autostart": self._tray.autostart,
                "hide_when_ok": self._tray.hide_when_ok,
                "space_enabled": self._tray.space_enabled,
                "device_enabled": self._tray.device_enabled,
                "retention_enabled": self._tray.retention_enabled,
                "scrub_enabled": self._tray.scrub_enabled,
                "quiet_hours": self._tray.quiet_hours,
                "space_warn_pct": self._tray.space_warn_pct,
                "space_critical_pct": self._tray.space_critical_pct,
                "space_interval_min": self._tray.space_interval_min,
                "device_interval_min": self._tray.device_interval_min,
                "retention_interval_min": self._tray.retention_interval_min,
                "scrub_interval_min": self._tray.scrub_interval_min,
            },
            "policies": {
                subvol: {
                    "enabled": p.enabled,
                    "schedule_hourly": p.schedule_hourly,
                    "schedule_daily": p.schedule_daily,
                    "schedule_weekly": p.schedule_weekly,
                    "schedule_monthly": p.schedule_monthly,
                    "schedule_boot": p.schedule_boot,
                    "retention": {
                        "keep_hourly": p.retention.keep_hourly,
                        "keep_daily": p.retention.keep_daily,
                        "keep_weekly": p.retention.keep_weekly,
                        "keep_monthly": p.retention.keep_monthly,
                        "keep_boot": p.retention.keep_boot,
                        "keep_pre_upgrade": p.retention.keep_pre_upgrade,
                    },
                }
                for subvol, p in self._policies.items()
            },
        }
