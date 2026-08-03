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


def _shell_escape_dq(value: str) -> str:
    """Escape a value for a double-quoted POSIX shell string.

    ``$`` and backtick expand, ``"`` terminates the string, ``\\`` escapes
    — a hostile ``snapshot_dir``/``subvol_name`` could inject a command
    substitution that runs as root during ``update-grub``.  Control
    characters (newlines) are stripped — they would split the script line.
    """
    value = "".join(c for c in value if c >= " " and c != "\x7f")
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("`", "\\`")
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

# LUKS-encrypted root support: the initramfs unlock parameters of the
# current boot (rd.luks.*) are carried over to every snapshot entry, and
# GRUB unlocks the container itself when the kernel lives on the
# encrypted filesystem (no separate /boot).  Without a usable unlock
# path the entries would be dead — skip them with a warning instead.
CRYPT_PARAMS=$(tr ' ' '\\n' < /proc/cmdline | grep '^rd\\.luks' | tr '\\n' ' ')
ROOT_SOURCE=$(findmnt -n -o SOURCE / 2>/dev/null || true)
BOOT_SOURCE=$(findmnt -n -o SOURCE /boot 2>/dev/null || true)
[ -z "$BOOT_SOURCE" ] && BOOT_SOURCE="$ROOT_SOURCE"

IS_CRYPT="no"
[ -n "$CRYPT_PARAMS" ] && IS_CRYPT="yes"
case "$ROOT_SOURCE" in /dev/mapper/luks-*) IS_CRYPT="yes";; esac
# lsblk reports dm-crypt devices as type "crypt" regardless of the mapper
# name, catching custom crypttab names the luks-* pattern cannot.
ROOT_TYPE=$(lsblk -no TYPE "$ROOT_SOURCE" 2>/dev/null || true)
case "$ROOT_TYPE" in crypt) IS_CRYPT="yes";; esac

CRYPT_LUKS_UUIDS=""
if [ "$IS_CRYPT" = "yes" ] && [ "$BOOT_SOURCE" = "$ROOT_SOURCE" ]; then
    CRYPT_LUKS_UUIDS=$(echo "$CRYPT_PARAMS" | tr ' ' '\\n' | sed -n 's/^rd\\.luks\\.uuid=//p')
    if [ -z "$CRYPT_LUKS_UUIDS" ]; then
        # No rd.luks params on the command line: derive the container
        # uuid from the device-mapper name (dracut names it
        # luks-<container-uuid>); initramfs unlock then follows crypttab.
        CRYPT_LUKS_UUIDS=$(basename "$ROOT_SOURCE" 2>/dev/null | sed -n 's/^luks-//p')
    fi
    if [ -z "$CRYPT_LUKS_UUIDS" ]; then
        echo "manaradix: skipping snapshot boot entries: encrypted root but GRUB unlock parameters unknown" >&2
        exit 0
    fi
fi

count=0
for snapshot_dir in "$SNAPSHOT_DIR"/*/; do
    [ -d "${{snapshot_dir}}{subvol_name}" ] || continue
    snap_id=$(basename "$snapshot_dir")
    # Snapshot dirs are manaradix timestamps (digits/dashes only).  Skip
    # anything else — a hostile directory name must never reach the
    # menuentry text or the kernel command line (command substitution).
    case "$snap_id" in *[!0-9-]*) continue;; esac
    snap_path="{snapshot_relpath}/${{snap_id}}/{subvol_name}"

    # Emit the submenu only once, and only when at least one snapshot
    # exists — an empty submenu block is a GRUB lexer syntax error
    # (lexer.c: "Incorrect command"), which makes update-grub fail.
    if [ "$count" -eq 0 ]; then
        cat <<'SUBEOF'
submenu "Manaradix Snapshots" {{
SUBEOF
        # Kernel on the encrypted root: unlock before the entries search
        # for it.  The prompt appears when the submenu is opened.
        for luks_uuid in ${{CRYPT_LUKS_UUIDS}}; do
            cat <<CRYPTEOF
    cryptomount -u $luks_uuid
CRYPTEOF
        done
    fi

    cat <<ENTRYEOF
    menuentry "Snapshot: ${{snap_id}}" {{
        search --no-floppy --fs-uuid --set=root ${{ROOT_UUID}}
        linux /boot/vmlinuz root=UUID=${{ROOT_UUID}} rootflags=subvol=${{snap_path}} ${{CRYPT_PARAMS}} ro
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
            snapshot_dir=_shell_escape_dq(snapshot_dir),
            snapshot_relpath=_shell_escape_dq(relpath),
            subvol_name=_shell_escape_dq(subvol_name),
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
