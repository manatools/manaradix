"""pkexec privilege escalation helpers.

Determines which CLI operations require elevated privileges and handles
re-executing the process via ``pkexec``.

Polkit actions (from ``data/polkit/org.mageia.manaradix.policy``):

=====================  =============================================
Action ID               Required for
=====================  =============================================
``read``               snapshot list, snapshot info
``manage-snapshots``   snapshot create, snapshot delete, snapshot prune
``manage-policies``    (future) policy apply/remove
``manage-filesystem``  (future) scrub, balance, defrag
=====================  =============================================
"""

from __future__ import annotations

import os
import sys

from manaradix.i18n import _

# ---------------------------------------------------------------------------
# Environment guard — prevents infinite pkexec re-exec loops
# ---------------------------------------------------------------------------

_REEXEC_ENV_VAR = "MANARADIX_PKEXEC"

# ---------------------------------------------------------------------------
# Subcommand privilege tables
# ---------------------------------------------------------------------------

# Read-only operations that never need elevation
_READ_ONLY_COMMANDS: set[tuple[str, str | None]] = {
    ("snapshot", "list"),
    ("snapshot", "info"),
    ("setup", "status"),
    ("setup", "list-profiles"),
}

# Operations requiring write access to the btrfs filesystem
_PRIVILEGED_COMMANDS: set[tuple[str, str | None]] = {
    ("snapshot", "create"),
    ("snapshot", "delete"),
    ("snapshot", "prune"),
    ("setup", "apply"),
}

_PKEXEC_BIN = "/usr/bin/pkexec"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_root() -> bool:
    """Return True if the current process is running as root (euid 0)."""
    return os.geteuid() == 0


def already_reexecuted() -> bool:
    """Return True if the process was already re-exec'd via pkexec.

    Prevents infinite loops when pkexec itself fails or the user
    dismisses the authentication dialog.
    """
    return os.environ.get(_REEXEC_ENV_VAR) == "1"


def needs_elevation(command: str | None, subcommand: str | None) -> bool:
    """Return True if *command* + *subcommand* requires elevated privileges.

    Args:
        command: Top-level command (e.g. ``"snapshot"``), or None for GUI
                 mode.
        subcommand: Nested action (e.g. ``"create"``), or None.

    Returns:
        True if the operation needs root.
    """
    # GUI mode: always needs elevation (can trigger any operation)
    if command is None:
        return True

    key = (command, subcommand)
    if key in _READ_ONLY_COMMANDS:
        return False
    if key in _PRIVILEGED_COMMANDS:
        return True

    # Unknown subcommands — safe default: require elevation
    return True


def reexec_with_pkexec() -> None:
    """Replace the current process with a pkexec-wrapped invocation.

    Does **not** return.  If pkexec is unavailable or the user cancels
    authentication, the process exits with code 1.

    The ``MANARADIX_PKEXEC`` environment variable is set on the new
    process so ``already_reexecuted()`` can detect the re-exec.
    """
    env = os.environ.copy()
    env[_REEXEC_ENV_VAR] = "1"

    pkexec_args = [
        _PKEXEC_BIN,
        "--disable-internal-agent",
        sys.argv[0],
        *sys.argv[1:],
    ]

    try:
        os.execve(_PKEXEC_BIN, pkexec_args, env)
    except FileNotFoundError:
        print(
            _("error: %s not found. Install polkit to use privileged manaradix commands.")
            % _PKEXEC_BIN,
            file=sys.stderr,
        )
        sys.exit(1)
    except OSError as exc:
        print(_("error: pkexec failed: %s") % exc, file=sys.stderr)
        sys.exit(1)
