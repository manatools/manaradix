"""Thin Python wrapper around the ``btrfs`` CLI.

Each method accepts plain Python types, returns dataclass instances,
raises typed exceptions, and supports ``dry_run=True``.

Requirements: ``btrfs-progs >= 6.7``
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from manaradix.model.snapshot import Snapshot, SnapshotType
from manaradix.model.subvolume import Subvolume

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class BtrfsError(Exception):
    """Base exception for btrfs operation failures."""


class SnapshotNotFoundError(BtrfsError):
    """Requested snapshot does not exist."""


class SubvolumeNotFoundError(BtrfsError):
    """Requested subvolume does not exist."""


class BtrfsPermissionError(BtrfsError):
    """Insufficient privileges for a btrfs operation."""


# ---------------------------------------------------------------------------
# Output dataclasses
# ---------------------------------------------------------------------------


@dataclass
class BalanceStatus:
    """Status of an ongoing or completed balance operation."""

    total: int = 0
    relocated: int = 0
    running: bool = False


@dataclass
class ScrubStatus:
    """Status of an ongoing or completed scrub operation."""

    data_extents_scrubbed: int = 0
    errors: int = 0
    running: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SNAPSHOT_DIR = ".snapshots"

# Parses lines like: "ID 257 gen 42 top level 5 path root"
_SUBVOL_LIST_RE = re.compile(
    r"ID\s+(?P<id>\d+)\s+"
    r"gen\s+\d+\s+"
    r"(?:cgen\s+\d+\s+)?"
    r"(?:parent\s+(?P<parent_id>\d+)\s+)?"
    r"top level\s+\d+\s+"
    r"path\s+(?:<FS_TREE>/)?(?P<path>.*)"
)

# Parses lines like: "UUID: 01234567-89ab-cdef-0123-456789abcdef"
_UUID_RE = re.compile(r"UUID:\s+(?P<uuid>[0-9a-f-]+)")

# Parses lines like: "Parent UUID: 01234567-89ab-cdef-0123-456789abcdef"
_PARENT_UUID_RE = re.compile(r"Parent UUID:\s+(?P<uuid>[0-9a-f-]+)")


def _parse_subvol_list_line(line: str) -> Optional[dict]:
    """Parse a single line of ``btrfs subvolume list -p`` output."""
    match = _SUBVOL_LIST_RE.match(line)
    if not match:
        return None
    return {
        "id": int(match.group("id")),
        "path": match.group("path"),
        "parent_id": int(match.group("parent_id") or 0),
    }


def _parse_subvol_show(text: str) -> dict:
    """Extract UUID and Parent UUID from ``btrfs subvolume show`` output."""
    result: dict[str, str] = {}
    uuid_match = _UUID_RE.search(text)
    if uuid_match:
        result["uuid"] = uuid_match.group("uuid")
    puuid_match = _PARENT_UUID_RE.search(text)
    if puuid_match:
        result["parent_uuid"] = puuid_match.group("uuid")
    return result


# ---------------------------------------------------------------------------
# BtrfsOps
# ---------------------------------------------------------------------------


class BtrfsOps:
    """Wraps ``/usr/bin/btrfs`` subcommands.

    All methods accept ``dry_run=True`` to preview the command without
    executing it.  The ``snapshot_dir`` parameter controls the base
    directory for snapshot storage (default ``.snapshots``).

    Usage::

        ops = BtrfsOps()
        snap = ops.create_snapshot("/", "my-snapshot")
        print(ops.list_snapshots("/"))
    """

    # Mageia installs btrfs-progs under /usr/sbin; resolve via PATH with a
    # fallback so the elevated (pkexec) environment still finds it.
    BTRFS_BIN = shutil.which("btrfs") or "/usr/sbin/btrfs"
    FINMNT_BIN = "/usr/bin/findmnt"

    def __init__(self, snapshot_dir: str = ".snapshots") -> None:
        self.snapshot_dir = snapshot_dir.rstrip("/")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run(
        self,
        args: list[str],
        *,
        dry_run: bool = False,
        check: bool = True,
        capture: bool = True,
        timeout: int = 60,
    ) -> subprocess.CompletedProcess[str]:
        """Run a btrfs command.

        Args:
            args: Arguments *after* the ``btrfs`` binary.
            dry_run: If True, log and return a fake CompletedProcess.
            check: Passed to ``subprocess.run``.
            capture: If True, capture stdout/stderr.
            timeout: Command timeout in seconds.
        """
        cmd = [self.BTRFS_BIN, *args]
        if dry_run:
            logger.info("[DRY RUN] %s", " ".join(cmd))
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        logger.debug("Running: %s", " ".join(cmd))
        try:
            return subprocess.run(
                cmd,
                capture_output=capture,
                text=True,
                check=check,
                timeout=timeout,
            )
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr or ""
            if "permission denied" in stderr.lower() or "operation not permitted" in stderr.lower():
                raise BtrfsPermissionError(stderr.strip()) from exc
            raise BtrfsError(stderr.strip()) from exc
        except FileNotFoundError:
            raise BtrfsError(
                f"btrfs binary not found at {self.BTRFS_BIN}. "
                "Is btrfs-progs installed?"
            )

    # ------------------------------------------------------------------
    # Subvolume operations
    # ------------------------------------------------------------------

    def list_subvolumes(self, mount: str = "/", *, dry_run: bool = False) -> list[Subvolume]:
        """Return all subvolumes for *mount*.

        Runs ``btrfs subvolume list -p <mount>`` and enriches each entry
        with UUID data from ``btrfs subvolume show``.
        """
        result = self._run(
            ["subvolume", "list", "-p", mount],
            dry_run=dry_run,
        )
        subvolumes: list[Subvolume] = []

        # Collect subvol IDs
        subvol_data: list[dict] = []
        for line in result.stdout.splitlines():
            parsed = _parse_subvol_list_line(line)
            if parsed:
                subvol_data.append(parsed)

        # Enrich with UUID info
        for data in subvol_data:
            if dry_run:
                data["uuid"] = ""
                data["parent_uuid"] = ""
            else:
                try:
                    sv_path = f"{mount.rstrip('/')}/{data['path']}"
                    show = self._run(
                        ["subvolume", "show", sv_path],
                        capture=True,
                        check=False,
                    )
                    uuids = _parse_subvol_show(show.stdout)
                    data["uuid"] = uuids.get("uuid", "")
                    data["parent_uuid"] = uuids.get("parent_uuid", "")
                except BtrfsError:
                    data["uuid"] = ""
                    data["parent_uuid"] = ""

            subvolumes.append(Subvolume(**data))

        return subvolumes

    def get_subvolume(self, path: str) -> Optional[Subvolume]:
        """Get a single subvolume by path, or None."""
        try:
            result = self._run(["subvolume", "show", path], check=False)
            if result.returncode != 0:
                return None
        except BtrfsError:
            return None

        uuids = _parse_subvol_show(result.stdout)
        return Subvolume(
            id=0,  # not easily parseable from show output alone
            path=path,
            uuid=uuids.get("uuid", ""),
            parent_uuid=uuids.get("parent_uuid", ""),
        )

    def create_subvolume(self, path: str, *, dry_run: bool = False) -> None:
        """Create a new subvolume at *path*.

        Runs ``btrfs subvolume create <path>``.
        """
        self._run(["subvolume", "create", path], dry_run=dry_run)

    def delete_subvolume(self, path: str, *, dry_run: bool = False) -> None:
        """Delete a subvolume at *path*.

        Runs ``btrfs subvolume delete <path>``.
        """
        self._run(["subvolume", "delete", path], dry_run=dry_run)

    def set_default_subvolume(self, subvol_id: int, mount: str = "/", *, dry_run: bool = False) -> None:
        """Set the default subvolume for a mount point.

        Runs ``btrfs subvolume set-default <id> <mount>``.
        """
        self._run(
            ["subvolume", "set-default", str(subvol_id), mount],
            dry_run=dry_run,
        )

    # ------------------------------------------------------------------
    # Snapshot operations
    # ------------------------------------------------------------------

    def _snapshot_path(
        self,
        mount: str,
        subvolume: str,
        timestamp: Optional[str] = None,
    ) -> str:
        """Build the full snapshot storage path."""
        ts = timestamp or datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        subvol_name = subvolume.strip("/").replace("/", "_") or "root"
        return f"{mount.rstrip('/')}/{self.snapshot_dir}/{ts}/{subvol_name}"

    def create_snapshot(
        self,
        source: str,
        dest: str,
        *,
        read_only: bool = True,
        dry_run: bool = False,
    ) -> Snapshot:
        """Create a btrfs snapshot.

        Runs ``btrfs subvolume snapshot [-r] <source> <dest>``.

        Args:
            source: Source subvolume path.
            dest: Destination path for the snapshot.
            read_only: Create a read-only snapshot (default True).
            dry_run: Preview only.

        Returns:
            A ``Snapshot`` representing the newly created snapshot.
        """
        args = ["subvolume", "snapshot"]
        if read_only:
            args.append("-r")
        args.extend([source, dest])
        self._run(args, dry_run=dry_run)

        ts = dest.rstrip("/").rsplit("/", 1)[-1]
        snap_type = SnapshotType.READ_ONLY if read_only else SnapshotType.MANUAL
        return Snapshot(
            id=ts,
            subvolume=source,
            path=dest,
            date=datetime.now(),
            type=snap_type,
        )

    def list_snapshots(
        self,
        mount: str = "/",
        *,
        dry_run: bool = False,
    ) -> list[Snapshot]:
        """List all snapshots under ``<mount>/<snapshot_dir>``.

        Reads ``info.json`` sidecar files for metadata. Falls back to
        filesystem listing if metadata is missing.
        """
        import os

        snap_base = f"{mount.rstrip('/')}/{self.snapshot_dir}"
        snapshots: list[Snapshot] = []

        if dry_run:
            return snapshots

        if not os.path.isdir(snap_base):
            return snapshots

        for entry in sorted(os.listdir(snap_base)):
            snap_dir = os.path.join(snap_base, entry)
            if not os.path.isdir(snap_dir):
                continue

            info_path = os.path.join(snap_dir, "info.json")
            if os.path.isfile(info_path):
                snap = self._read_snapshot_info(info_path, snap_dir)
                if snap:
                    snapshots.append(snap)
                    continue

            # Fallback: scan subdirs for snapshot subvolumes
            for sub in os.listdir(snap_dir):
                sub_path = os.path.join(snap_dir, sub)
                if not os.path.isdir(sub_path):
                    continue
                snapshots.append(
                    Snapshot(
                        id=entry,
                        subvolume="/" if sub == "root" else f"/{sub}",
                        path=sub_path,
                        date=datetime.fromtimestamp(os.path.getmtime(sub_path)),
                        type=SnapshotType.TIMED,
                    )
                )

        return snapshots

    def _read_snapshot_info(
        self,
        info_path: str,
        snap_dir: str,
    ) -> Optional[Snapshot]:
        """Parse an ``info.json`` sidecar file into a Snapshot."""
        try:
            with open(info_path) as fh:
                data = json.load(fh)
            return Snapshot(
                id=data.get("id", ""),
                subvolume=data.get("subvolume", ""),
                path=snap_dir,
                date=datetime.fromisoformat(data["date"]),
                type=SnapshotType(data.get("type", "manual")),
                description=data.get("description", ""),
                schedule=data.get("schedule", ""),
            )
        except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.warning("Could not read snapshot info %s: %s", info_path, exc)
            return None

    def write_snapshot_info(self, snapshot: Snapshot) -> None:
        """Write the ``info.json`` sidecar for a snapshot."""
        import os

        info_path = os.path.join(snapshot.path, "info.json")
        os.makedirs(os.path.dirname(info_path), exist_ok=True)
        data = {
            "id": snapshot.id,
            "subvolume": snapshot.subvolume,
            "date": snapshot.date.isoformat(),
            "type": snapshot.type.value,
            "description": snapshot.description,
            "schedule": snapshot.schedule,
        }
        with open(info_path, "w") as fh:
            json.dump(data, fh, indent=2)

    def delete_snapshot(self, path: str, *, dry_run: bool = False) -> None:
        """Delete a snapshot subvolume.

        Runs ``btrfs subvolume delete <path>``.
        """
        self._run(["subvolume", "delete", path], dry_run=dry_run)

    def restore_snapshot(
        self,
        snapshot_path: str,
        target_subvol: str,
        *,
        mount: str = "/",
        dry_run: bool = False,
    ) -> Snapshot:
        """Restore a subvolume to a snapshot state (swap method).

        1. Snapshot current subvol as a pre-restore backup.
        2. Create read-write snapshot of the source.
        3. Rename (mv) current → ``.old-<ts>``, restored → target name.
        4. Set default subvolume.
        5. Return the pre-restore safety snapshot.

        .. warning::
           The rename steps require the subvolume NOT to be in use
           (i.e. not the currently mounted root).  The caller is
           responsible for ensuring this — typically by booting from
           a rescue environment or another snapshot.
        """
        import os

        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        pre_restore_path = f"{target_subvol}.pre-restore-{ts}"

        # 1. Safety snapshot of current state
        self.create_snapshot(
            target_subvol,
            pre_restore_path,
            read_only=True,
            dry_run=dry_run,
        )

        # 2. Create read-write clone of the target snapshot
        restored_path = f"{target_subvol}.restored-{ts}"
        self.create_snapshot(
            snapshot_path,
            restored_path,
            read_only=False,
            dry_run=dry_run,
        )

        if not dry_run:
            # 3. Swap: rename current → .old, restored → original name
            old_path = f"{target_subvol}.old-{ts}"
            os.rename(target_subvol, old_path)
            os.rename(restored_path, target_subvol)

            # 4. Set default subvolume
            try:
                result = self._run(
                    ["subvolume", "list", "-o", mount],
                    capture=True,
                )
                for line in result.stdout.splitlines():
                    parsed = _parse_subvol_list_line(line)
                    if parsed and f"{mount.rstrip('/')}/{parsed['path']}" == target_subvol:
                        self.set_default_subvolume(parsed["id"], mount)
                        break
            except BtrfsError:
                pass

        return Snapshot(
            id=f"pre-restore-{ts}",
            subvolume=target_subvol,
            path=pre_restore_path,
            date=datetime.now(),
            type=SnapshotType.PRE_RESTORE,
            description=f"Pre-restore backup before restoring {snapshot_path}",
        )

    # ------------------------------------------------------------------
    # Filesystem maintenance operations
    # ------------------------------------------------------------------

    def balance_start(
        self,
        mount: str = "/",
        *,
        dusage: int = 0,
        musage: int = 0,
        dry_run: bool = False,
    ) -> None:
        """Start a balance operation.

        Runs ``btrfs balance start [-dusage=<n>] [-musage=<n>] <mount>``.
        """
        args = ["balance", "start"]
        if dusage:
            args.append(f"-dusage={dusage}")
        if musage:
            args.append(f"-musage={musage}")
        args.append(mount)
        self._run(args, dry_run=dry_run, timeout=0)

    def balance_status(self, mount: str = "/") -> BalanceStatus:
        """Get status of the last or current balance operation."""
        try:
            result = self._run(
                ["balance", "status", mount],
                check=False,
            )
            output = result.stdout
        except BtrfsError:
            return BalanceStatus()

        total = relocated = 0
        running = "running" in output.lower() or "in progress" in output.lower()
        for line in output.splitlines():
            if "total relocated" in line.lower():
                m = re.search(r"(\d+)", line)
                if m:
                    relocated = int(m.group(1))
                m = re.search(r"of\s+(\d+)", line)
                if m:
                    total = int(m.group(1))
        return BalanceStatus(total=total, relocated=relocated, running=running)

    def balance_cancel(self, mount: str = "/", *, dry_run: bool = False) -> None:
        """Cancel a running balance operation."""
        self._run(["balance", "cancel", mount], dry_run=dry_run)

    def scrub_start(
        self,
        mount: str = "/",
        *,
        dry_run: bool = False,
    ) -> None:
        """Start a scrub operation.

        Runs ``btrfs scrub start [-B] <mount>``.
        """
        self._run(["scrub", "start", "-B", mount], dry_run=dry_run, timeout=0)

    def scrub_status(self, mount: str = "/") -> ScrubStatus:
        """Get scrub status for *mount*."""
        try:
            result = self._run(
                ["scrub", "status", "-d", mount],
                check=False,
            )
            output = result.stdout
        except BtrfsError:
            return ScrubStatus()

        data_extents = errors = 0
        running = "running" in output.lower()
        for line in output.splitlines():
            if "data_extents_scrubbed" in line:
                m = re.search(r"(\d+)", line)
                if m:
                    data_extents = int(m.group(1))
            if "errors" in line.lower() and "no" not in line.lower():
                m = re.search(r"(\d+)", line)
                if m:
                    errors = int(m.group(1))
        return ScrubStatus(
            data_extents_scrubbed=data_extents,
            errors=errors,
            running=running,
        )

    def scrub_cancel(self, mount: str = "/", *, dry_run: bool = False) -> None:
        """Cancel a running scrub operation."""
        self._run(["scrub", "cancel", mount], dry_run=dry_run)

    def defragment(
        self,
        path: str,
        *,
        recursive: bool = True,
        dry_run: bool = False,
    ) -> None:
        """Defragment a file or directory.

        Runs ``btrfs filesystem defrag [-r] <path>``.
        """
        args = ["filesystem", "defrag"]
        if recursive:
            args.append("-r")
        args.append(path)
        self._run(args, dry_run=dry_run, timeout=300)

    # ------------------------------------------------------------------
    # Filesystem info
    # ------------------------------------------------------------------

    def filesystem_usage(self, mount: str = "/") -> dict:
        """Return filesystem usage info as a dict.

        Runs ``btrfs filesystem usage -b <mount>`` and parses key sections.
        """
        try:
            result = self._run(
                ["filesystem", "usage", "-b", mount],
                check=False,
            )
            output = result.stdout
        except BtrfsError:
            return {}

        info: dict = {"device": {}, "overall": {}}
        in_device = False
        for line in output.splitlines():
            line = line.strip()
            if "Device size:" in line:
                m = re.search(r"(\d+)", line)
                if m:
                    info["overall"]["device_size"] = int(m.group(1))
            elif "Free (estimated):" in line:
                m = re.search(r"(\d+)", line.replace(",", ""))
                if m:
                    info["overall"]["free_estimated"] = int(m.group(1))
            elif line.startswith("/dev/"):
                in_device = True
            elif in_device and not line:
                in_device = False
        return info

    def filesystem_show(self, *, dry_run: bool = False) -> list[dict]:
        """Return all btrfs filesystems with device info.

        Runs ``btrfs filesystem show --raw`` and parses each filesystem
        block: the label/uuid line, the ``Total devices`` line, and one
        ``devid`` line per device.  Sizes come back as raw bytes.

        Returns an empty list on error (e.g. no btrfs filesystems).
        """
        try:
            result = self._run(
                ["filesystem", "show", "--raw"],
                dry_run=dry_run,
                check=False,
            )
            output = result.stdout
        except BtrfsError:
            return []

        filesystems: list[dict] = []
        current: dict | None = None
        for line in output.splitlines():
            line = line.strip()

            label_match = re.match(
                r"Label:\s*(?:'([^']*)'|none)\s+uuid:\s*(\S+)$", line
            )
            if label_match:
                label, uuid = label_match.groups()
                current = {
                    "label": label or "",
                    "uuid": uuid,
                    "total_devices": 0,
                    "fs_bytes_used": 0,
                    "devices": [],
                }
                filesystems.append(current)
                continue
            if current is None:
                continue

            total_match = re.match(
                r"Total devices\s+(\d+)\s+FS bytes used\s+(\d+)$", line
            )
            if total_match:
                current["total_devices"] = int(total_match.group(1))
                current["fs_bytes_used"] = int(total_match.group(2))
                continue

            devid_match = re.match(
                r"devid\s+(\d+)\s+size\s+(\d+)\s+used\s+(\d+)\s+path\s+(\S+)"
                r"(?:\s+(MISSING))?$",
                line,
            )
            if devid_match:
                devid, size, used, path, missing = devid_match.groups()
                current["devices"].append(
                    {
                        "devid": int(devid),
                        "size": int(size),
                        "used": int(used),
                        "path": path,
                        "missing": missing is not None,
                    }
                )
        return filesystems

    def btrfs_mounts(self, *, dry_run: bool = False) -> list[dict]:
        """Return mounted btrfs filesystems from findmnt.

        Runs ``findmnt -t btrfs -o TARGET,SOURCE,UUID -r`` (util-linux;
        ``-r`` disables the tree decorations).  Returns a list of
        ``{"target", "source", "uuid"}`` dicts, or an empty list on error.
        """
        cmd = [
            self.FINMNT_BIN,
            "-t", "btrfs",
            "-o", "TARGET,SOURCE,UUID",
            "-r",
        ]
        if dry_run:
            logger.info("[DRY RUN] %s", " ".join(cmd))
            return []
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("Could not query btrfs mounts via findmnt: %s", exc)
            return []

        mounts: list[dict] = []
        for line in proc.stdout.splitlines()[1:]:  # skip the header line
            parts = line.split("\t")  # raw findmnt separates columns with tabs
            if len(parts) >= 3:
                mounts.append(
                    {
                        "target": parts[0],
                        "source": parts[1],
                        "uuid": parts[2],
                    }
                )
        return mounts

    # ------------------------------------------------------------------
    # Mount helpers (mount/umount are util-linux binaries)
    # ------------------------------------------------------------------

    def _resolve_device(self, subvol_path: str) -> str:
        """Return the block device backing *subvol_path*'s filesystem.

        Matches the btrfs mount whose target equals the subvolume path,
        falling back to the longest-prefix match (e.g. a nested subvolume
        under a mounted parent).
        """
        mounts = self.btrfs_mounts()
        exact = [m for m in mounts if m["target"] == subvol_path]
        if exact:
            return exact[0]["source"]
        matches = [
            m for m in mounts
            if subvol_path.startswith(m["target"].rstrip("/") + "/")
            or subvol_path == m["target"].rstrip("/")
        ]
        if not matches:
            raise BtrfsError(
                f"Cannot determine the btrfs device for subvolume {subvol_path!r}: "
                "no matching btrfs mount found"
            )
        return max(matches, key=lambda m: len(m["target"]))["source"]

    def mount_subvolume(
        self,
        subvol_path: str,
        mount_point: str,
        *,
        device: str | None = None,
        read_only: bool = True,
        dry_run: bool = False,
    ) -> None:
        """Mount a btrfs subvolume (e.g. a snapshot) at *mount_point*.

        The backing device is resolved from ``findmnt`` unless given
        explicitly.

        Raises:
            BtrfsError: If the device cannot be resolved or mount fails.
        """
        if device is None:
            device = self._resolve_device(subvol_path)
        cmd = [
            "mount",
            "-o",
            f"subvol={subvol_path}",
            "-o",
            "ro" if read_only else "rw",
            device,
            mount_point,
        ]
        if dry_run:
            logger.info("[DRY RUN] %s", " ".join(cmd))
            return
        logger.debug("Running: %s", " ".join(cmd))
        try:
            subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            )
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr or ""
            if "permission denied" in stderr.lower() or "operation not permitted" in stderr.lower():
                raise BtrfsPermissionError(stderr.strip()) from exc
            raise BtrfsError(stderr.strip()) from exc
        except FileNotFoundError:
            raise BtrfsError(
                "mount binary not found. Is util-linux installed?"
            ) from None

    def umount(self, mount_point: str, *, dry_run: bool = False) -> None:
        """Unmount a previously mounted subvolume.

        Raises:
            BtrfsError: If the unmount fails.
        """
        cmd = ["umount", mount_point]
        if dry_run:
            logger.info("[DRY RUN] %s", " ".join(cmd))
            return
        logger.debug("Running: %s", " ".join(cmd))
        try:
            subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            )
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr or ""
            if "permission denied" in stderr.lower() or "operation not permitted" in stderr.lower():
                raise BtrfsPermissionError(stderr.strip()) from exc
            raise BtrfsError(stderr.strip()) from exc
        except FileNotFoundError:
            raise BtrfsError(
                "umount binary not found. Is util-linux installed?"
            ) from None

    # ------------------------------------------------------------------
    # Compression helpers
    # ------------------------------------------------------------------

    def get_compression(self, path: str) -> Optional[str]:
        """Read the current compression property of a path.

        Runs ``btrfs property get <path> compression``.
        """
        try:
            result = self._run(
                ["property", "get", path, "compression"],
                check=False,
            )
            if result.returncode == 0:
                # Output: "compression=lzo" or "compression=zstd"
                if "=" in result.stdout:
                    return result.stdout.strip().split("=", 1)[1]
                return result.stdout.strip()
        except BtrfsError:
            pass
        return None

    def set_compression(
        self,
        path: str,
        algorithm: str,
        *,
        dry_run: bool = False,
    ) -> None:
        """Set compression on a path.

        Runs ``btrfs property set <path> compression <algo>``.
        Use ``""`` to disable compression.
        """
        if algorithm:
            self._run(
                ["property", "set", path, "compression", algorithm],
                dry_run=dry_run,
            )
        else:
            self._run(
                ["property", "set", path, "compression", ""],
                dry_run=dry_run,
            )
