"""Setup profile templates for the first-run wizard.

Instead of one hardcoded baseline, the wizard proposes **setup profiles**
(desktop, server, kiosk, ...) that map to concrete snapshot policies.
Each profile is a TOML file (parsed with the already-required ``tomlkit``)
describing global settings and a list of per-subvolume policies:

.. code-block:: toml

    id = "desktop"
    name = "Desktop workstation"
    role = "desktop"                # "desktop" | "server" | "any"

    [global]
    boot_from_snapshot = true       # only effective on a btrfs root + GRUB

    [[policies]]
    subvolume = "/"
    schedules = ["daily", "weekly", "boot"]
    keep_daily = 7                  # optional; model defaults fill the rest

Templates are searched in precedence order (later directories override
profiles with the same ``id``):

1. ``$MANARADIX_TEMPLATE_DIRS`` (colon-separated)
2. the repo ``data/templates/`` directory when running from a checkout
3. ``/usr/share/manaradix/templates/`` (shipped by the RPM)
4. ``/etc/manaradix/templates.d/`` (admin overrides)

A malformed file is logged and skipped — the wizard must work even when
a user template is broken.  Pure data + parsing: no UI imports.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import tomlkit

from manaradix.model.policy import RetentionPolicy

logger = logging.getLogger(__name__)

#: Roles a profile can declare; ``any`` profiles are never auto-selected.
VALID_ROLES = ("desktop", "server", "any")

VALID_SCHEDULES = ("hourly", "daily", "weekly", "monthly", "boot")

_KEEP_FIELDS = (
    "keep_hourly",
    "keep_daily",
    "keep_weekly",
    "keep_monthly",
    "keep_boot",
)

#: Repo checkout location of the shipped templates (editable installs).
_REPO_TEMPLATES = Path(__file__).resolve().parents[2] / "data" / "templates"

SYSTEM_TEMPLATE_DIR = Path("/usr/share/manaradix/templates")
USER_TEMPLATE_DIR = Path("/etc/manaradix/templates.d")


@dataclass
class TemplatePolicy:
    """One per-subvolume policy entry of a setup profile.

    Attributes:
        subvolume: Subvolume (mount point) the policy applies to.
        schedules: Active schedule names (subset of ``VALID_SCHEDULES``).
        retention: Retention settings; unspecified ``keep_*`` values use
            the model defaults.
        required: If True, the policy is expected on the system — a
            missing subvolume is shown to the user instead of being
            silently dropped.
    """

    subvolume: str
    schedules: list[str]
    retention: RetentionPolicy = field(default_factory=RetentionPolicy)
    required: bool = True


@dataclass
class SetupProfile:
    """A named, shippable setup profile (one TOML template file)."""

    id: str
    name: str
    description: str = ""
    role: str = "any"
    boot_from_snapshot: bool = False
    snapshot_dir: str = ".snapshots"
    policies: list[TemplatePolicy] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Template discovery
# ---------------------------------------------------------------------------


def default_template_dirs() -> list[Path]:
    """Return the template search path in precedence order."""
    dirs: list[Path] = []
    env = os.environ.get("MANARADIX_TEMPLATE_DIRS", "")
    dirs.extend(Path(p) for p in env.split(":") if p)
    if _REPO_TEMPLATES.is_dir():
        dirs.append(_REPO_TEMPLATES)
    dirs.append(SYSTEM_TEMPLATE_DIR)
    dirs.append(USER_TEMPLATE_DIR)
    return dirs


def load_profiles(dirs: list[Path] | None = None) -> list[SetupProfile]:
    """Load all setup profiles, in ``dirs`` precedence order.

    Later directories override profiles with the same ``id``.  A
    malformed or invalid file is skipped with a warning, never fatal.
    """
    if dirs is None:
        dirs = default_template_dirs()

    profiles: dict[str, SetupProfile] = {}
    for directory in dirs:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.toml")):
            profile = _parse_profile(path)
            if profile is not None:
                profiles[profile.id] = profile

    return list(profiles.values())


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_profile(path: Path) -> SetupProfile | None:
    """Parse one template file, or None when invalid (warning logged)."""
    try:
        doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    except (OSError, tomlkit.exceptions.ParseError) as exc:
        logger.warning("Could not parse setup profile %s: %s", path, exc)
        return None

    profile_id = doc.get("id")
    name = doc.get("name")
    if not profile_id or not name:
        logger.warning(
            "Setup profile %s is missing the required 'id' or 'name'; skipping",
            path,
        )
        return None

    role = str(doc.get("role", "any"))
    if role not in VALID_ROLES:
        logger.warning(
            "Setup profile %s has unknown role %r; treating as 'any'", path, role
        )
        role = "any"

    global_section = doc.get("global", {})
    if not isinstance(global_section, dict):
        global_section = {}
    boot_from_snapshot = bool(global_section.get("boot_from_snapshot", False))
    snapshot_dir = str(global_section.get("snapshot_dir", ".snapshots"))

    policies: list[TemplatePolicy] = []
    raw_policies = doc.get("policies") or []
    for raw in raw_policies:
        if not isinstance(raw, dict):
            continue
        policy = _parse_policy(raw, path)
        if policy is not None:
            policies.append(policy)

    return SetupProfile(
        id=str(profile_id),
        name=str(name),
        description=str(doc.get("description", "")),
        role=role,
        boot_from_snapshot=boot_from_snapshot,
        snapshot_dir=snapshot_dir,
        policies=policies,
    )


def _parse_policy(raw: dict, source: Path) -> TemplatePolicy | None:
    """Parse one [[policies]] entry, or None when invalid."""
    subvolume = raw.get("subvolume")
    if not subvolume:
        logger.warning(
            "Setup profile %s has a policy without a subvolume; skipping", source
        )
        return None

    schedules: list[str] = []
    for schedule in raw.get("schedules", []) or []:
        if schedule in VALID_SCHEDULES:
            schedules.append(schedule)
        else:
            logger.warning(
                "Setup profile %s: unknown schedule %r; dropping", source, schedule
            )
    if not schedules:
        logger.warning(
            "Setup profile %s: policy for %s has no valid schedules; skipping",
            source,
            subvolume,
        )
        return None

    keep: dict[str, int] = {}
    for keep_field in _KEEP_FIELDS:
        value = raw.get(keep_field)
        if isinstance(value, int):
            keep[keep_field] = value
        elif value is not None:
            logger.warning(
                "Setup profile %s: %s=%r is not an integer; using the default",
                source,
                keep_field,
                value,
            )

    required = raw.get("required", True)
    if not isinstance(required, bool):
        logger.warning(
            "Setup profile %s: 'required' must be a boolean; assuming true",
            source,
        )
        required = True

    return TemplatePolicy(
        subvolume=str(subvolume),
        schedules=schedules,
        retention=RetentionPolicy(**keep),
        required=required,
    )
