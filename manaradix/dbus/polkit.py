"""Polkit authorization helpers for the D-Bus daemon.

The daemon runs as root; every method checks the *caller's* process
(``GetConnectionUnixProcessID`` on the message-bus connection) against
the method's polkit action, so the interactive polkit agent in the
caller's session decides the challenge.  This mirrors the udisks2
pattern.

``gi`` (PyGObject) is imported lazily inside the functions so unit
tests can patch the Polkit bindings at the module boundary and so the
client library never pulls in GI.
"""

from __future__ import annotations

import logging
import os

from manaradix.dbus.errors import AuthorizationError

logger = logging.getLogger(__name__)


def check_authorization(pid: int, action_id: str, *,
                        allow_user_interaction: bool = True) -> None:
    """Raise :class:`~manaradix.dbus.errors.AuthorizationError` unless
    process *pid* is authorized for the polkit *action_id*.

    With ``allow_user_interaction`` (the default) the polkit agent may
    prompt the user; the check blocks the caller's mainloop while the
    dialog is up.
    """
    import gi

    gi.require_version("Polkit", "1.0")
    from gi.repository import Polkit

    subject = Polkit.UnixProcess.new(pid)
    authority = Polkit.Authority.get_sync()
    flags = (
        Polkit.CheckAuthorizationFlags.ALLOW_USER_INTERACTION
        if allow_user_interaction else 0
    )
    result = authority.check_authorization_sync(subject, action_id, None,
                                                flags, None)
    if not result.get_is_authorized():
        raise AuthorizationError(
            f"Authorization required for {action_id}", action_id=action_id
        )


def sender_pid(connection, sender: str) -> int:
    """Resolve the D-Bus sender's PID via ``GetConnectionUnixProcessID``.

    *connection* is a ``dbus.BusConnection``; *sender* is the unique
    bus name supplied by the ``sender_keyword``.  Falls back to the
    daemon's own PID when the name cannot be resolved (which is safe:
    the daemon runs as root, so an unknown sender is *not* granted
    anything extra — the fallback only affects the polkit subject of
    the check).
    """
    if not sender:
        return os.getpid()
    try:
        reply = connection.call_blocking(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "GetConnectionUnixProcessID",
            "s", [sender],
        )
        return int(reply)
    except Exception as exc:  # pragma: no cover -- defensive only
        logger.warning("Could not resolve sender PID %r: %s", sender, exc)
        return os.getpid()
