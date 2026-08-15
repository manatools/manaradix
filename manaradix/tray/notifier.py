"""Desktop notifications via ``org.freedesktop.Notifications``.

Thin wrapper over the freedesktop notification daemon on the session
bus.  ``Notify`` calls carry an optional action pair (``["open", ...]``)
so the notification can launch the GUI; :meth:`Notifier.on_action`
listens for the daemon's ``ActionInvoked`` signal.
"""

from __future__ import annotations

from typing import Optional

NOTIFY_IFACE = "org.freedesktop.Notifications"
NOTIFY_PATH = "/org/freedesktop/Notifications"

_URGENCY = {"low": 0, "normal": 1, "critical": 2}


class Notifier:
    """Sends notifications; action callbacks are injectable for tests.

    Args:
        bus: The session bus connection.
        app_name: The application name shown by the daemon.
        app_icon: The icon name used on notifications.
    """

    def __init__(self, bus, *, app_name: str = "manaradix-tray",
                 app_icon: str = "manaradix") -> None:
        self._bus = bus
        self._app_name = app_name
        self._app_icon = app_icon
        self._on_action = None
        self._signal_attached = False

    def _iface(self):
        obj = self._bus.get_object(NOTIFY_IFACE, NOTIFY_PATH)
        import dbus
        return dbus.Interface(obj, NOTIFY_IFACE)

    def notify(self, summary: str, body: str = "", *,
               urgency: str = "normal",
               actions: Optional[list] = None,
               timeout: int = -1) -> int:
        """Show a notification; returns its daemon id.

        Args:
            summary: The notification title.
            body: The notification body.
            urgency: ``"low"``, ``"normal"`` or ``"critical"``.
            actions: Optional ``["key", "label", ...]`` action pairs.
            timeout: Milliseconds (default: the daemon's own policy).

        Returns:
            The notification id assigned by the daemon.
        """
        iface = self._iface()
        import dbus
        hints = {"urgency": dbus.Byte(_URGENCY.get(urgency, 1))}
        return int(iface.Notify(
            self._app_name, dbus.UInt32(0), self._app_icon,
            summary, body, actions or [], hints, dbus.Int32(timeout)))

    def close(self, notification_id: int) -> None:
        """Close a notification by its daemon id."""
        import dbus  # lazy: this module stays importable without python3-dbus

        self._iface().CloseNotification(dbus.UInt32(notification_id))

    def on_action(self, callback) -> None:
        """Invoke *callback(action_key, notification_id)* on ActionInvoked."""
        self._on_action = callback
        if self._signal_attached:
            return
        self._signal_attached = True
        self._bus.add_signal_receiver(
            self._handle_action, signal_name="ActionInvoked",
            dbus_interface=NOTIFY_IFACE, path=NOTIFY_PATH)

    def _handle_action(self, notification_id, action_key) -> None:
        if self._on_action is not None:
            self._on_action(str(action_key), int(notification_id))
