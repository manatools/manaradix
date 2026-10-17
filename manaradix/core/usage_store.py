"""SQLite-backed snapshot usage store and collector.

The dashboard used to measure snapshot disk usage synchronously
(``btrfs filesystem du`` over the snapshot directory); on a busy root
the call hit the 60 s subprocess timeout and froze the whole GUI.  This
module moves the measurement out of the GUI: a root-owned collector
process (the ``manaradix-usage.service`` oneshot, fired daily by
``manaradix-usage.timer``) writes the numbers to
``/var/lib/manaradix/usage.sqlite``; the GUI reads the file with a
read-only URI so a broken write never corrupts it, and the ``Force
Update`` button starts the collector in the background (returns
immediately).

The store is the single source of truth for the dashboard and the
``GetUsage`` D-Bus method.  The writer uses SQLite's own journal file
plus a ``meta`` table tracking the live collection state
(``running``/``ok``/``failed`` + timestamps), so a reader that opens the
DB mid-collection sees either the previous snapshot or the new one, and
never a torn file.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import tempfile
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

#: Where the usage database lives.  Parent directory must be writable
#: by root (the collector); the DB itself is world-readable so the GUI
#: (running as the logged-in user) can query it.
DEFAULT_DB_PATH = "/var/lib/manaradix/usage.sqlite"

#: Parent directory of the database.  Matches the directory used by the
#: activity run-record file (``/var/lib/manaradix/activity-runs.jsonl``)
#: — both files share the same root-owned state area.
DB_DIR = "/var/lib/manaradix"

#: SQLite busy timeout in milliseconds.  Readers open read-only; a
#: writer that holds a write lock must not block them past this, and a
#: writer blocked on another writer gives up after this.  30 s is more
#: than enough for a collector that runs once per day.
_BUSY_TIMEOUT_MS = 30_000

#: SQL schema applied to a freshly-created database.  Keep in sync with
#: the column names used by :class:`UsageStore` and :class:`UsageReader`.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS aggregates (
    mount TEXT PRIMARY KEY,
    snapshot_dir TEXT NOT NULL,
    total_bytes INTEGER NOT NULL,
    exclusive_bytes INTEGER NOT NULL,
    device_size_bytes INTEGER NOT NULL,
    measured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshot_usage (
    mount TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    snapshot_path TEXT NOT NULL,
    total_bytes INTEGER NOT NULL,
    exclusive_bytes INTEGER NOT NULL,
    measured_at TEXT NOT NULL,
    PRIMARY KEY (mount, snapshot_id)
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Schema bootstrap
# ---------------------------------------------------------------------------


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create tables on a fresh database; no-op on an existing one.

    Wrapped in one ``executescript`` call so a half-created database
    never happens — ``CREATE TABLE IF NOT EXISTS`` is idempotent but
    the schema must be atomic with respect to the very first write.
    """
    conn.executescript(_SCHEMA)


def _open(path: str, *, read_only: bool = False) -> sqlite3.Connection:
    """Open (or create) the database with a safe URI path.

    ``mode=ro`` for read-only callers ensures a concurrent writer that
    crashed mid-update leaves the file intact — SQLite's journal
    recovery then runs on the next write, not the next read.  The busy
    timeout covers the brief lock window of a writer that finishes
    cleanly.
    """
    if read_only:
        uri = f"file:{path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    else:
        conn = sqlite3.connect(path, check_same_thread=False)
    # Parameter binding is not supported for PRAGMA values, and
    # _BUSY_TIMEOUT_MS is a module constant — interpolate directly.
    conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
    # Foreign-key style pragmas: durable writes and quick reads.
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


