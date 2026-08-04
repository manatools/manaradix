"""Unit tests for PolicyEngine."""

import os
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from manaradix.core.policy_engine import (
    PolicyEngine,
    PolicyEngineError,
    _escape_systemd_arg,
)
from manaradix.model.policy import (
    RetentionPolicy,
    SnapshotPolicy,
)

# Hostile subvolume names: every shell/systemd metacharacter that could
# break out of ExecStart=, plus control characters.
HOSTILE_SUBVOLS = [
    "/home; touch /tmp/pwned",
    '/home" ; touch /tmp/pwned',
    "/home' ; touch /tmp/pwned",
    "/home\\; touch /tmp/pwned",
    "/home$(touch /tmp/pwned)",
    "/home`touch /tmp/pwned`",
    "/home%n %i %%",
    "/home with spaces",
    "/home\ntouch /tmp/pwned",
    "/home\t; touch /tmp/pwned",
]


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

    def test_generate_service_unit_records_run(self) -> None:
        engine = PolicyEngine(cli_path="/usr/bin/manaradix")
        unit = engine._generate_service_unit("/", "daily")
        # ExecStopPost runs at the end of every fire — success or
        # failure — with $SERVICE_RESULT set, feeding the run records.
        assert (
            'ExecStopPost=/usr/bin/manaradix activity record'
            ' --subvolume "/" --schedule daily' in unit
        )

    def test_generate_boot_service_unit_records_run(self) -> None:
        engine = PolicyEngine(cli_path="/usr/bin/manaradix")
        unit = engine._generate_boot_service_unit("/")
        assert (
            'ExecStopPost=/usr/bin/manaradix activity record'
            ' --subvolume "/" --schedule boot' in unit
        )

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


class TestExecStartInjection:
    """SEC-01: hostile subvolume names must never break out of ExecStart=."""

    def test_escape_roundtrip(self) -> None:
        # Escaping is idempotent-safe and leaves no raw special character.
        for sv in HOSTILE_SUBVOLS:
            out = _escape_systemd_arg(sv)
            assert '"' not in out or '\\"' in out
            assert "\n" not in out and "\t" not in out
            # Every ';' must be escaped — an unescaped one is a command
            # separator in ExecStart.
            assert ";" not in out.replace("\\;", "")

    def test_service_unit_single_command(self) -> None:
        engine = PolicyEngine()
        for sv in HOSTILE_SUBVOLS:
            unit = engine._generate_service_unit(sv, "daily")
            # No unescaped ';' or '%' anywhere — a leaked one would be a
            # second command or a specifier expansion in ExecStart.
            stripped = unit.replace("\\;", "").replace("%%", "")
            assert ";" not in stripped, f"command separator leaked for {sv!r}"
            assert "%" not in stripped, f"specifier leaked for {sv!r}"
            # The argument is quoted and complete on one line.
            assert f'--subvolume "' in unit, f"arg not quoted for {sv!r}"
            for line in unit.splitlines():
                if "ExecStart" in line:
                    # Strip escaped quotes first so the first remaining "
                    # is the structural closer.
                    tail = line.split('--subvolume "', 1)[1].replace('\\"', "x")
                    value, rest = tail.split('"', 1)
                    # ExecStart is `create --type timed ...`; ExecStartPost
                    # is `prune ...` — both must end the same way.
                    assert rest in (
                        " --type timed --schedule daily",
                        " --schedule daily",
                    ), f"arg unterminated for {sv!r}"

    def test_boot_unit_single_command(self) -> None:
        engine = PolicyEngine()
        for sv in HOSTILE_SUBVOLS:
            unit = engine._generate_boot_service_unit(sv)
            assert ";" not in unit.replace("\\;", ""), f"command separator leaked for {sv!r}"
            assert "%" not in unit.replace("%%", ""), f"specifier leaked for {sv!r}"
            assert '--subvolume "' in unit

    def test_unit_filename_sanitized(self) -> None:
        engine = PolicyEngine()
        for sv in HOSTILE_SUBVOLS:
            name = engine._service_name(sv, "daily")
            assert name == f"manaradix-snapshot-{engine._sanitize_name(sv)}-daily.service"
            # systemd unit filename charset: alnum plus _ . : @ -
            assert re.fullmatch(r"[a-zA-Z0-9_.:@-]+", name), f"bad unit name {name!r}"

    def test_apply_policy_writes_escaped_unit(self, tmp_path) -> None:
        engine = PolicyEngine(systemd_dir=str(tmp_path))
        policy = SnapshotPolicy(subvolume="/home; touch /tmp/pwned", schedule_daily=True)
        with patch.object(engine, "_systemctl") as sc:
            engine.apply_policy(policy)
        (unit,) = [p for p in tmp_path.iterdir() if p.suffix == ".service"]
        content = unit.read_text()
        assert ";" not in content.replace("\\;", "")
        assert '--subvolume "/home\\; touch /tmp/pwned"' in content
        # The ExecStopPost record line must be escaped exactly like
        # ExecStart — a hostile subvolume cannot inject into it either.
        assert (
            'activity record --subvolume "/home\\; touch /tmp/pwned"' in content
        )


