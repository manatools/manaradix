"""Unit tests for activity run records and status aggregation."""

import json
import os
import stat
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from manaradix.core.activity_status import (
    ActivityStatus,
    MAX_RECORDS,
    _result_from_service_env,
    append_run_record,
    load_run_records,
    record_activity_run,
)
from manaradix.model.policy import RetentionPolicy, SnapshotPolicy
from manaradix.model.snapshot import Snapshot, SnapshotType


# ---------------------------------------------------------------------------
# Run record file I/O
# ---------------------------------------------------------------------------


class TestRunRecords:
    def test_missing_file_loads_empty(self, tmp_path) -> None:
        assert load_run_records(str(tmp_path / "nope.jsonl")) == []

    def test_append_then_load_newest_first(self, tmp_path) -> None:
        path = str(tmp_path / "runs.jsonl")
        append_run_record(
            {"subvolume": "/", "schedule": "daily", "result": "success",
             "ts": "2026-08-04T10:00:00"},
            path=path,
        )
        append_run_record(
            {"subvolume": "/", "schedule": "daily", "result": "failed",
             "ts": "2026-08-04T11:00:00"},
            path=path,
        )
        records = load_run_records(path)
        assert [r["result"] for r in records] == ["failed", "success"]

    def test_ts_defaults_to_now(self, tmp_path) -> None:
        path = str(tmp_path / "runs.jsonl")
        record = append_run_record(
            {"subvolume": "/", "schedule": "daily", "result": "success"},
            path=path,
        )
        assert "ts" in record

    def test_trims_to_max_records(self, tmp_path) -> None:
        path = str(tmp_path / "runs.jsonl")
        for i in range(MAX_RECORDS + 5):
            append_run_record(
                {"subvolume": "/", "schedule": "hourly", "result": "success",
                 "ts": f"2026-01-01T{i:06.0f}"},
                path=path,
            )
        records = load_run_records(path)
        assert len(records) == MAX_RECORDS
        # The 5 oldest were dropped.
        assert records[-1]["ts"] == "2026-01-01T000005"

    def test_corrupt_lines_skipped(self, tmp_path) -> None:
        path = tmp_path / "runs.jsonl"
        path.write_text('{"ok": true}\nnot json\n{"ok": false}\n')
        records = load_run_records(str(path))
        assert len(records) == 2

    def test_file_is_world_readable(self, tmp_path) -> None:
        path = str(tmp_path / "runs.jsonl")
        append_run_record(
            {"subvolume": "/", "schedule": "daily", "result": "success"},
            path=path,
        )
        # The unprivileged GUI reads the records; only root can write.
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o644


class TestResultFromEnv:
    def test_success(self) -> None:
        assert _result_from_service_env({"SERVICE_RESULT": "success"}) == "success"

    def test_clean_counts_as_success(self) -> None:
        assert _result_from_service_env({"SERVICE_RESULT": "clean"}) == "success"

    def test_cancelled(self) -> None:
        assert _result_from_service_env({"SERVICE_RESULT": "canceled"}) == "cancelled"

    def test_exit_code_is_failed(self) -> None:
        assert _result_from_service_env({"SERVICE_RESULT": "exit-code"}) == "failed"

    def test_missing_env_is_unknown(self) -> None:
        assert _result_from_service_env({}) == "unknown"


class TestRecordActivityRun:
    def test_result_from_env(self, tmp_path) -> None:
        path = str(tmp_path / "runs.jsonl")
        record = record_activity_run(
            "/", "daily",
            env={"SERVICE_RESULT": "exit-code", "EXIT_STATUS": "1"},
            path=path,
        )
        assert record["result"] == "failed"
        assert record["exit_status"] == "1"

    def test_explicit_result_overrides_env(self, tmp_path) -> None:
        path = str(tmp_path / "runs.jsonl")
        record = record_activity_run(
            "/", "daily",
            result="success",
            env={"SERVICE_RESULT": "exit-code"},
            path=path,
        )
        assert record["result"] == "success"

    def test_invalid_result_raises(self, tmp_path) -> None:
        with pytest.raises(ValueError, match="invalid result"):
            record_activity_run("/", "daily", result="bogus",
                                path=str(tmp_path / "runs.jsonl"))

    def test_write_failure_raises_oserror(self) -> None:
        # /proc is not writable — a manual unprivileged call must fail
        # with a plain OSError, not a traceback.
        with pytest.raises(OSError):
            record_activity_run("/", "daily", path="/proc/definitely-not-writable")


# ---------------------------------------------------------------------------
# ActivityStatus aggregation
# ---------------------------------------------------------------------------


