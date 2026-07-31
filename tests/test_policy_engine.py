"""Unit tests for PolicyEngine."""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from manaradix.core.policy_engine import (
    PolicyEngine,
    PolicyEngineError,
)
from manaradix.model.policy import (
    RetentionPolicy,
    SnapshotPolicy,
)


class TestSanitizeName:
    def test_root(self) -> None:
        engine = PolicyEngine()
        assert engine._sanitize_name("/") == "root"

    def test_home(self) -> None:
        engine = PolicyEngine()
        assert engine._sanitize_name("/home") == "home"

    def test_deep_path(self) -> None:
        engine = PolicyEngine()
        assert engine._sanitize_name("/var/lib/machines") == "var-lib-machines"

    def test_special_chars(self) -> None:
        engine = PolicyEngine()
        name = engine._sanitize_name("/my data/sub!")
        assert " " not in name
        assert "!" not in name


class TestUnitNames:
    def test_service_name(self) -> None:
        engine = PolicyEngine()
        name = engine._service_name("/", "daily")
        assert name == "manaradix-snapshot-root-daily.service"

    def test_timer_name(self) -> None:
        engine = PolicyEngine()
        name = engine._timer_name("/home", "hourly")
        assert name == "manaradix-snapshot-home-hourly.timer"


class TestGenerateUnits:
    def test_generate_service_unit(self) -> None:
        engine = PolicyEngine(cli_path="/usr/bin/manaradix")
        unit = engine._generate_service_unit("/", "daily")
        assert "[Unit]" in unit
        assert "Description=Manaradix daily snapshot of /" in unit
        assert "ExecStart=/usr/bin/manaradix" in unit
        assert "Type=oneshot" in unit

    def test_generate_timer_unit(self) -> None:
        engine = PolicyEngine()
        unit = engine._generate_timer_unit("/", "daily")
        assert "[Unit]" in unit
        assert "OnCalendar=*-*-* 00:00:00" in unit
        assert "Persistent=true" in unit
        assert "WantedBy=timers.target" in unit

    def test_generate_timer_unit_weekly_calendar(self) -> None:
        engine = PolicyEngine()
        unit = engine._generate_timer_unit("/", "weekly")
        assert "OnCalendar=Mon *-*-* 00:00:00" in unit


