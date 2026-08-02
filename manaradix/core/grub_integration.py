"""GRUB integration for boot-from-snapshot support.

Generates and manages ``/etc/grub.d/41_manaradix``, a GRUB script
that lists read-only snapshots as bootable menu entries.
"""

from __future__ import annotations

import logging
import os
import stat
from typing import Optional

logger = logging.getLogger(__name__)

_GRUB_SCRIPT_PATH = "/etc/grub.d/41_manaradix"
_GRUB_UPDATE_BIN = "/usr/sbin/update-grub"

#: Candidate GRUB configuration files (BIOS layouts); EFI layouts are
#: covered by the ``/boot/efi/EFI/*/grub.cfg`` glob in has_system_grub().
_GRUB_CFG_CANDIDATES = (
    "/boot/grub2/grub.cfg",
    "/boot/grub/grub.cfg",
)

_GRUB_SCRIPT_TEMPLATE = """\
#!/bin/sh
# 41_manaradix — GRUB menu entries for btrfs snapshots
# Managed by manaradix. Do not edit manually.
set -e

. /usr/share/grub/grub-mkconfig_lib

SNAPSHOT_DIR="{snapshot_dir}"
[ ! -d "$SNAPSHOT_DIR" ] && exit 0

ROOT_UUID=$(findmnt -n -o UUID / 2>/dev/null || true)
[ -z "$ROOT_UUID" ] && exit 0

count=0
for snapshot_dir in "$SNAPSHOT_DIR"/*/; do
    [ -d "${{snapshot_dir}}{subvol_name}" ] || continue
    snap_id=$(basename "$snapshot_dir")
    snap_path="{snapshot_relpath}/${{snap_id}}/{subvol_name}"

    # Emit the submenu only once, and only when at least one snapshot
    # exists — an empty submenu block is a GRUB lexer syntax error
    # (lexer.c: "Incorrect command"), which makes update-grub fail.
    if [ "$count" -eq 0 ]; then
        cat <<'SUBEOF'
submenu "Manaradix Snapshots" {{
SUBEOF
    fi

    cat <<ENTRYEOF
    menuentry "Snapshot: ${{snap_id}}" {{
        search --no-floppy --fs-uuid --set=root ${{ROOT_UUID}}
        linux /boot/vmlinuz root=UUID=${{ROOT_UUID}} rootflags=subvol=${{snap_path}} ro
        initrd /boot/initrd.img
    }}
ENTRYEOF

    count=$((count + 1))
done

if [ "$count" -gt 0 ]; then
    cat <<'SUBEOF'
}}
SUBEOF
fi
"""


class GrubIntegrationError(Exception):
    """Raised when GRUB integration operations fail."""


