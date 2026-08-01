"""Client-side exception hierarchy for the manaradix D-Bus service.

Errors raised locally by the client library map one-to-one onto the
error names the service emits (``org.mageia.manaradix.Error.<Kind>``),
so callers can catch typed exceptions instead of parsing raw
``DBusException`` objects.
"""

from __future__ import annotations

from dbus.exceptions import DBusException

from manaradix.dbus.constants import (
    ERROR_AUTH_REQUIRED,
    ERROR_FAILED,
    ERROR_INVALID_ARGS,
    ERROR_NOT_FOUND,
)


class ManaradixDBusError(Exception):
    """Base class for all manaradix D-Bus client errors."""


class DaemonUnavailableError(ManaradixDBusError):
    """The manaradix D-Bus service is not running (and could not be started)."""


class AuthorizationError(ManaradixDBusError):
    """The caller is not authorized for the required polkit action.

    ``action_id`` carries the polkit action that was required.
    """

    def __init__(self, message: str, action_id: str | None = None) -> None:
        super().__init__(message)
        self.action_id = action_id


class NotFoundError(ManaradixDBusError):
    """A requested snapshot, subvolume, or object does not exist."""


class InvalidArgumentError(ManaradixDBusError):
    """A method argument is malformed or invalid."""


class OperationError(ManaradixDBusError):
    """The underlying operation failed (btrfs, config, GRUB, ...)."""


def raise_for_dbus(exc: Exception) -> None:
    """Translate a raw ``DBusException`` into a :class:`ManaradixDBusError`.

    ``ServiceUnknown`` / ``UnknownMethod`` mean the daemon is not
    reachable on the bus.  Everything that is not a known error name is
    re-raised unchanged.
    """
    if not isinstance(exc, DBusException):
        raise exc
    name = getattr(exc, "get_dbus_name", lambda: None)()
    if name == ERROR_AUTH_REQUIRED:
        raise AuthorizationError(str(exc))
    if name == ERROR_NOT_FOUND:
        raise NotFoundError(str(exc))
    if name == ERROR_INVALID_ARGS:
        raise InvalidArgumentError(str(exc))
    if name == ERROR_FAILED:
        raise OperationError(str(exc))
    if name in ("org.freedesktop.DBus.Error.ServiceUnknown",
                "org.freedesktop.DBus.Error.UnknownMethod"):
        raise DaemonUnavailableError(str(exc))
    raise exc
