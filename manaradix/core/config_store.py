"""Configuration persistence for manaradix.

Reads and writes ``/etc/manaradix/config.toml`` (TOML format).
Supports per-subvolume snapshot policies and global settings.
"""

from __future__ import annotations

import logging
import os
from copy import deepcopy
from typing import Optional

import tomlkit

from manaradix.model.policy import (
    RetentionPolicy,
    SnapshotPolicy,
)

logger = logging.getLogger(__name__)

# Default content written when no config file exists.
_DEFAULT_CONFIG = """\
[global]
default_subvolume = "/"
snapshot_dir = ".snapshots"
boot_from_snapshot = false
"""


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

        # Per-subvolume policies, keyed by subvolume path
        self._policies: dict[str, SnapshotPolicy] = {}

    # ------------------------------------------------------------------
    # Global property accessors
    # ------------------------------------------------------------------

    @property
    def default_subvolume(self) -> str:
        return self._default_subvolume

    @default_subvolume.setter
    def default_subvolume(self, value: str) -> None:
        self._default_subvolume = value

    @property
    def snapshot_dir(self) -> str:
        return self._snapshot_dir

    @snapshot_dir.setter
    def snapshot_dir(self, value: str) -> None:
        self._snapshot_dir = value

    @property
    def boot_from_snapshot(self) -> bool:
        return self._boot_from_snapshot

    @boot_from_snapshot.setter
    def boot_from_snapshot(self, value: bool) -> None:
        self._boot_from_snapshot = value

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
        doc.add("global", global_table)

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
                    },
                }
                for subvol, p in self._policies.items()
            },
        }