class UsageStore:
    """Collects and stores snapshot usage measurements.

    The writer owns the database: it creates it on first use, flips the
    ``status`` meta key to ``running`` before starting the heavy call
    (so readers can show "Measurement in progress"), writes the new
    rows inside one transaction, and flips ``status`` to ``ok`` or
    ``failed:<msg>`` at the end.

    Args:
        path: Database location; defaults to :data:`DEFAULT_DB_PATH`.
    """

    def __init__(self, path: str = DEFAULT_DB_PATH) -> None:
        self._path = path
        # Parent dir must exist for sqlite3 to create the file.  Root
        # creates it; the GUI only reads, so a missing directory is a
        # "no data yet" state, not an error.
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._conn = _open(path)
        _ensure_schema(self._conn)

    # -- Lifecycle ----------------------------------------------------------

    def start_collect(self) -> None:
        """Mark the start of a collection run.

        Sets ``status=running`` and ``last_started=now`` in the ``meta``
        table.  Called before the slow ``btrfs filesystem du`` call, so
        a reader that opens the DB during collection sees the in-progress
        state instead of stale numbers.
        """
        now = datetime.now().isoformat()
        self._upsert_meta("status", "running")
        self._upsert_meta("last_started", now)
        self._conn.commit()

    def finish_collect(
        self,
        aggregates: list[dict],
        snapshots: list[dict],
    ) -> None:
        """Replace the stored data with a fresh measurement.

        ``aggregates`` is one row per mount (``mount``, ``snapshot_dir``,
        ``total_bytes``, ``exclusive_bytes``, ``device_size_bytes``);
        ``snapshots`` is one row per snapshot (``mount``,
        ``snapshot_id``, ``snapshot_path``, ``total_bytes``,
        ``exclusive_bytes``).  Rows are rewritten inside one transaction;
        a reader always sees either the previous snapshot or the new one.

        On success the ``meta`` row ``status`` becomes ``ok`` and
        ``last_finished`` is set.
        """
        now = datetime.now().isoformat()
        try:
            cur = self._conn.cursor()
            cur.execute("BEGIN")
            cur.execute("DELETE FROM aggregates")
            for row in aggregates:
                cur.execute(
                    "INSERT INTO aggregates "
                    "(mount, snapshot_dir, total_bytes, exclusive_bytes, "
                    "device_size_bytes, measured_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        row["mount"],
                        row["snapshot_dir"],
                        int(row["total_bytes"]),
                        int(row["exclusive_bytes"]),
                        int(row["device_size_bytes"]),
                        now,
                    ),
                )
            cur.execute("DELETE FROM snapshot_usage")
            for row in snapshots:
                cur.execute(
                    "INSERT INTO snapshot_usage "
                    "(mount, snapshot_id, snapshot_path, total_bytes, "
                    "exclusive_bytes, measured_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        row["mount"],
                        row["snapshot_id"],
                        row["snapshot_path"],
                        int(row["total_bytes"]),
                        int(row["exclusive_bytes"]),
                        now,
                    ),
                )
            self._upsert_meta("status", "ok")
            self._upsert_meta("last_finished", now)
            self._conn.commit()
        except Exception:
            # A failure mid-write leaves the previous snapshot intact —
            # sqlite's rollback undoes the partial transaction.
            try:
                self._conn.rollback()
            except Exception:
                pass
            raise

    def fail_collect(self, message: str) -> None:
        """Record a collection failure.

        ``status`` becomes ``failed:<message>`` (truncated at 200 chars
        so a runaway error message does not grow the meta row).  The
        previous data rows stay untouched — the dashboard keeps showing
        the last successful measurement, with an explicit "failed" note.
        """
        short = (message or "unknown error")[:200]
        self._upsert_meta("status", f"failed:{short}")
        now = datetime.now().isoformat()
        self._upsert_meta("last_finished", now)
        self._conn.commit()

    def is_running(self) -> bool:
        """True when a collection run is in progress."""
        cur = self._conn.execute(
            "SELECT value FROM meta WHERE key = 'status'"
        )
        row = cur.fetchone()
        return bool(row) and row[0] == "running"

    # -- Helpers ------------------------------------------------------------

    def _upsert_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    @property
    def path(self) -> str:
        """The database path (exposed for tests)."""
        return self._path


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------


def _meta_dict(conn: sqlite3.Connection) -> dict[str, str]:
    """Return the ``meta`` table as a plain ``{key: value}`` dict."""
    cur = conn.execute("SELECT key, value FROM meta")
    return {k: v for k, v in cur.fetchall()}


