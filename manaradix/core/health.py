"""Pure health-check and retention-suggestion logic for the tray applet.

Everything in this module is frontend-free and daemon-free: functions
take plain data (dicts, dataclasses) and return plain data.  The live
sources (``os.statvfs``, the D-Bus daemon) are wired in
:mod:`manaradix.core.health_sources`, and the tray applet decides when
to run the checks and what to notify (``manaradix/tray/checks.py``).

Finding shapes
--------------
- Snapshots arrive as daemon-shaped dicts (``ListSnapshots`` output):
  ``{"id", "subvolume", "date" (ISO), "type", "schedule"}``.  The
  ``type`` is the :class:`~manaradix.model.snapshot.SnapshotType`
  value; ``schedule`` is the tier name (``"hourly"`` ... ``"monthly"``)
  or ``""`` for manual snapshots.
- Devices arrive as ``GetDeviceStats`` output (see
  ``manaradix/dbus/udisks.py``): btrfs error counters plus an optional
  nested ``smart`` dict.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dtime
from typing import Iterable, Optional, Tuple

from manaradix.i18n import _
from manaradix.model.policy import SnapshotPolicy

# ---------------------------------------------------------------------------
# Constants (mirrored in the spec; the [tray] config can override the
# space thresholds, never the profile strategies)
# ---------------------------------------------------------------------------

#: Default space thresholds, overridable via ``[tray]``.
DEFAULT_SPACE_WARN_PCT = 85
DEFAULT_SPACE_CRITICAL_PCT = 95

#: Profile flavors understood by the retention suggestion engine.
PROFILES = ("desktop", "kiosk", "server")

#: Kiosk: keep at most this many non-boot snapshots in total, and start
#: suggesting pausing schedules when free space drops below this.
KIOSK_MAX_TOTAL = 2
KIOSK_PAUSE_FREE_PCT = 20

#: Server: suggest deleting anything older than this, and scrubbing when
#: the last recorded scrub predates this (or never ran).
SERVER_MAX_AGE_DAYS = 30

#: A scrub that has been running longer than this is reported as stuck.
SERVER_STUCK_HOURS = 48

#: Drives at or above this temperature (°C) are reported as hot.
SMART_HOT_TEMP_C = 60

#: Tier names, in age order; ``manual`` snapshots are never suggested.
TIERS = ("hourly", "daily", "weekly", "monthly", "boot", "pre-upgrade")

#: Severity levels, weakest first.
SEVERITY_ORDER = ("info", "warning", "critical")

_SEVERITY_RANK = {level: i for i, level in enumerate(SEVERITY_ORDER)}


@dataclass
class HealthFinding:
    """One actionable finding produced by a health check.

    ``check`` is the check name (``"space"``, ``"devices"``,
    ``"retention"``, ``"scrub"``), ``finding_id`` a stable key the
    applet uses to deduplicate notifications (e.g. ``"space:/"``),
    and ``severity`` one of :data:`SEVERITY_ORDER`.  ``summary`` is
    the short notification title, ``details`` the body, and ``action``
    an optional suggested user action.
    """

    check: str
    finding_id: str
    severity: str
    summary: str
    details: str = ""
    action: str = ""

    def __post_init__(self) -> None:
        if self.severity not in _SEVERITY_RANK:
            raise ValueError(f"unknown severity: {self.severity!r}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def format_bytes(n: int) -> str:
    """Format *n* bytes as a human-readable string (binary units)."""
    value = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")  # pragma: no cover


def _parse_iso(text: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp, tolerating garbage (returns None)."""
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def classify_space(
    percent: float,
    warn: int = DEFAULT_SPACE_WARN_PCT,
    critical: int = DEFAULT_SPACE_CRITICAL_PCT,
) -> Optional[str]:
    """Classify a usage percentage.

    Returns ``"critical"`` at/above *critical*, ``"warning"`` at/above
    *warn*, else ``None``.
    """
    if percent >= critical:
        return "critical"
    if percent >= warn:
        return "warning"
    return None


def _tier_of(snap: dict) -> str:
    """Map a snapshot dict to its retention tier name."""
    stype = snap.get("type")
    if stype in ("boot", "BOOT"):
        return "boot"
    if stype in ("pre-upgrade", "PRE_UPGRADE"):
        return "pre-upgrade"
    schedule = snap.get("schedule") or ""
    return schedule if schedule in TIERS else "manual"


