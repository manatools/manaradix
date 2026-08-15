"""Tests for the pure health/retention-suggestion engine."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from manaradix.core import health
from manaradix.model.policy import RetentionPolicy, SnapshotPolicy


def make_policy(subvol="@", **retention_kwargs) -> SnapshotPolicy:
    """A policy with per-tier retention overrides."""
    return SnapshotPolicy(
        subvolume=subvol,
        retention=RetentionPolicy(**retention_kwargs),
    )


def snap(sid: str, tier: str = "hourly", *, subvol: str = "@",
         age_days: int = 1) -> dict:
    """A daemon-shaped snapshot dict."""
    stype = tier if tier in ("boot", "pre-upgrade") else "timed"
    return {
        "id": sid,
        "subvolume": subvol,
        "date": (datetime.now() - timedelta(days=age_days)).isoformat(),
        "type": stype,
        "schedule": tier if tier not in ("boot", "pre-upgrade") else "",
    }


# ---------------------------------------------------------------------------
# classify_space / space_findings
# ---------------------------------------------------------------------------


class TestClassifySpace:
    def test_ok(self) -> None:
        assert health.classify_space(50.0) is None

    def test_warning(self) -> None:
        assert health.classify_space(85.0) == "warning"

    def test_critical(self) -> None:
        assert health.classify_space(95.0) == "critical"

    def test_custom_thresholds(self) -> None:
        assert health.classify_space(90.0, warn=90, critical=99) == "warning"
        assert health.classify_space(99.9, warn=90, critical=99) == "critical"


class TestSpaceFindings:
    def test_none_below_threshold(self) -> None:
        findings = health.space_findings(
            [{"mount": "/", "percent_used": 50.0,
              "total_bytes": 1000, "free_bytes": 500}]
        )
        assert findings == []

    def test_warning_finding(self) -> None:
        findings = health.space_findings(
            [{"mount": "/", "percent_used": 90.0,
              "total_bytes": 1 << 30, "free_bytes": int(0.1 * (1 << 30))}]
        )
        assert len(findings) == 1
        f = findings[0]
        assert f.check == "space"
        assert f.finding_id == "space:/"
        assert f.severity == "warning"
        assert "Disk space low on /" in f.summary

    def test_critical_finding(self) -> None:
        findings = health.space_findings(
            [{"mount": "/home", "percent_used": 97.0,
              "total_bytes": 1 << 30, "free_bytes": int(0.03 * (1 << 30))}]
        )
        assert findings[0].severity == "critical"
        assert findings[0].finding_id == "space:/home"

    def test_multiple_mounts(self) -> None:
        findings = health.space_findings(
            [
                {"mount": "/", "percent_used": 50.0,
                 "total_bytes": 1000, "free_bytes": 500},
                {"mount": "/data", "percent_used": 96.0,
                 "total_bytes": 1000, "free_bytes": 40},
            ]
        )
        assert [f.finding_id for f in findings] == ["space:/data"]

    def test_format_bytes(self) -> None:
        assert health.format_bytes(512) == "512 B"
        assert health.format_bytes(2048) == "2.0 KiB"
        assert health.format_bytes(3 * 1024 * 1024) == "3.0 MiB"


# ---------------------------------------------------------------------------
# device_findings
# ---------------------------------------------------------------------------


class TestDeviceFindings:
    def _device(self, **overrides: object) -> dict:
        device: dict = {
            "device": "/dev/sda", "write_io_errs": 0, "read_io_errs": 0,
            "flush_io_errs": 0, "corruption_errs": 0, "generation_errs": 0,
            "smart_available": False,
        }
        device.update(overrides)
        return device

    def test_clean_device_silent(self) -> None:
        assert health.device_findings([self._device()]) == []

    def test_clean_smart_device_silent(self) -> None:
        assert health.device_findings(
            [self._device(smart_available=True, smart_failing=False,
                          smart_temperature=35.0)]
        ) == []

    def test_io_errors_warning(self) -> None:
        findings = health.device_findings(
            [self._device(write_io_errs=3, read_io_errs=1)]
        )
        assert len(findings) == 1
        f = findings[0]
        assert f.severity == "warning"
        assert f.check == "devices"
        assert "IO errors on /dev/sda" in f.summary

    def test_integrity_errors_critical(self) -> None:
        findings = health.device_findings(
            [self._device(corruption_errs=1)]
        )
        assert len(findings) == 1
        assert findings[0].severity == "critical"
        assert "integrity" in findings[0].finding_id

    def test_smart_failing_critical(self) -> None:
        findings = health.device_findings(
            [self._device(device="/dev/sdb", smart_available=True,
                          smart_failing=True)]
        )
        assert len(findings) == 1
        assert findings[0].severity == "critical"
        assert "SMART" in findings[0].summary

    def test_hot_drive_warning(self) -> None:
        findings = health.device_findings(
            [self._device(smart_available=True, smart_failing=False,
                          smart_temperature=health.SMART_HOT_TEMP_C + 5)]
        )
        assert len(findings) == 1
        assert findings[0].severity == "warning"
        assert "hot" in findings[0].finding_id

    def test_warm_but_not_hot_is_silent(self) -> None:
        assert health.device_findings(
            [self._device(smart_available=True, smart_failing=False,
                          smart_temperature=45.0)]
        ) == []

    def test_missing_smart_section_is_silent(self) -> None:
        assert health.device_findings(
            [{"device": "/dev/sda", "write_io_errs": 0, "read_io_errs": 0,
              "flush_io_errs": 0, "corruption_errs": 0, "generation_errs": 0}]
        ) == []


# ---------------------------------------------------------------------------
# scrub_findings
# ---------------------------------------------------------------------------


class TestScrubFindings:
    def test_clean_scrub_silent(self) -> None:
        assert health.scrub_findings(
            {"data_extents_scrubbed": 100, "errors": 0, "running": False,
             "last_started": datetime.now().isoformat()}
        ) == []

    def test_errors_warning(self) -> None:
        findings = health.scrub_findings(
            {"errors": 2, "running": False, "last_started": None}
        )
        assert len(findings) == 1
        assert findings[0].severity == "warning"
        assert "Scrub found errors" in findings[0].summary

    def test_stuck_scrub(self) -> None:
        started = datetime.now() - timedelta(hours=health.SERVER_STUCK_HOURS + 2)
        findings = health.scrub_findings(
            {"errors": 0, "running": True, "last_started": started.isoformat()}
        )
        assert any(f.finding_id == "scrub:stuck" for f in findings)

    def test_running_scrub_not_stuck_is_silent(self) -> None:
        assert health.scrub_findings(
            {"errors": 0, "running": True,
             "last_started": datetime.now().isoformat()}
        ) == []

    def test_server_never_scrubbed(self) -> None:
        findings = health.scrub_findings(
            {"errors": 0, "running": False, "last_started": None},
            profile="server",
        )
        assert any(f.finding_id == "scrub:stale" for f in findings)

    def test_server_stale_scrub(self) -> None:
        old = datetime.now() - timedelta(days=health.SERVER_MAX_AGE_DAYS + 1)
        findings = health.scrub_findings(
            {"errors": 0, "running": False, "last_started": old.isoformat()},
            profile="server",
        )
        assert any(f.finding_id == "scrub:stale" for f in findings)

    def test_server_fresh_scrub_silent(self) -> None:
        assert health.scrub_findings(
            {"errors": 0, "running": False,
             "last_started": datetime.now().isoformat()},
            profile="server",
        ) == []

    def test_desktop_never_asks_for_scrub(self) -> None:
        assert health.scrub_findings(
            {"errors": 0, "running": False, "last_started": None},
            profile="desktop",
        ) == []


# ---------------------------------------------------------------------------
# Retention suggestions
# ---------------------------------------------------------------------------


class TestRetentionSuggestions:
    def test_within_caps_silent(self) -> None:
        snaps = [snap(f"h{i}") for i in range(10)]
        policy = make_policy(keep_hourly=10)
        assert health.compute_retention_suggestions(snaps, policy) == []

    def test_desktop_over_hourly_cap(self) -> None:
        snaps = [snap(f"h{i}") for i in range(12)]
        policy = make_policy(keep_hourly=10)
        findings = health.compute_retention_suggestions(snaps, policy)
        assert len(findings) == 1
        f = findings[0]
        assert f.check == "retention"
        assert f.finding_id == "retention:@:hourly"
        assert f.severity == "warning"
        assert "12 snapshots kept" in f.details

    def test_boot_tier_never_below_cap(self) -> None:
        snaps = [snap(f"b{i}", "boot") for i in range(2)]
        policy = make_policy(keep_boot=5)
        assert health.compute_retention_suggestions(snaps, policy) == []

    def test_boot_tier_above_cap(self) -> None:
        snaps = [snap(f"b{i}", "boot") for i in range(6)]
        policy = make_policy(keep_boot=5)
        findings = health.compute_retention_suggestions(snaps, policy)
        assert len(findings) == 1
        assert findings[0].finding_id == "retention:@:boot"

    def test_manual_snapshots_never_suggested(self) -> None:
        manual = snap("m1", "manual")
        manual["schedule"] = ""
        manual["type"] = "manual"
        policy = make_policy(keep_hourly=1)
        assert health.compute_retention_suggestions([manual], policy) == []

    def test_other_subvolumes_ignored(self) -> None:
        snaps = [snap(f"h{i}", subvol="@home") for i in range(20)]
        policy = make_policy(keep_hourly=10)
        assert health.compute_retention_suggestions(snaps, policy) == []

    def test_kiosk_total_cap(self) -> None:
        snaps = [snap(f"h{i}") for i in range(5)]
        policy = make_policy(keep_hourly=10, keep_daily=7)
        findings = health.compute_retention_suggestions(
            snaps, policy, profile="kiosk"
        )
        assert len(findings) == 1
        f = findings[0]
        assert "total" in f.finding_id
        assert "2" in f.details  # KIOSK_MAX_TOTAL

    def test_kiosk_protects_boot_and_pre_upgrade(self) -> None:
        snaps = (
            [snap(f"h{i}") for i in range(5)]
            + [snap("boot1", "boot"), snap("pre1", "pre-upgrade")]
        )
        policy = make_policy(keep_hourly=10, keep_daily=7,
                             keep_boot=5, keep_pre_upgrade=3)
        findings = health.compute_retention_suggestions(
            snaps, policy, profile="kiosk"
        )
        # 5 non-protected > 2 → exactly the total finding, never boot/pre.
        assert [f.finding_id for f in findings] == [
            "retention:@:total"
        ]

    def test_kiosk_low_space_pause_suggestion(self) -> None:
        snaps = []
        policy = make_policy()
        findings = health.compute_retention_suggestions(
            snaps, policy, profile="kiosk", free_pct=12.0
        )
        assert any("pause" in f.finding_id for f in findings)

    def test_kiosk_ok_free_space_silent(self) -> None:
        snaps = []
        policy = make_policy()
        assert health.compute_retention_suggestions(
            snaps, policy, profile="kiosk", free_pct=40.0
        ) == []

    def test_server_age_based(self) -> None:
        snaps = [snap(f"d{i}", "daily", age_days=40) for i in range(3)]
        policy = make_policy()
        findings = health.compute_retention_suggestions(
            snaps, policy, profile="server"
        )
        assert len(findings) == 1
        assert findings[0].finding_id == "retention:@:daily"
        assert "older than 30 days" in findings[0].summary

    def test_server_recent_snapshots_silent(self) -> None:
        snaps = [snap("d1", "daily", age_days=2) for i in range(20)]
        policy = make_policy(keep_daily=7)
        assert health.compute_retention_suggestions(
            snaps, policy, profile="server"
        ) == []

    def test_server_never_suggests_boot(self) -> None:
        snaps = [snap("b1", "boot", age_days=400)]
        policy = make_policy(keep_boot=5)
        assert health.compute_retention_suggestions(
            snaps, policy, profile="server"
        ) == []

    def test_server_boot_above_cap_still_suggested(self) -> None:
        snaps = [snap(f"b{i}", "boot", age_days=400) for i in range(6)]
        policy = make_policy(keep_boot=5)
        findings = health.compute_retention_suggestions(
            snaps, policy, profile="server"
        )
        assert [f.finding_id for f in findings] == ["retention:@:boot"]


class TestInferProfile:
    def test_desktop_defaults(self) -> None:
        assert health.infer_profile(make_policy()) == "desktop"

    def test_kiosk_tiny_caps(self) -> None:
        policy = make_policy(keep_hourly=2, keep_daily=1, keep_weekly=1,
                             keep_monthly=0, keep_boot=1, keep_pre_upgrade=1)
        assert health.infer_profile(policy) == "kiosk"

    def test_server_lean(self) -> None:
        policy = make_policy(keep_hourly=4, keep_daily=3, keep_weekly=1,
                             keep_monthly=1)
        assert health.infer_profile(policy) == "server"


# ---------------------------------------------------------------------------
# Quiet hours
# ---------------------------------------------------------------------------


class TestQuietHours:
    def test_parse_valid(self) -> None:
        assert health.parse_quiet_hours("22:30-07:00") is not None

    def test_parse_garbage(self) -> None:
        assert health.parse_quiet_hours("not a spec") is None
        assert health.parse_quiet_hours("") is None
        assert health.parse_quiet_hours(None) is None

    def test_inside_window(self) -> None:
        assert health.in_quiet_hours(
            "22:00-07:00", now=datetime(2026, 8, 13, 23, 30)
        )

    def test_outside_window(self) -> None:
        assert not health.in_quiet_hours(
            "22:00-07:00", now=datetime(2026, 8, 13, 12, 0)
        )

    def test_non_midnight_window(self) -> None:
        assert health.in_quiet_hours(
            "13:00-14:00", now=datetime(2026, 8, 13, 13, 30)
        )
        assert not health.in_quiet_hours(
            "13:00-14:00", now=datetime(2026, 8, 13, 14, 30)
        )

    def test_missing_spec_false(self) -> None:
        assert not health.in_quiet_hours(None)


class TestFindingValidation:
    def test_bad_severity_rejected(self) -> None:
        with pytest.raises(ValueError):
            health.HealthFinding("x", "y", "catastrophic", "s")
