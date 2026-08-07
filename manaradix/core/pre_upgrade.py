"""Pre-upgrade snapshot trigger: script source, install, and probe.

The rpm file trigger (``data/libexec/manaradix-pre-upgrade``, body of the
``%transfiletriggerun`` in the spec) ships with the package, so on an
installed system the script is always present at
:data:`SCRIPT_PATH`.  A development checkout has no RPM, though: the
Triggers page installs the script on demand when the pre-upgrade trigger
is enabled, using the embedded :data:`SCRIPT_SOURCE`.  A parity test
keeps the embedded copy byte-identical to the shipped data file.
"""

from __future__ import annotations

import os

#: Where the trigger script lives on an installed system.
SCRIPT_PATH = "/usr/libexec/manaradix/pre-upgrade"

#: The trigger script verbatim — must stay byte-identical to
#: ``data/libexec/manaradix-pre-upgrade`` (tests enforce this).  See
#: that file's header for the rpm transaction contract and the env-var
#: test hooks.
SCRIPT_SOURCE = """#!/bin/sh
# manaradix pre-upgrade snapshot trigger
#
# Installed as an rpm %transfiletriggerun (see manaradix.spec): rpm runs
# this once at the START of every transaction that removes/upgrades
# files under /usr or /etc — i.e. essentially every package update —
# BEFORE any file is changed.  The snapshot lets the user boot the
# pre-upgrade system from the GRUB recovery menu, or restore it, after
# a failed upgrade (a distribution upgrade in particular).
#
# CONTRACT: this script must NEVER fail the rpm transaction — rpm aborts
# the whole transaction when a trigger exits non-zero.  Every error path
# exits 0; failures are left on stderr (captured by rpm) only.
#
# Behavior is decided by the CLI it calls: `snapshot create --type
# pre-upgrade` is a silent no-op (exit 0) when snapshot_before_upgrade
# is disabled in /etc/manaradix/config.toml or the root filesystem is
# not btrfs.
#
# Test hooks (production rpm scriptlets run with a clean environment):
#   MANARADIX_BIN             CLI to invoke (default /usr/bin/manaradix)
#   MANARADIX_RUN_DIR         throttle marker directory (default /run/manaradix)
#   MANARADIX_THROTTLE_SECONDS  minimum seconds between snapshots (default 600)

set +e

CLI="${MANARADIX_BIN:-/usr/bin/manaradix}"
[ -x "$CLI" ] || exit 0

# Throttle: an update run often splits into several rpm transactions
# (removals, --split-length, ...); take at most one pre-upgrade snapshot
# per window.  /run is tmpfs, so a reboot re-arms the throttle.
throttle="${MANARADIX_THROTTLE_SECONDS:-600}"
run_dir="${MANARADIX_RUN_DIR:-/run/manaradix}"
if mkdir -p "$run_dir" 2>/dev/null; then
    marker="$run_dir/last-pre-upgrade"
    if [ -f "$marker" ]; then
        last=$(cat "$marker" 2>/dev/null || echo 0)
        case "$last" in
            ''|*[!0-9]*) last=0 ;;
        esac
        now=$(date +%s 2>/dev/null || echo 0)
        case "$now" in
            ''|*[!0-9]*) now=0 ;;
        esac
        if [ "$now" -lt "$((last + throttle))" ]; then
            exit 0
        fi
    fi
    date +%s > "$marker" 2>/dev/null
fi

# Bounded runtime: a slow or stuck snapshot must not stall the update.
timeout 300 nice -n 19 ionice -c3 "$CLI" snapshot create \\
    --subvolume / --type pre-upgrade --schedule pre-upgrade

exit 0
"""


def is_installed(path: str | None = None) -> bool:
    """Return True when the trigger script exists at *path*.

    Args:
        path: Script path to probe; defaults to :data:`SCRIPT_PATH`.

    Returns:
        True when the script file is present (a package install, or an
        earlier :func:`install_script`).
    """
    return os.path.isfile(path or SCRIPT_PATH)


def install_script(path: str | None = None, dry_run: bool = False) -> str:
    """Install the trigger script, returning the path written.

    Creates the parent directory (``/usr/libexec/manaradix``) and makes
    the script executable.  The file is written from
    :data:`SCRIPT_SOURCE`, so a development checkout needs no RPM.

    Args:
        path: Destination; defaults to :data:`SCRIPT_PATH`.
        dry_run: When True, only resolve the path — nothing is written.

    Returns:
        The target path (written, or resolved under ``dry_run``).

    Raises:
        OSError: When the directory or file cannot be created (e.g. an
            unprivileged caller on a system path).
    """
    target = path or SCRIPT_PATH
    if dry_run:
        return target
    directory = os.path.dirname(target)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(target, "w", encoding="utf-8") as script:
        script.write(SCRIPT_SOURCE)
    os.chmod(target, 0o755)
    return target