class UsageReader:
    """Reads the usage store without locking the writer.

    Opens the database read-only (``file://...?mode=ro``) so a crashed
    writer leaves the file intact — SQLite's journal recovery runs on
    the next write, never the next read.  A broken read-only URI falls
    back to a regular open, which is fine on an unwritable filesystem
    that still has the file.

    Args:
        path: Database location; defaults to :data:`DEFAULT_DB_PATH`.
    """

    def __init__(self, path: str = DEFAULT_DB_PATH) -> None:
        self._path = path
        if not os.path.isfile(path):
            # No database yet (first run, never collected): every read
            # returns empty, status is idle.  We do NOT create the file
            # — that would require write access the GUI does not have.
            self._conn = None
            return
        try:
            self._conn = _open(path, read_only=True)
        except sqlite3.OperationalError:
            # Read-only URI rejected (older sqlite, or weird fs).  Fall
            # back to a regular connection; the file stays unmodified
            # because we never write.
            try:
                self._conn = _open(path)
            except sqlite3.OperationalError as exc:
                logger.warning(
                    "Could not open usage database %s: %s", path, exc
                )
                self._conn = None

    # -- Queries ------------------------------------------------------------

    def aggregates(self) -> list[dict]:
        """One row per measured mount (newest measurement per mount)."""
        if self._conn is None:
            return []
        try:
            cur = self._conn.execute(
                "SELECT mount, snapshot_dir, total_bytes, exclusive_bytes, "
                "device_size_bytes, measured_at FROM aggregates"
            )
        except sqlite3.OperationalError as exc:
            logger.warning("usage aggregates query failed: %s", exc)
            return []
        return [
            {
                "mount": r[0],
                "snapshot_dir": r[1],
                "total_bytes": r[2],
                "exclusive_bytes": r[3],
                "device_size_bytes": r[4],
                "measured_at": r[5],
            }
            for r in cur.fetchall()
        ]

    def aggregate_for(self, mount: str = "/") -> Optional[dict]:
        """Aggregate row for one mount, or None when no measurement exists."""
        for row in self.aggregates():
            if row["mount"] == mount:
                return row
        return None

    def snapshots(self, mount: Optional[str] = None) -> list[dict]:
        """Per-snapshot rows, optionally filtered by mount."""
        if self._conn is None:
            return []
        try:
            if mount is None:
                cur = self._conn.execute(
                    "SELECT mount, snapshot_id, snapshot_path, total_bytes, "
                    "exclusive_bytes, measured_at FROM snapshot_usage"
                )
            else:
                cur = self._conn.execute(
                    "SELECT mount, snapshot_id, snapshot_path, total_bytes, "
                    "exclusive_bytes, measured_at FROM snapshot_usage "
                    "WHERE mount = ?",
                    (mount,),
                )
        except sqlite3.OperationalError as exc:
            logger.warning("usage snapshots query failed: %s", exc)
            return []
        return [
            {
                "mount": r[0],
                "snapshot_id": r[1],
                "snapshot_path": r[2],
                "total_bytes": r[3],
                "exclusive_bytes": r[4],
                "measured_at": r[5],
            }
            for r in cur.fetchall()
        ]

    def status(self) -> dict:
        """Current collector state.

        ``state`` is one of ``idle`` (no collection has ever run),
        ``running``, ``ok``, or ``failed``.  ``last_error`` holds the
        tail of the failure message (None otherwise).  ``last_started``
        and ``last_finished`` are ISO timestamps (None when unknown).
        """
        if self._conn is None:
            return {
                "state": "idle",
                "last_started": None,
                "last_finished": None,
                "last_error": None,
            }
        try:
            meta = _meta_dict(self._conn)
        except sqlite3.OperationalError as exc:
            logger.warning("usage status query failed: %s", exc)
            return {
                "state": "idle",
                "last_started": None,
                "last_finished": None,
                "last_error": None,
            }
        raw = meta.get("status", "")
        if raw == "running":
            state = "running"
            error = None
        elif raw.startswith("failed:"):
            state = "failed"
            error = raw[len("failed:") :]
        elif raw == "ok":
            state = "ok"
            error = None
        else:
            state = "idle"
            error = None
        return {
            "state": state,
            "last_started": meta.get("last_started"),
            "last_finished": meta.get("last_finished"),
            "last_error": error,
        }

    @property
    def path(self) -> str:
        """The database path (exposed for tests)."""
        return self._path


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------


