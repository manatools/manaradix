"""Finding tracking and re-nag scheduling (spec §16.3).

The tray notifies on the *transition* into a finding, then re-nags at
a per-check cadence while the finding stays active.  :class:`FindingTracker`
is the pure state machine behind that rule; it is fed the merged
findings of every check cycle and returns exactly the findings that
should be turned into notifications now.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from manaradix.core.health import HealthFinding, SEVERITY_ORDER

#: Re-nag cadence per check while a finding stays active (seconds).
NAG_INTERVALS: dict[str, float] = {
    "space": 24 * 3600,
    "devices": 24 * 3600,
    "retention": 7 * 24 * 3600,
    "scrub": 24 * 3600,
}

#: Default cadence for checks not listed above.
DEFAULT_NAG_INTERVAL = 24 * 3600

_SEVERITY_RANK = {level: i for i, level in enumerate(SEVERITY_ORDER)}


class FindingTracker:
    """Remembers active findings and decides what to notify now.

    Args:
        now_fn: Time source in seconds (default ``time.monotonic``);
            injectable so tests can step the clock.
        nag_intervals: Per-check re-nag cadence in seconds.
    """

    def __init__(
        self,
        *,
        now_fn: Callable[[], float] = time.monotonic,
        nag_intervals: Optional[dict[str, float]] = None,
    ) -> None:
        self._now_fn = now_fn
        self._nag = dict(nag_intervals or NAG_INTERVALS)
        # finding_id -> (severity, first_seen_ts)
        self._active: dict[str, tuple[str, float]] = {}
        # finding_id -> last notification ts
        self._last_notified: dict[str, float] = {}

    def _nag_interval(self, check: str) -> float:
        return self._nag.get(check, DEFAULT_NAG_INTERVAL)

    def update(self, findings: list[HealthFinding]) -> list[HealthFinding]:
        """Feed one merged findings batch; returns findings to notify.

        Rules:
        - A finding that just became active is notified (first time).
        - A severity upgrade (warning → critical) is notified again.
        - An unchanged active finding is re-notified only after its
          check's nag cadence has elapsed.
        - Findings that disappeared are forgotten silently.
        """
        now = self._now_fn()
        current = {finding.finding_id: finding for finding in findings}

        to_notify: list[HealthFinding] = []
        for finding_id, finding in current.items():
            previous = self._active.get(finding_id)
            if previous is None:
                self._active[finding_id] = (finding.severity, now)
                self._last_notified[finding_id] = now
                to_notify.append(finding)
                continue
            prev_severity, _first = previous
            upgraded = (
                _SEVERITY_RANK.get(finding.severity, 0)
                > _SEVERITY_RANK.get(prev_severity, 0)
            )
            if upgraded:
                self._active[finding_id] = (finding.severity, now)
                self._last_notified[finding_id] = now
                to_notify.append(finding)
                continue
            last = self._last_notified.get(finding_id, 0.0)
            if now - last >= self._nag_interval(finding.check):
                self._last_notified[finding_id] = now
                to_notify.append(finding)

        for finding_id in list(self._active):
            if finding_id not in current:
                del self._active[finding_id]
        return to_notify

    @property
    def active_ids(self) -> set[str]:
        """The finding ids currently tracked as active."""
        return set(self._active)