def _snap_date(snap: dict) -> Optional[datetime]:
    """The snapshot's creation time as a datetime, or None."""
    return _parse_iso(snap.get("date"))


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def space_findings(
    usages: Iterable[dict],
    *,
    warn: int = DEFAULT_SPACE_WARN_PCT,
    critical: int = DEFAULT_SPACE_CRITICAL_PCT,
) -> list[HealthFinding]:
    """Classify per-mount disk usage.

    *usages* is a list of ``{"mount", "percent_used", "total_bytes",
    "free_bytes"}`` records (as produced by
    :func:`manaradix.core.health_sources.statvfs_usages`).
    """
    findings: list[HealthFinding] = []
    for usage in usages:
        percent = float(usage["percent_used"])
        severity = classify_space(percent, warn=warn, critical=critical)
        if severity is None:
            continue
        mount = usage["mount"]
        total = format_bytes(int(usage["total_bytes"]))
        free = format_bytes(int(usage["free_bytes"]))
        if severity == "critical":
            summary = _("Disk space critical on %s") % mount
            details = _(
                "%(percent)d%% of %(total)s used, only %(free)s free"
            ) % {"percent": int(percent), "total": total, "free": free}
        else:
            summary = _("Disk space low on %s") % mount
            details = _(
                "%(percent)d%% of %(total)s used, %(free)s free"
            ) % {"percent": int(percent), "total": total, "free": free}
        findings.append(
            HealthFinding(
                check="space",
                finding_id=f"space:{mount}",
                severity=severity,
                summary=summary,
                details=details,
                action=_("Free some space, or let the retention profile prune "
                         "old snapshots."),
            )
        )
    return findings


def device_findings(devices: Iterable[dict]) -> list[HealthFinding]:
    """Turn merged btrfs + SMART device stats into findings.

    *devices* is a list of ``GetDeviceStats`` records: btrfs error
    counters plus optional flat ``smart_*`` keys (absent when
    UDisks2/SMART is unavailable).  IO errors are warnings; data-
    integrity errors (corruption/generation) and a failing SMART disk
    are critical.  SMART attribute values are normalized (100 =
    perfect), so they carry no absolute threshold — only the drive's
    own health verdict and temperature are used.
    """
    findings: list[HealthFinding] = []
    for dev in devices:
        device = dev["device"]
        io_errs = int(dev.get("write_io_errs", 0)) + int(dev.get("read_io_errs", 0)) + int(
            dev.get("flush_io_errs", 0)
        )
        integrity_errs = int(dev.get("corruption_errs", 0)) + int(
            dev.get("generation_errs", 0)
        )
        if integrity_errs > 0:
            findings.append(
                HealthFinding(
                    check="devices",
                    finding_id=f"devices:integrity:{device}",
                    severity="critical",
                    summary=_("Data integrity errors on %s") % device,
                    details=_(
                        "%(corruption)d corruption, %(generation)d generation "
                        "errors recorded by btrfs."
                    ) % {"corruption": int(dev.get("corruption_errs", 0)),
                         "generation": int(dev.get("generation_errs", 0))},
                    action=_("Check the device and restore from the newest "
                             "good snapshot."),
                )
            )
        elif io_errs > 0:
            findings.append(
                HealthFinding(
                    check="devices",
                    finding_id=f"devices:io:{device}",
                    severity="warning",
                    summary=_("IO errors on %s") % device,
                    details=_(
                        "%(write)d write, %(read)d read, %(flush)d flush "
                        "errors recorded by btrfs."
                    ) % {"write": int(dev.get("write_io_errs", 0)),
                         "read": int(dev.get("read_io_errs", 0)),
                         "flush": int(dev.get("flush_io_errs", 0))},
                    action=_("Inspect the device, cabling, and enclosure."),
                )
            )
        if not dev.get("smart_available"):
            continue
        if dev.get("smart_failing"):
            findings.append(
                HealthFinding(
                    check="devices",
                    finding_id=f"devices:smart:{device}",
                    severity="critical",
                    summary=_("SMART health failing on %s") % device,
                    details=_("The drive reports an imminent failure.  Back "
                              "up your data now."),
                    action=_("Replace the drive as soon as possible."),
                )
            )
        temperature = dev.get("smart_temperature")
        if temperature is not None and float(temperature) >= SMART_HOT_TEMP_C:
            findings.append(
                HealthFinding(
                    check="devices",
                    finding_id=f"devices:hot:{device}",
                    severity="warning",
                    summary=_("Drive running hot on %s") % device,
                    details=_("The drive is at %(temp)d °C.") % {
                        "temp": int(temperature)
                    },
                    action=_("Check airflow and cooling."),
                )
            )
    return findings