def _parse_du_multi_path_output(stdout: str) -> list[dict]:
    """Parse ``btrfs filesystem du -s --raw p1 p2 ...`` output.

    One summary row per path in the argument list, formatted as::

         Total   Exclusive  Set shared  Filename
         1228800    122880           0  /.snapshots
          204800      2048           0  /.snapshots/2026-01-01_00-00-00/root

    A header line starting with ``Total`` (possibly with leading spaces)
    is skipped; data rows match the existing :data:`_DU_ROW_RE` shape.
    """
    # Reuse the same regex shape the single-path parser uses.
    import re

    row_re = re.compile(r"^\s*(\d+)\s+(\d+)\s+(\d+)\s+(\S+)\s*$")
    rows: list[dict] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        if line.lstrip().lower().startswith("total"):
            continue
        m = row_re.match(line)
        if not m:
            continue
        total, exclusive, _shared, path = m.groups()
        rows.append({
            "path": path,
            "total_bytes": int(total),
            "exclusive_bytes": int(exclusive),
        })
    return rows


class UsageCollector:
    """Gathers snapshot usage and stores it.

    One ``btrfs filesystem du -s --raw`` invocation over the snapshot
    directories of every btrfs mount, plus each snapshot path, gives the
    aggregate *and* per-snapshot numbers in a single pass.  The result
    is written through a :class:`UsageStore` so readers see a
    consistent snapshot (pun intended).

    Args:
        btrfs_ops: ``BtrfsOps`` instance (subprocess calls).
        snapshot_manager: ``SnapshotManager`` for the per-mount snapshot
            lists used to enumerate snapshot paths.
        store: ``UsageStore`` (defaults to a fresh one on
            :data:`DEFAULT_DB_PATH`).
    """

    def __init__(
        self,
        btrfs_ops,
        snapshot_manager,
        store: Optional[UsageStore] = None,
    ) -> None:
        self._ops = btrfs_ops
        self._snapshots = snapshot_manager
        self._store = store or UsageStore()

    def collect_once(self) -> None:
        """Run a single collection cycle.

        Walks every btrfs mount, builds the list of paths to measure
        (one snapshot dir + every snapshot path per mount), runs the
        single subprocess, parses the output, and writes the result.
        Failures are recorded (status=``failed``) so the dashboard can
        show the previous measurement plus a failure note rather than
        silently going stale.
        """
        self._store.start_collect()
        try:
            targets = self._build_targets()
            if not targets["paths"]:
                # No btrfs mounts or no snapshot dirs — empty but
                # successful measurement; the dashboard shows 0 rows.
                self._store.finish_collect([], [])
                return
            du_rows = self._measure(targets["paths"])
            aggregates, snapshots = self._partition(du_rows, targets)
            self._store.finish_collect(aggregates, snapshots)
        except Exception as exc:
            logger.warning("usage collection failed: %s", exc)
            try:
                self._store.fail_collect(str(exc))
            except Exception:
                logger.exception("could not record collection failure")

    # -- Helpers ------------------------------------------------------------

    def _build_targets(self) -> dict:
        """Return ``{paths, mounts}`` — every path to measure, plus the
        mount they came from so the result can be reassembled.

        ``paths`` is a flat list ordered snapshot-dir first (the
        aggregate row) then every snapshot path under it (the per-snapshot
        rows).  ``mounts`` is a list of
        ``{"mount", "snapshot_dir", "device_size_bytes"}`` dicts, one
        per mount — same order as the snapshot-dir entries in ``paths``.
        """
        paths: list[str] = []
        mounts: list[dict] = []
        try:
            btrfs_mounts = self._ops.btrfs_mounts()
        except Exception as exc:
            logger.warning("could not list btrfs mounts: %s", exc)
            btrfs_mounts = []

        # One filesystem may be mounted at several targets (/, /var,
        # ...); keep the shortest (top-level) target per uuid so the
        # aggregate is measured once per filesystem — same rule as the
        # dashboard's internal stats section.
        by_uuid: dict[str, dict] = {}
        for m in btrfs_mounts:
            uuid = m.get("uuid", "")
            if uuid and (
                uuid not in by_uuid
                or len(m["target"]) < len(by_uuid[uuid]["target"])
            ):
                by_uuid[uuid] = m
        mounts_to_walk = list(by_uuid.values()) if by_uuid else btrfs_mounts

        for mount_info in mounts_to_walk:
            mount = mount_info["target"]
            snap_dir = self._ops.snapshot_dir
            snap_base = (
                snap_dir if os.path.isabs(snap_dir)
                else f"{mount.rstrip('/')}/{snap_dir.lstrip('/')}"
            )
            if not os.path.isdir(snap_base):
                continue
            try:
                usage = self._ops.filesystem_usage(mount)
            except Exception as exc:
                logger.warning(
                    "could not read device size for %s: %s", mount, exc
                )
                usage = {}
            device_size = usage.get("overall", {}).get("device_size", 0)
            paths.append(snap_base)
            mounts.append({
                "mount": mount,
                "snapshot_dir": snap_base,
                "device_size_bytes": device_size,
            })
            try:
                for snap in self._snapshots.list_all(mount):
                    paths.append(snap.path)
            except Exception as exc:
                logger.warning(
                    "could not list snapshots for %s: %s", mount, exc
                )
        return {"paths": paths, "mounts": mounts}

    def _measure(self, paths: list[str]) -> list[dict]:
        """Run ``btrfs filesystem du -s --raw`` over every path at once.

        A single subprocess for the whole batch is dramatically faster
        than one call per path — btrfs opens the filesystem once and
        walks the extent tree in a single pass.
        """
        # BtrfsOps exposes the single-path parser; we reuse it on the
        # multi-path output by driving the CLI directly — avoids adding
        # a multi-path variant to BtrfsOps for a one-off consumer.
        cmd = [
            self._ops.BTRFS_BIN,
            "filesystem", "du", "-s", "--raw",
            *paths,
        ]
        import subprocess

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, check=False, timeout=600,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"btrfs du failed: {exc}") from exc
        if result.returncode != 0 and not result.stdout.strip():
            raise RuntimeError(
                f"btrfs du failed: {result.stderr.strip() or 'exit ' + str(result.returncode)}"
            )
        return _parse_du_multi_path_output(result.stdout)

    def _partition(
        self, du_rows: list[dict], targets: dict
    ) -> tuple[list[dict], list[dict]]:
        """Split a flat du output into per-mount aggregates and per-snapshot rows.

        ``targets["mounts"]`` lists snapshot dirs in the same order
        ``paths`` was built; ``targets["paths"]`` is the full ordered
        list.  The first row of ``du_rows`` matching a snapshot dir is
        the aggregate; the following rows until the next snapshot dir
        are the per-snapshot rows.
        """
        snap_dirs = {m["snapshot_dir"]: m for m in targets["mounts"]}
        aggregates: list[dict] = []
        snapshots: list[dict] = []
        current_mount_info: Optional[dict] = None
        for row in du_rows:
            path = row["path"]
            if path in snap_dirs:
                info = snap_dirs[path]
                current_mount_info = info
                aggregates.append({
                    "mount": info["mount"],
                    "snapshot_dir": info["snapshot_dir"],
                    "total_bytes": row["total_bytes"],
                    "exclusive_bytes": row["exclusive_bytes"],
                    "device_size_bytes": info["device_size_bytes"],
                })
                continue
            if current_mount_info is None:
                # Stray row before any snapshot dir — skip.
                continue
            # Snapshot path looks like "<snap_dir>/<timestamp>/<subvol>";
            # extract the timestamp dir as the snapshot id.
            snap_id = self._snapshot_id_from_path(
                path, current_mount_info["snapshot_dir"]
            )
            if not snap_id:
                continue
            snapshots.append({
                "mount": current_mount_info["mount"],
                "snapshot_id": snap_id,
                "snapshot_path": path,
                "total_bytes": row["total_bytes"],
                "exclusive_bytes": row["exclusive_bytes"],
            })
        return aggregates, snapshots

    @staticmethod
    def _snapshot_id_from_path(path: str, snap_base: str) -> Optional[str]:
        """Extract the snapshot id from a full snapshot path.

        Snapshot paths are ``<snap_base>/<YYYY-MM-DD_HH-MM-SS>/<subvol>``;
        the id is the timestamp directory.  A path that does not live
        under ``snap_base`` returns None (defensive — the du call was
        asked for snapshot paths only, but the filesystem may have been
        restructured between the list and the measurement).
        """
        prefix = snap_base.rstrip("/") + "/"
        if not path.startswith(prefix):
            return None
        rest = path[len(prefix):]
        # First component is the timestamp dir; anything after the next
        # slash is the subvol copy inside it.
        return rest.split("/", 1)[0] or None