class TestApplyPolicy:
    def test_apply_invalid_policy_raises(self) -> None:
        engine = PolicyEngine()
        policy = SnapshotPolicy(subvolume="", schedule_daily=False)
        with pytest.raises(PolicyEngineError, match="Invalid policy"):
            engine.apply_policy(policy)

    def test_apply_valid_dry_run(self) -> None:
        engine = PolicyEngine()
        policy = SnapshotPolicy(subvolume="/", schedule_daily=True)
        written = engine.apply_policy(policy, dry_run=True)
        # Should return paths for all 5 schedules (4 timer + 1 boot)
        assert len(written) == 5
        # Timer schedules end with .timer; boot service ends with .service
        timer_paths = [p for p in written if p.endswith(".timer")]
        assert len(timer_paths) == 4

    @patch("subprocess.run")
    def test_apply_active_policy_writes_files(self, mock_run: MagicMock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = PolicyEngine(systemd_dir=tmpdir)
            policy = SnapshotPolicy(subvolume="/", schedule_daily=True)
            written = engine.apply_policy(policy)
            # Should write service + timer for the active schedule
            service_path = os.path.join(
                tmpdir, "manaradix-snapshot-root-daily.service"
            )
            timer_path = os.path.join(tmpdir, "manaradix-snapshot-root-daily.timer")
            assert service_path in written
            assert timer_path in written
            assert os.path.isfile(service_path)
            assert os.path.isfile(timer_path)

    @patch("subprocess.run")
    def test_apply_disabled_policy_removes_files(self, mock_run: MagicMock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = PolicyEngine(systemd_dir=tmpdir)
            # Write files first
            policy = SnapshotPolicy(subvolume="/", schedule_daily=True)
            engine.apply_policy(policy)

            # Now disable
            policy.enabled = False
            written = engine.apply_policy(policy)
            # Should contain removal markers (prefixed with -)
            for p in written:
                if p.startswith("-"):
                    assert not os.path.exists(p[1:])

    @patch("subprocess.run")
    def test_apply_boot_schedule_writes_service_only(self, mock_run: MagicMock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = PolicyEngine(systemd_dir=tmpdir)
            policy = SnapshotPolicy(subvolume="/", schedule_daily=False, schedule_boot=True)
            written = engine.apply_policy(policy)

            boot_service = os.path.join(tmpdir, "manaradix-snapshot-root-boot.service")
            assert boot_service in written
            assert os.path.isfile(boot_service)

            content = Path(boot_service).read_text()
            assert "WantedBy=multi-user.target" in content
            assert "--schedule boot" in content
            # No timer for boot
            boot_timer = os.path.join(tmpdir, "manaradix-snapshot-root-boot.timer")
            assert not os.path.exists(boot_timer)


class TestRemovePolicy:
    def test_remove_dry_run(self) -> None:
        engine = PolicyEngine()
        removed = engine.remove_policy("/", dry_run=True)
        assert len(removed) == 5  # hourly/daily/weekly/monthly + boot

    @patch("subprocess.run")
    def test_remove_policy_cleans_up(self, mock_run: MagicMock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = PolicyEngine(systemd_dir=tmpdir)
            # Create a dummy timer file
            timer_path = os.path.join(tmpdir, "manaradix-snapshot-root-daily.timer")
            Path(timer_path).write_text("[Timer]")
            service_path = os.path.join(
                tmpdir, "manaradix-snapshot-root-daily.service"
            )
            Path(service_path).write_text("[Service]")

            removed = engine.remove_policy("/")
            assert timer_path in removed
            assert not os.path.exists(timer_path)
            assert not os.path.exists(service_path)


class TestListGeneratedUnits:
    def test_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = PolicyEngine(systemd_dir=tmpdir)
            assert engine.list_generated_units() == []

    def test_lists_timers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = PolicyEngine(systemd_dir=tmpdir)
            Path(os.path.join(tmpdir, "manaradix-snapshot-root-daily.timer")).write_text("")
            Path(os.path.join(tmpdir, "manaradix-snapshot-home-weekly.timer")).write_text("")
            Path(os.path.join(tmpdir, "other.service")).write_text("")

            units = engine.list_generated_units()
            assert len(units) == 2
            assert "manaradix-snapshot-root-daily.timer" in units
            assert "manaradix-snapshot-home-weekly.timer" in units
            assert "other.service" not in units

    def test_lists_boot_services(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = PolicyEngine(systemd_dir=tmpdir)
            Path(os.path.join(tmpdir, "manaradix-snapshot-root-boot.service")).write_text("")
            Path(os.path.join(tmpdir, "manaradix-snapshot-root-daily.timer")).write_text("")
            Path(os.path.join(tmpdir, "unrelated.service")).write_text("")

            units = engine.list_generated_units()
            assert len(units) == 2
            assert "manaradix-snapshot-root-boot.service" in units
            assert "manaradix-snapshot-root-daily.timer" in units
            assert "unrelated.service" not in units


class TestRetentionPruning:
    def test_get_prune_commands_valid(self) -> None:
        engine = PolicyEngine()
        rp = RetentionPolicy(keep_daily=7)
        result = engine.get_prune_commands("/", rp)
        assert result == []

    def test_get_prune_commands_invalid_retention(self) -> None:
        engine = PolicyEngine()
        rp = RetentionPolicy(keep_daily=-1)
        with pytest.raises(PolicyEngineError, match="Invalid retention"):
            engine.get_prune_commands("/", rp)