def scrub_findings(
    scrub: dict,
    *,
    profile: str = "desktop",
    now: Optional[datetime] = None,
) -> list[HealthFinding]:
    """Turn a scrub-status dict into findings.

    *scrub* carries ``data_extents_scrubbed``, ``errors``, ``running``
    and optionally ``last_started`` (ISO).  Errors are reported on all
    profiles; a scrub stuck for over :data:`SERVER_STUCK_HOURS` is a
    warning; the server profile additionally suggests a scrub when the
    last one is older than :data:`SERVER_MAX_AGE_DAYS` or none was
    ever recorded.
    """
    findings: list[HealthFinding] = []
    now = now or datetime.now()
    errors = int(scrub.get("errors", 0))
    if errors > 0:
        findings.append(
            HealthFinding(
                check="scrub",
                finding_id="scrub:errors",
                severity="warning",
                summary=_("Scrub found errors"),
                details=_("%d errors were found by the last scrub.") % errors,
                action=_("Run a new scrub and inspect the devices."),
            )
        )
    last_started = _parse_iso(scrub.get("last_started"))
    if scrub.get("running") and last_started is not None:
        elapsed = (now - last_started).total_seconds()
        if elapsed > SERVER_STUCK_HOURS * 3600:
            findings.append(
                HealthFinding(
                    check="scrub",
                    finding_id="scrub:stuck",
                    severity="warning",
                    summary=_("Scrub appears stuck"),
                    details=_(
                        "The scrub has been running for more than %d hours."
                    ) % SERVER_STUCK_HOURS,
                    action=_("Check the scrub progress in the GUI."),
                )
            )
    if profile == "server":
        if last_started is None:
            findings.append(
                HealthFinding(
                    check="scrub",
                    finding_id="scrub:stale",
                    severity="warning",
                    summary=_("No scrub recorded"),
                    details=_(
                        "No scrub run is recorded.  Regular scrubs catch "
                        "bit-rot on long-lived servers."
                    ),
                    action=_("Run a scrub now and schedule it regularly."),
                )
            )
        elif (now - last_started).days >= SERVER_MAX_AGE_DAYS:
            findings.append(
                HealthFinding(
                    check="scrub",
                    finding_id="scrub:stale",
                    severity="warning",
                    summary=_("Last scrub is stale"),
                    details=_(
                        "The last scrub is %d days old; run a new one."
                    ) % (now - last_started).days,
                    action=_("Run a scrub now."),
                )
            )
    return findings


# ---------------------------------------------------------------------------
# Profile-aware retention suggestions (spec §16.5)
# ---------------------------------------------------------------------------


def infer_profile(policy: SnapshotPolicy) -> str:
    """Infer the profile flavor from a policy's retention numbers.

    Used when ``[global] applied_profile`` is absent: tiny caps (≤ 2)
    everywhere suggest a kiosk, very lean weekly/monthly retention a
    server, anything else behaves like a desktop.  Falls back to
    ``"desktop"``.
    """
    keep = policy.retention
    caps = (
        keep.keep_hourly,
        keep.keep_daily,
        keep.keep_weekly,
        keep.keep_monthly,
        keep.keep_boot,
        keep.keep_pre_upgrade,
    )
    if all(cap <= 2 for cap in caps):
        return "kiosk"
    if keep.keep_weekly <= 1 and keep.keep_monthly <= 1:
        return "server"
    return "desktop"


def _cap_for(policy: SnapshotPolicy, tier: str) -> Optional[int]:
    """The retention cap for a tier name, or None for manual."""
    keep = policy.retention
    return {
        "hourly": keep.keep_hourly,
        "daily": keep.keep_daily,
        "weekly": keep.keep_weekly,
        "monthly": keep.keep_monthly,
        "boot": keep.keep_boot,
        "pre-upgrade": keep.keep_pre_upgrade,
    }.get(tier)


def _group_by_tier(
    snapshots: Iterable[dict], policy: SnapshotPolicy
) -> dict[str, list[dict]]:
    """Group snapshots by tier, restricted to the policy's subvolume."""
    groups: dict[str, list[dict]] = {}
    for snap in snapshots:
        if snap.get("subvolume") != policy.subvolume:
            continue
        tier = _tier_of(snap)
        if tier == "manual":
            continue
        groups.setdefault(tier, []).append(snap)
    return groups


