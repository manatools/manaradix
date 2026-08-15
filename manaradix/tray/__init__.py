"""Notification-area applet (system tray) for manaradix.

The applet ships in the main package (spec §16.7) and implements the
StatusNotifierItem protocol by hand over python3-dbus — Mageia 10 ships
no ayatana-appindicator or pystray, and python3-dbus is already a hard
Requires.  See ``manaradix/tray/app.py`` for the wiring and
``docs/tray-applet.md`` for the user documentation.
"""

__all__ = ["app", "checks", "dbus_menu", "notifier", "scheduler", "status_icon"]
