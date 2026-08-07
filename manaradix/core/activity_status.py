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


def merge_never(state: str, last_run_ts, last_trigger) -> str:
    """Downgrade an ``ok`` state to ``never`` when there is no run evidence.

    The boot tier has no systemd last-trigger signal — its oneshot runs
    at boot and then rests inactive — so an enabled boot service with
    no run record and no snapshot would otherwise report ``ok`` forever.
    The rule is harmless for timer tiers: the engine already reports
    ``failed`` for a fire that never became a run, and an ``ok`` timer
    tier always carries a ``last_trigger``.
    """
    if state == "ok" and last_run_ts is None and last_trigger is None:
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

    # -- Aggregation --------------------------------------------------------

    def statuses(self) -> list[dict]:
        """One status row per enabled policy × active schedule.

        Row: ``{subvolume, schedule, state, last_trigger, next_trigger,
        last_run_ts, last_run_result}`` — the last-run fields come from
        ``last_run()`` and may be None.  ``state`` of ``ok`` is downgraded
        to ``never`` when no run evidence exists at all (see
        :func:`merge_never`).
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
                last = self.last_run(policy.subvolume, schedule)
                rows.append({
                    **status,
                    "state": merge_never(
                        status["state"],
                        last["ts"] if last else None,
                        status.get("last_trigger"),
                    ),
                    "last_run_ts": last["ts"] if last else None,
                    "last_run_result": last["result"] if last else None,
                })
        rows.sort(key=lambda r: (r["subvolume"], r["schedule"]))
        return rows