def compute_retention_suggestions(
    snapshots: Iterable[dict],
    policy: SnapshotPolicy,
    profile: str = "desktop",
    *,
    now: Optional[datetime] = None,
    free_pct: Optional[float] = None,
) -> list[HealthFinding]:
    """Suggest snapshot deletions according to the profile flavor.

    One finding per (subvolume, tier) — never per snapshot — so a
    backlog floods at most a handful of notifications.  Boot and
    pre-upgrade tiers are guarded everywhere: they are only ever
    suggested when their own tier cap is exceeded.
    """
    findings: list[HealthFinding] = []
    if profile not in PROFILES:
        profile = "desktop"
    now = now or datetime.now()
    groups = _group_by_tier(snapshots, policy)

    def tier_finding(tier: str, count: int, cap: int) -> HealthFinding:
        return HealthFinding(
            check="retention",
            finding_id=f"retention:{policy.subvolume}:{tier}",
            severity="warning",
            summary=_("Too many %(tier)s snapshots on %(subvol)s") % {
                "tier": tier, "subvol": policy.subvolume,
            },
            details=_(
                "%(count)d snapshots kept, retention caps at %(cap)d."
            ) % {"count": count, "cap": cap},
            action=_("Delete the oldest ones, or adjust the retention on "
                     "the Policies page."),
        )

    if profile == "kiosk":
        total = sum(len(group) for tier, group in groups.items())
        protected = len(groups.get("boot", [])) + len(groups.get("pre-upgrade", []))
        deleteable = total - protected
        if deleteable > KIOSK_MAX_TOTAL:
            findings.append(
                HealthFinding(
                    check="retention",
                    finding_id=f"retention:{policy.subvolume}:total",
                    severity="warning",
                    summary=_("Too many snapshots on %s (kiosk)") % policy.subvolume,
                    details=_(
                        "Kiosk profiles keep at most %(max)d snapshots; "
                        "%(count)d exist."
                    ) % {"max": KIOSK_MAX_TOTAL, "count": deleteable},
                    action=_("Delete the oldest snapshots and keep the last "
                             "good state."),
                )
            )
        for tier in ("boot", "pre-upgrade"):
            cap = _cap_for(policy, tier)
            if cap and len(groups.get(tier, [])) > cap:
                findings.append(tier_finding(tier, len(groups[tier]), cap))
        if free_pct is not None and free_pct < KIOSK_PAUSE_FREE_PCT:
            findings.append(
                HealthFinding(
                    check="retention",
                    finding_id=f"retention:{policy.subvolume}:pause",
                    severity="warning",
                    summary=_("Free space below %d%% on %s") % (
                        KIOSK_PAUSE_FREE_PCT, policy.subvolume,
                    ),
                    details=_(
                        "The kiosk is nearly full; new snapshots may fail."
                    ),
                    action=_("Consider pausing the hourly and daily snapshot "
                             "schedules."),
                )
            )
        return findings

    if profile == "server":
        for tier in ("hourly", "daily", "weekly", "monthly"):
            stale = [
                snap for snap in groups.get(tier, [])
                if (snap_date := _snap_date(snap)) is not None
                and (now - snap_date).days >= SERVER_MAX_AGE_DAYS
            ]
            if stale:
                oldest = min(stale, key=lambda s: s.get("date", ""))
                findings.append(
                    HealthFinding(
                        check="retention",
                        finding_id=f"retention:{policy.subvolume}:{tier}",
                        severity="warning",
                        summary=_("%(tier)s snapshots older than %(days)d days "
                                  "on %(subvol)s") % {
                            "tier": tier, "days": SERVER_MAX_AGE_DAYS,
                            "subvol": policy.subvolume,
                        },
                        details=_(
                            "%(count)d snapshots, oldest from %(date)s."
                        ) % {"count": len(stale), "date": oldest.get("date", "?")},
                        action=_("Delete the old ones, or extend the retention "
                                 "policy."),
                    )
                )
        for tier in ("boot", "pre-upgrade"):
            cap = _cap_for(policy, tier)
            if cap and len(groups.get(tier, [])) > cap:
                findings.append(tier_finding(tier, len(groups[tier]), cap))
        return findings

    # Desktop: count-based per tier.
    for tier, snaps in groups.items():
        cap = _cap_for(policy, tier)
        if cap and len(snaps) > cap:
            findings.append(tier_finding(tier, len(snaps), cap))
    return findings


# ---------------------------------------------------------------------------
# Quiet hours (spec §16.3)
# ---------------------------------------------------------------------------


def parse_quiet_hours(spec: str) -> Optional[Tuple[dtime, dtime]]:
    """Parse a ``"HH:MM-HH:MM"`` quiet-hours spec.

    Returns ``(start, end)`` or ``None`` when malformed.  ``start >
    end`` means the window crosses midnight.
    """
    try:
        start_s, end_s = spec.split("-", 1)
        return dtime.fromisoformat(start_s.strip()), dtime.fromisoformat(
            end_s.strip()
        )
    except (ValueError, AttributeError):
        return None


def in_quiet_hours(
    spec: Optional[str], now: Optional[datetime] = None
) -> bool:
    """True when *now* falls inside the ``"HH:MM-HH:MM"`` window."""
    pair = parse_quiet_hours(spec) if spec else None
    if not pair:
        return False
    start, end = pair
    current = (now or datetime.now()).time()
    if start <= end:
        return start <= current < end
    return current >= start or current < end  # overnight window