class TestUnitStatus:
    """unit_status(): systemctl state → derived per-activity status.

    ``_systemctl_show`` output is one value per line in property order
    (with --value); time properties come back as unix epochs or "n/a".
    """

    @staticmethod
    def _proc(*lines, rc=0) -> MagicMock:
        return MagicMock(
            returncode=rc, stdout="\n".join(lines) + "\n", stderr=""
        )

    @staticmethod
    def _install(engine, subvolume, schedule) -> None:
        Path(os.path.join(
            engine._systemd_dir, engine._service_name(subvolume, schedule)
        )).write_text("")
        if schedule != "boot":
            Path(os.path.join(
                engine._systemd_dir, engine._timer_name(subvolume, schedule)
            )).write_text("")

    @patch("subprocess.run")
    def test_unknown_when_units_missing(self, mock_run: MagicMock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = PolicyEngine(systemd_dir=tmpdir)
            status = engine.unit_status("/", "daily")
        assert status["state"] == "unknown"
        mock_run.assert_not_called()

    @patch("subprocess.run")
    def test_ok(self, mock_run: MagicMock) -> None:
        now = int(time.time())
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = PolicyEngine(systemd_dir=tmpdir)
            self._install(engine, "/", "daily")
            mock_run.side_effect = [
                # service show: ActiveState, UnitFileState
                self._proc("inactive", "enabled"),
                # timer show: ActiveState, UnitFileState, last, next
                self._proc("active", "enabled", str(now - 3600), str(now + 3600)),
            ]
            status = engine.unit_status("/", "daily")
        assert status["state"] == "ok"
        assert status["last_trigger"] == str(now - 3600)
        assert status["next_trigger"] == str(now + 3600)

    @patch("subprocess.run")
    def test_failed_service(self, mock_run: MagicMock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = PolicyEngine(systemd_dir=tmpdir)
            self._install(engine, "/", "daily")
            mock_run.side_effect = [self._proc("failed", "enabled")]
            status = engine.unit_status("/", "daily")
        assert status["state"] == "failed"
        # Failure short-circuits: no timer query needed.
        assert mock_run.call_count == 1

    @patch("subprocess.run")
    def test_inactive_timer(self, mock_run: MagicMock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = PolicyEngine(systemd_dir=tmpdir)
            self._install(engine, "/", "daily")
            mock_run.side_effect = [
                self._proc("inactive", "enabled"),
                self._proc("inactive", "enabled", "n/a", "n/a"),
            ]
            status = engine.unit_status("/", "daily")
        assert status["state"] == "inactive"

    @patch("subprocess.run")
    def test_missed_when_last_fire_stale(self, mock_run: MagicMock) -> None:
        # Daily cadence: stale threshold is 2.5 days.
        old = int(time.time()) - int(3 * 86400)
        now = int(time.time())
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = PolicyEngine(systemd_dir=tmpdir)
            self._install(engine, "/", "daily")
            mock_run.side_effect = [
                self._proc("inactive", "enabled"),
                self._proc("active", "enabled", str(old), str(now + 3600)),
            ]
            status = engine.unit_status("/", "daily")
        assert status["state"] == "missed"

    @patch("subprocess.run")
    def test_recent_fire_not_missed(self, mock_run: MagicMock) -> None:
        now = int(time.time())
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = PolicyEngine(systemd_dir=tmpdir)
            self._install(engine, "/", "daily")
            mock_run.side_effect = [
                self._proc("inactive", "enabled"),
                self._proc("active", "enabled", str(now - 2 * 86400), str(now + 3600)),
            ]
            status = engine.unit_status("/", "daily")
        assert status["state"] == "ok"

    @patch("subprocess.run")
    def test_boot_tier_enabled_is_ok(self, mock_run: MagicMock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = PolicyEngine(systemd_dir=tmpdir)
            self._install(engine, "/", "boot")
            mock_run.side_effect = [self._proc("inactive", "enabled")]
            status = engine.unit_status("/", "boot")
        assert status["state"] == "ok"

    @patch("subprocess.run")
    def test_boot_tier_failed(self, mock_run: MagicMock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = PolicyEngine(systemd_dir=tmpdir)
            self._install(engine, "/", "boot")
            mock_run.side_effect = [self._proc("failed", "enabled")]
            status = engine.unit_status("/", "boot")
        assert status["state"] == "failed"

    @patch("subprocess.run")
    def test_boot_tier_not_enabled(self, mock_run: MagicMock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = PolicyEngine(systemd_dir=tmpdir)
            self._install(engine, "/", "boot")
            mock_run.side_effect = [self._proc("inactive", "disabled")]
            status = engine.unit_status("/", "boot")
        assert status["state"] == "inactive"

    @patch("subprocess.run")
    def test_systemctl_unreachable_is_unknown(self, mock_run: MagicMock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = PolicyEngine(systemd_dir=tmpdir)
            self._install(engine, "/", "daily")
            mock_run.side_effect = [
                self._proc("", rc=1),
                self._proc("", rc=1),
            ]
            status = engine.unit_status("/", "daily")
        assert status["state"] == "unknown"

    def test_parse_epoch(self) -> None:
        engine = PolicyEngine()
        now = int(time.time())
        assert engine._parse_epoch(str(now)) == datetime.fromtimestamp(now)
        assert engine._parse_epoch(None) is None
        assert engine._parse_epoch("n/a") is None
        assert engine._parse_epoch("garbage") is None


class TestUnitState:
    def test_active(self) -> None:
        engine = PolicyEngine()
        with patch("subprocess.run", return_value=TestUnitStatus._proc("active")):
            assert engine.unit_state("manaradix-snapshot-root-daily.timer") == "active"

    def test_failed(self) -> None:
        engine = PolicyEngine()
        with patch("subprocess.run", return_value=TestUnitStatus._proc("failed")):
            assert engine.unit_state("manaradix-snapshot-root-daily.service") == "failed"

    def test_unknown_on_query_failure(self) -> None:
        engine = PolicyEngine()
        with patch("subprocess.run", return_value=TestUnitStatus._proc("", rc=1)):
            assert engine.unit_state("nonexistent.service") == "unknown"