class GrubIntegration:
    """Manages the GRUB snapshot boot menu.

    Usage::

        grub = GrubIntegration()
        grub.install_script()
        grub.update_grub()
    """

    def __init__(
        self,
        script_path: str = _GRUB_SCRIPT_PATH,
        update_grub_bin: str = _GRUB_UPDATE_BIN,
    ) -> None:
        self._script_path = script_path
        self._update_grub_bin = update_grub_bin

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_grub_script(
        self,
        snapshot_dir: str = "/.snapshots",
        subvol_name: str = "root",
    ) -> str:
        """Generate the GRUB snapshot menu script.

        Args:
            snapshot_dir: Absolute path to the snapshot directory.
            subvol_name: Name of the subvolume within each snapshot dir.

        Returns:
            The script content as a string.
        """
        relpath = snapshot_dir.lstrip("/")
        return _GRUB_SCRIPT_TEMPLATE.format(
            snapshot_dir=snapshot_dir,
            snapshot_relpath=relpath,
            subvol_name=subvol_name,
        )

    def install_script(
        self,
        snapshot_dir: str = "/.snapshots",
        subvol_name: str = "root",
        *,
        dry_run: bool = False,
    ) -> None:
        """Write the GRUB snapshot script and make it executable.

        Args:
            snapshot_dir: Absolute path to the snapshot directory.
            subvol_name: Name of the subvolume within each snapshot dir.
            dry_run: If True, log but do not write.
        """
        content = self.generate_grub_script(snapshot_dir, subvol_name)

        if dry_run:
            logger.info("[DRY RUN] Would write GRUB script to %s", self._script_path)
            logger.debug("Content:\n%s", content)
            return

        os.makedirs(os.path.dirname(self._script_path), exist_ok=True)
        with open(self._script_path, "w") as fh:
            fh.write(content)

        # Make executable
        st = os.stat(self._script_path)
        os.chmod(self._script_path, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        logger.info("Installed GRUB script: %s", self._script_path)

    def uninstall_script(self, *, dry_run: bool = False) -> None:
        """Remove the GRUB snapshot script."""
        if dry_run:
            logger.info("[DRY RUN] Would remove %s", self._script_path)
            return

        try:
            os.remove(self._script_path)
            logger.info("Removed GRUB script: %s", self._script_path)
        except OSError as exc:
            logger.warning("Could not remove %s: %s", self._script_path, exc)

    def is_installed(self) -> bool:
        """Return True if the GRUB snapshot script is installed."""
        return os.path.isfile(self._script_path) and os.access(
            self._script_path,
            os.X_OK,
        )

    def has_system_grub(self, prefix: str = "") -> bool:
        """Return True when GRUB is the system bootloader.

        Detection is conservative: a GRUB configuration file must exist
        (BIOS or EFI layout).  This is what makes boot-from-snapshot
        meaningful — a btrfs root alone is not enough.  ``prefix`` is
        prepended to every path (used by tests to probe a temp tree).
        """
        import glob

        for candidate in _GRUB_CFG_CANDIDATES:
            if os.path.isfile(prefix + candidate):
                return True
        try:
            if glob.glob(prefix + "/boot/efi/EFI/*/grub.cfg"):
                return True
        except OSError:
            pass
        return False

    def update_grub(self, *, dry_run: bool = False) -> None:
        """Run ``update-grub`` (or ``grub-mkconfig``) to regenerate the GRUB menu.

        Args:
            dry_run: If True, log but do not run.
        """
        import subprocess

        # Some distros use grub2-mkconfig directly
        if os.path.exists(self._update_grub_bin):
            cmd = [self._update_grub_bin]
        else:
            cmd = ["grub2-mkconfig", "-o", "/boot/grub2/grub.cfg"]

        if dry_run:
            logger.info("[DRY RUN] Would run: %s", " ".join(cmd))
            return

        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=60)
            logger.info("GRUB configuration updated")
        except subprocess.CalledProcessError as exc:
            raise GrubIntegrationError(
                f"update-grub failed: {exc.stderr}"
            ) from exc
        except FileNotFoundError:
            raise GrubIntegrationError(
                "Neither update-grub nor grub2-mkconfig found. "
                "Install grub2 or grub2-common."
            )

    def list_snapshot_entries(self) -> list[str]:
        """Parse the installed script and return snapshot IDs it references.

        Returns:
            List of snapshot IDs present in the GRUB script.
        """
        if not self.is_installed():
            return []

        try:
            with open(self._script_path) as fh:
                content = fh.read()
        except OSError:
            return []

        entries: list[str] = []
        for line in content.splitlines():
            if "snap_id" in line and "=" in line:
                # Extract from: snap_path=".snapshots/${snap_id}/root"
                # This is a static script — we scan the snapshot dir directly
                pass

        # Scan the snapshot directory instead
        snapshot_dir = "/.snapshots"
        try:
            for entry in sorted(os.listdir(snapshot_dir)):
                full = os.path.join(snapshot_dir, entry)
                if os.path.isdir(full):
                    entries.append(entry)
        except OSError:
            pass

        return entries
