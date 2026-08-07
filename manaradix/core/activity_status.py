"""Activity run records and status aggregation.

Automated snapshot schedules (see docs/activities.md) write one JSON run
record at the end of every systemd unit fire; this module reads those
records and merges them with live systemd state (``PolicyEngine``) and
snapshot sidecars into the per-activity status the GUI and CLI surface.

Every read is unprivileged and every failure degrades gracefully — a
missing records file, an unreadable config, or an unreachable systemd
must never crash a page render or a CLI command.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

#: Run records live here.  Root-owned (only the units and root can write),
#: but 0644 so the unprivileged GUI can read last-run state — the records
#: hold no secrets, only subvolume/schedule/timestamps/results.
DEFAULT_RECORDS_PATH = "/var/lib/manaradix/activity-runs.jsonl"

#: Trim the file to this many newest records after every append.
MAX_RECORDS = 1000

_RESULTS = ("success", "failed", "cancelled", "unknown")


def _result_from_service_env(env: dict) -> str:
    """Map systemd's ``$SERVICE_RESULT`` to a run-record result.

    ``ExecStopPost=`` commands get ``$SERVICE_RESULT``/``$EXIT_STATUS``
    set (systemd.service(5)).  Without them — a manual invocation — the
    result stays ``unknown`` unless the caller passed one explicitly.
    """
    result = (env.get("SERVICE_RESULT") or "").strip().lower()
    if result in ("success", "clean"):
        return "success"
    if result in ("canceled", "cancelled"):
        return "cancelled"
    if result:
        return "failed"
    return "unknown"


def load_run_records(path: str = DEFAULT_RECORDS_PATH) -> list[dict]:
    """Load run records, newest first.  Missing/unreadable file → [].

    One JSON object per line (JSONL); corrupt lines are skipped, never
    fatal — a torn record must not hide the ones around it.
    """
    try:
        with open(path) as fh:
            lines = fh.readlines()
    except OSError:
        return []
    records: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except ValueError:
            logger.warning("Skipping corrupt activity record line in %s", path)
            continue
    records.sort(key=lambda r: r.get("ts", ""), reverse=True)
    return records


def append_run_record(
    record: dict,
    path: str = DEFAULT_RECORDS_PATH,
    max_records: int = MAX_RECORDS,
) -> None:
    """Append *record* as one JSON line, trimmed to the newest *max_records*.

    Creates the parent directory (0755) and rewrites the file atomically
    (tmp + rename) so a concurrent reader never sees a torn file.  The
    file is world-readable; only root (or the units, which run as root)
    can write it.
    """
    record.setdefault("ts", datetime.now().isoformat())
    os.makedirs(os.path.dirname(path), exist_ok=True)
    records = load_run_records(path) if os.path.isfile(path) else []
    records.append(record)
    records.sort(key=lambda r: r.get("ts", ""), reverse=True)
    del records[max_records:]
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w") as fh:
            for r in records:
                fh.write(json.dumps(r) + "\n")
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return record


def record_activity_run(
    subvolume: str,
    schedule: str,
    *,
    result: str | None = None,
    exit_status: str | None = None,
    path: str = DEFAULT_RECORDS_PATH,
    env: Optional[dict] = None,
) -> dict:
    """Write one run record and return it.

    *result* comes from the explicit argument, else from ``$SERVICE_RESULT``
    (set by systemd for ``ExecStopPost=``); *exit_status* likewise from
    ``$EXIT_STATUS``.  This is what ``manaradix activity record`` calls.
    """
    env = os.environ if env is None else env
    if result is None:
        result = _result_from_service_env(env)
    if exit_status is None:
        exit_status = (env.get("EXIT_STATUS") or "").strip() or None
    if result not in _RESULTS:
        raise ValueError(f"invalid result {result!r}")
    record: dict = {
        "subvolume": subvolume,
        "schedule": schedule,
        "result": result,
    }
    if exit_status is not None:
        record["exit_status"] = exit_status
    append_run_record(record, path=path)
    return record


def merge_never(state: str, last, last_trigger) -> str:
    """Downgrade an ``ok`` state to ``never`` without proven run evidence.

    ``ok`` requires evidence that a run actually happened.  The boot
    tier has no systemd last-trigger signal — its oneshot runs at boot
    and then rests inactive — so an enabled boot service with no run
    record and no snapshot would otherwise report ``ok`` forever.  And
    a service whose only evidence is a systemd start attempt
    (``source == "systemd"``) has not proven it ran: a missing binary
    leaves ``ExecMainStartTimestamp`` set (a 203 start attempt) and
    ``Result`` reverts to the ``success`` placeholder after
    ``systemctl reset-failed`` — neither is a run.  Only a run record
    or a snapshot sidecar proves a run.
    """
    if state == "ok" and (last is None or last.get("source") == "systemd"):
        return "never"
    return state


class ActivityStatus:
    """Per-activity status: live systemd state + last-run evidence.

    Composes ``PolicyEngine.unit_status`` (the live systemd truth) with
    the run-record file and snapshot sidecars (what actually happened).
    Pure reads; never raises.
    """

    def __init__(
        self,
        policy_engine,
        snapshot_manager,
        config_store,
        records_path: str = DEFAULT_RECORDS_PATH,
    ) -> None:
        self.engine = policy_engine
        self.snapshots = snapshot_manager
        self.config = config_store
        self.records_path = records_path

    # -- Run records --------------------------------------------------------

    def run_records(
        self,
        *,
        subvolume: str | None = None,
        schedule: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Newest run records, optionally filtered, capped at *limit*."""
        records = load_run_records(self.records_path)
        if subvolume is not None:
            records = [r for r in records if r.get("subvolume") == subvolume]
        if schedule is not None:
            records = [r for r in records if r.get("schedule") == schedule]
        return records[:limit]

    def last_run(self, subvolume: str, schedule: str) -> Optional[dict]:
        """Newest run evidence for one activity, or None.

        Prefers the run record — it also covers **failed** runs, which
        leave no snapshot.  Falls back to the newest snapshot tagged with
        the tier (evidence from before records existed).
        """
        for r in self.run_records(subvolume=subvolume, schedule=schedule, limit=1):
            return {
                "ts": r.get("ts", ""),
                "result": r.get("result", "unknown"),
                "exit_status": r.get("exit_status"),
                "source": "record",
            }
        try:
            snaps = [
                s
                for s in self.snapshots.list_all()
                if s.subvolume == subvolume and s.schedule == schedule
            ]
        except Exception as exc:
            logger.warning("Could not list snapshots for last-run lookup: %s", exc)
            snaps = []
        if snaps:
            newest = max(snaps, key=lambda s: s.date)
            return {
                "ts": newest.date.isoformat(),
                "result": "success",
                "exit_status": None,
                "source": "snapshot",
            }
        return None

    def last_run_with_fallback(
        self,
        subvolume: str,
        schedule: str,
        status: Optional[dict] = None,
    ) -> Optional[dict]:
        """Newest run evidence, falling back to systemd's service state.

        Prefers the run-record/snapshot evidence (see :meth:`last_run`).
        When none exists — units without the record step, or a service
        that failed before either record or snapshot could be written —
        the service's own last start (``last_exec`` / ``last_result``
        from ``unit_status``) is the next best evidence.  Note this is
        a **start attempt**, not a proven run: a missing binary (203)
        leaves ``ExecMainStartTimestamp`` set, so a failed tier shows
        when it failed while a timestamp with a ``success``-looking
        placeholder is reported as ``attempt`` — the state merge
        (see :func:`merge_never`) downgrades such rows to ``never``.
        """
        last = self.last_run(subvolume, schedule)
        if last is not None:
            return last
        if status is None:
            try:
                status = self.engine.unit_status(subvolume, schedule)
            except Exception as exc:
                logger.warning(
                    "Could not read unit status for %s/%s: %s",
                    subvolume, schedule, exc,
                )
                return None
        exec_ts = (status or {}).get("last_exec")
        if not exec_ts:
            return None
        last_result = (status.get("last_result") or "unknown")
        if last_result == "failed":
            result = "failed"
        else:
            # success placeholder / unknown: a start happened, but no
            # record or snapshot proves the service actually ran.
            result = "attempt"
        return {
            "ts": self._systemd_ts_to_iso(exec_ts),
            "result": result,
            "exit_status": None,
            "source": "systemd",
        }

    def _systemd_ts_to_iso(self, value: str) -> str:
        """Normalize a systemd timestamp (epoch or human local) to ISO.

        Falls back to the raw string when the engine's parser is not
        available (test doubles) or does not understand the value.
        """
        parse = getattr(self.engine, "_parse_epoch", None)
        if callable(parse):
            try:
                parsed = parse(value)
            except Exception:
                parsed = None
            if isinstance(parsed, datetime):
                return parsed.isoformat()
        return value

    # -- Aggregation --------------------------------------------------------

    def statuses(self) -> list[dict]:
        """One status row per enabled policy × active schedule.

        Row: ``{subvolume, schedule, state, last_trigger, next_trigger,
        last_run_ts, last_run_result}`` — the last-run fields come from
        ``last_run_with_fallback()`` (records, then snapshot sidecars,
        then systemd's own start-attempt evidence) and may be None.
        ``state`` of ``ok`` is downgraded to ``never`` when no proven
        run evidence exists (see :func:`merge_never`).
        """
        try:
            policies = [p for p in self.config.list_policies() if p.enabled]
        except Exception as exc:
            logger.warning("Could not list policies for activity status: %s", exc)
            policies = []

        rows: list[dict] = []
        for policy in policies:
            for schedule in policy.active_schedules:
                try:
                    status = self.engine.unit_status(policy.subvolume, schedule)
                except Exception as exc:
                    logger.warning(
                        "Could not read unit status for %s/%s: %s",
                        policy.subvolume, schedule, exc,
                    )
                    continue
                last = self.last_run_with_fallback(
                    policy.subvolume, schedule, status
                )
                rows.append({
                    **status,
                    "state": merge_never(
                        status["state"],
                        last,
                        status.get("last_trigger"),
                    ),
                    "last_run_ts": last["ts"] if last else None,
                    "last_run_result": last["result"] if last else None,
                })
        rows.sort(key=lambda r: (r["subvolume"], r["schedule"]))
        return rows
