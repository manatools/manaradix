"""Tests for the notification wrapper (fake bus)."""

from unittest.mock import MagicMock

from manaradix.tray.notifier import Notifier


class _FakeBus:
    """Mimics dbus-python's proxy path: methods resolve via
    ``get_dbus_method(name, interface)`` — exactly how
    ``dbus.Interface`` looks them up on the real session bus."""

    def __init__(self) -> None:
        self.signals = []
        self.methods = {}

    def get_object(self, name, path):
        assert name == "org.freedesktop.Notifications"
        assert path == "/org/freedesktop/Notifications"
        return self

    def get_dbus_method(self, name, dbus_interface):
        return self.methods.setdefault(name, MagicMock())

    def add_signal_receiver(self, handler, **kwargs):
        self.signals.append((handler, kwargs))


class TestNotifier:
    def test_notify_calls_daemon(self):
        bus = _FakeBus()
        bus.methods["Notify"] = MagicMock(return_value=42)
        notifier = Notifier(bus, app_name="manaradix-tray",
                            app_icon="manaradix")
        nid = notifier.notify("Summary", "Body", urgency="critical",
                              actions=["open", "Apri"])
        assert nid == 42
        args, _kwargs = bus.methods["Notify"].call_args
        app_name, replace_id, icon, summary, body, actions, hints, timeout = args
        assert app_name == "manaradix-tray"
        assert summary == "Summary"
        assert body == "Body"
        assert actions == ["open", "Apri"]
        assert int(hints["urgency"]) == 2  # critical
        assert int(timeout) == -1

    def test_notify_defaults_normal_urgency(self):
        bus = _FakeBus()
        notifier = Notifier(bus)
        notifier.notify("S")
        args, _kwargs = bus.methods["Notify"].call_args
        hints = args[6]
        assert int(hints["urgency"]) == 1

    def test_close(self):
        bus = _FakeBus()
        notifier = Notifier(bus)
        notifier.close(7)
        bus.methods["CloseNotification"].assert_called_once()

    def test_on_action_registers_receiver_once(self):
        bus = _FakeBus()
        notifier = Notifier(bus)
        callback = MagicMock()
        notifier.on_action(callback)
        notifier.on_action(callback)  # second call must not re-register
        assert len(bus.signals) == 1
        handler, kwargs = bus.signals[0]
        assert kwargs["signal_name"] == "ActionInvoked"
        assert kwargs["dbus_interface"] == "org.freedesktop.Notifications"

    def test_action_invoked_dispatches(self):
        bus = _FakeBus()
        notifier = Notifier(bus)
        callback = MagicMock()
        notifier.on_action(callback)
        handler = bus.signals[0][0]
        handler(5, "open")
        callback.assert_called_once_with("open", 5)

    def test_action_invoked_without_callback_is_safe(self):
        bus = _FakeBus()
        notifier = Notifier(bus)
        notifier.on_action(lambda key, nid: None)
        handler = bus.signals[0][0]
        notifier._on_action = None
        handler(5, "open")  # must not raise