def _policy(subvolume="/", *schedules: str) -> SnapshotPolicy:
    return SnapshotPolicy(
        subvolume=subvolume,
        schedule_hourly="hourly" in schedules,
        schedule_daily="daily" in schedules,
        schedule_weekly="weekly" in schedules,
        schedule_monthly="monthly" in schedules,
        schedule_boot="boot" in schedules,
        enabled=True,
        retention=RetentionPolicy(),
    )


class TestActivityStatus:
    def _status(self, tmp_path, policies=None, engine_rows=None, snaps=None):
        engine = MagicMock()
        engine.unit_status.side_effect = engine_rows or []
        config = MagicMock()
        config.list_policies.return_value = policies or []
        snapshots = MagicMock()
        snapshots.list_all.return_value = snaps or []
        return ActivityStatus(
            engine, snapshots, config,
            records_path=str(tmp_path / "runs.jsonl"),
        )

    def test_statuses_aggregates_policies(self, tmp_path) -> None:
        policies = [_policy("/", "daily", "weekly"), _policy("/home", "daily")]
        engine_rows = [
            {"subvolume": "/", "schedule": "daily", "state": "ok",
             "last_trigger": "1", "next_trigger": "2"},
            {"subvolume": "/", "schedule": "weekly", "state": "missed",
             "last_trigger": "1", "next_trigger": "2"},
            {"subvolume": "/home", "schedule": "daily", "state": "ok",
             "last_trigger": "1", "next_trigger": "2"},
        ]
        status = self._status(tmp_path, policies, engine_rows)
        rows = status.statuses()
        assert [(r["subvolume"], r["schedule"]) for r in rows] == [
            ("/", "daily"), ("/", "weekly"), ("/home", "daily"),
        ]
        assert rows[1]["state"] == "missed"

    def test_statuses_skips_disabled_policies(self, tmp_path) -> None:
        policy = _policy("/", "daily")
        policy.enabled = False
        status = self._status(tmp_path, [policy])
        assert status.statuses() == []

    def test_statuses_tolerates_engine_failure(self, tmp_path) -> None:
        engine = MagicMock()
        engine.unit_status.side_effect = RuntimeError("systemd unreachable")
        config = MagicMock()
        config.list_policies.return_value = [_policy("/", "daily")]
        status = ActivityStatus(
            engine, MagicMock(), config,
            records_path=str(tmp_path / "runs.jsonl"),
        )
        assert status.statuses() == []  # must not raise

    def test_last_run_prefers_record(self, tmp_path) -> None:
        path = str(tmp_path / "runs.jsonl")
        append_run_record(
            {"subvolume": "/", "schedule": "daily", "result": "failed",
             "ts": "2026-08-04T10:00:00"},
            path=path,
        )
        snap = Snapshot(
            id="x", subvolume="/", path="/.snapshots/x/root",
            date=datetime(2026, 8, 3, 9, 0, 0),
            type=SnapshotType.TIMED, schedule="daily",
        )
        status = self._status(tmp_path, snaps=[snap])
        last = status.last_run("/", "daily")
        assert last["source"] == "record"
        assert last["result"] == "failed"

    def test_last_run_falls_back_to_snapshot(self, tmp_path) -> None:
        # A run from before records existed shows up via the sidecar tag.
        snap = Snapshot(
            id="x", subvolume="/", path="/.snapshots/x/root",
            date=datetime(2026, 8, 3, 9, 0, 0),
            type=SnapshotType.TIMED, schedule="daily",
        )
        status = self._status(tmp_path, snaps=[snap])
        last = status.last_run("/", "daily")
        assert last["source"] == "snapshot"
        assert last["result"] == "success"

    def test_last_run_none_when_no_evidence(self, tmp_path) -> None:
        status = self._status(tmp_path)
        assert status.last_run("/", "daily") is None

    def test_run_records_filters(self, tmp_path) -> None:
        path = str(tmp_path / "runs.jsonl")
        for subvol, sched, ts in (
            ("/", "daily", "2026-08-04T10:00:00"),
            ("/", "weekly", "2026-08-04T11:00:00"),
            ("/home", "daily", "2026-08-04T12:00:00"),
        ):
            append_run_record(
                {"subvolume": subvol, "schedule": sched, "result": "success",
                 "ts": ts},
                path=path,
            )
        status = self._status(tmp_path)
        assert [r["schedule"] for r in status.run_records(subvolume="/")] == [
            "weekly", "daily",
        ]
        assert len(status.run_records(subvolume="/home")) == 1
        assert len(status.run_records(subvolume="/", limit=1)) == 1
