"""Tests for the finding tracker (notification transition state)."""

from manaradix.core.health import HealthFinding
from manaradix.tray.scheduler import (
    DEFAULT_NAG_INTERVAL,
    FindingTracker,
    NAG_INTERVALS,
)


def _finding(check: str, finding_id: str, severity: str = "warning",
             summary: str = "s") -> HealthFinding:
    return HealthFinding(check=check, finding_id=finding_id,
                         severity=severity, summary=summary)


class SteppingClock:
    """Injectable time source stepping in fixed increments."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestFindingTracker:
    def test_new_finding_notified(self):
        clock = SteppingClock()
        tracker = FindingTracker(now_fn=clock)
        out = tracker.update([_finding("space", "space:/")])
        assert [f.finding_id for f in out] == ["space:/"]
        assert tracker.active_ids == {"space:/"}

    def test_unchanged_finding_notified_after_nag_cadence(self):
        clock = SteppingClock()
        tracker = FindingTracker(now_fn=clock)
        findings = [_finding("space", "space:/")]
        tracker.update(findings)
        # well before the 24 h nag cadence: silence
        clock.advance(3600)
        assert tracker.update(findings) == []
        # past the cadence: re-nag
        clock.advance(24 * 3600)
        assert [f.finding_id for f in tracker.update(findings)] == ["space:/"]

    def test_upgrade_notifies_immediately(self):
        clock = SteppingClock()
        tracker = FindingTracker(now_fn=clock)
        tracker.update([_finding("space", "space:/", "warning")])
        clock.advance(60)
        out = tracker.update([_finding("space", "space:/", "critical")])
        assert [f.finding_id for f in out] == ["space:/"]
        # downgrade is not a re-notify
        clock.advance(60)
        assert tracker.update([_finding("space", "space:/", "warning")]) == []

    def test_gone_finding_forgotten(self):
        clock = SteppingClock()
        tracker = FindingTracker(now_fn=clock)
        tracker.update([_finding("space", "space:/")])
        tracker.update([])
        assert tracker.active_ids == set()

    def test_gone_then_back_notifies_again(self):
        clock = SteppingClock()
        tracker = FindingTracker(now_fn=clock)
        tracker.update([_finding("space", "space:/")])
        tracker.update([])
        out = tracker.update([_finding("space", "space:/")])
        assert [f.finding_id for f in out] == ["space:/"]

    def test_per_check_nag_intervals(self):
        clock = SteppingClock()
        tracker = FindingTracker(now_fn=clock)
        # retention re-nags weekly: silent after 24 h
        tracker.update([_finding("retention", "retention:home")])
        clock.advance(24 * 3600)
        assert tracker.update([_finding("retention", "retention:home")]) == []
        clock.advance(6 * 24 * 3600)
        assert [f.finding_id for f in
                tracker.update([_finding("retention", "retention:home")])] \
            == ["retention:home"]

    def test_custom_nag_intervals(self):
        clock = SteppingClock()
        tracker = FindingTracker(
            now_fn=clock, nag_intervals={"space": 10.0})
        tracker.update([_finding("space", "space:/")])
        clock.advance(9)
        assert tracker.update([_finding("space", "space:/")]) == []
        clock.advance(1)
        assert tracker.update([_finding("space", "space:/")])

    def test_unknown_check_uses_default(self):
        clock = SteppingClock()
        tracker = FindingTracker(now_fn=clock, nag_intervals={})
        tracker.update([_finding("mystery", "m:1")])
        clock.advance(DEFAULT_NAG_INTERVAL)
        assert tracker.update([_finding("mystery", "m:1")])

    def test_mixed_batch(self):
        clock = SteppingClock()
        tracker = FindingTracker(now_fn=clock)
        tracker.update([
            _finding("space", "space:/"),
            _finding("devices", "devices:/dev/sda2"),
        ])
        # one persists, one clears: only the new one is notified
        out = tracker.update([_finding("space", "space:/")])
        assert [f.finding_id for f in out] == []


class TestNagIntervals:
    def test_known_checks(self):
        assert set(NAG_INTERVALS) == {"space", "devices", "retention", "scrub"}

    def test_retention_longest(self):
        assert NAG_INTERVALS["retention"] == 7 * 24 * 3600
