"""StatusNotifierItem (SNI) tray icon over the session bus.

Hand-rolled over python3-dbus (no ayatana-appindicator/pystray in the
Mageia 10 repositories), following the system-tray patterns dnfdragora
uses.  The item exports a ``org.kde.StatusNotifierItem`` object with
the minimal ``org.freedesktop.DBus.Properties`` surface, registers with
``org.kde.StatusNotifierWatcher``, and pushes state changes through the
``New*`` signals.  Pure helpers (:func:`icon_state`) are separated from
the bus wiring so tests can exercise the logic without a bus.
"""

from __future__ import annotations

from pathlib import Path

from manaradix.i18n import _

SNI_IFACE = "org.kde.StatusNotifierItem"
SNI_WATCHER = "org.kde.StatusNotifierWatcher"
SNI_WATCHER_PATH = "/StatusNotifierWatcher"

SNI_CATEGORY_APP_STATUS = "ApplicationStatus"
SNI_STATUS_PASSIVE = "Passive"
SNI_STATUS_ACTIVE = "Active"
SNI_STATUS_NEEDS_ATTENTION = "NeedsAttention"

#: Default icon names (normal / attention state).
ICON_NORMAL = "manaradix"
ICON_ATTENTION = "manaradix-attention"

#: Repo checkout of the hicolor icons (editable installs).
_REPO_ICONS = Path(__file__).resolve().parents[2] / "data" / "icons" / "hicolor"
_SYSTEM_ICONS = Path("/usr/share/icons/hicolor")


def icon_available(name: str) -> bool:
    """True when an icon called *name* is shipped (repo or system)."""
    for base in (_REPO_ICONS, _SYSTEM_ICONS):
        for size in ("scalable", "256x256", "48x48", "22x22"):
            if (base / size / "apps" / f"{name}.svg").is_file():
                return True
            if (base / size / "apps" / f"{name}.png").is_file():
                return True
    return False


def icon_state(findings, *, hide_when_ok: bool = False) -> tuple[str, str]:
    """(SNI status, icon name) for a merged list of findings.

    No findings → active with the normal icon by default (the icon
    stays visible and clickable for quick GUI access); with
    ``hide_when_ok`` the healthy state is passive instead, which Plasma
    renders in the hidden overflow.  Warnings → active; any critical
    finding → NeedsAttention, using the attention icon when one is
    shipped (the current art is black line work that fades on dark
    themes — spec §16.8).
    """
    severities = {finding.severity for finding in findings}
    if "critical" in severities:
        return (
            SNI_STATUS_NEEDS_ATTENTION,
            ICON_ATTENTION if icon_available(ICON_ATTENTION) else ICON_NORMAL,
        )
    if severities:
        return SNI_STATUS_ACTIVE, ICON_NORMAL
    if hide_when_ok:
        return SNI_STATUS_PASSIVE, ICON_NORMAL
    return SNI_STATUS_ACTIVE, ICON_NORMAL


def _tooltip_struct(title: str, body: str):
    """The SNI ToolTip property: ``(name, icon-pixmap, title, body)``."""
    import dbus  # lazy: this module stays importable without python3-dbus

    return dbus.Struct(
        (dbus.String(""), dbus.Array([], signature="(sv)"),
         dbus.String(title), dbus.String(body)),
        signature="(sa(sv)ss)",
    )


def _menu_struct(service_name: str) -> object:
    """The SNI Menu property: ``(service, path)`` pointing at the dbusmenu."""
    import dbus  # lazy

    return dbus.Struct((dbus.String(service_name), dbus.String("/MenuBar")),
                       signature="(so)")


class StatusIcon(object):
    """The SNI item: Properties surface + New* signals + host callbacks.

    The dbus.service.Object registration happens in
    :meth:`export` so this class can be constructed and tested without
    python3-dbus; the applet calls :meth:`export` once a session bus is
    available.  All ``set_*`` methods emit the matching SNI signal when
    the value actually changes.
    """

    def __init__(
        self,
        *,
        service_name: str = "org.kde.StatusNotifierItem",
        app_id: str = "manaradix-tray",
        title: str = "Manaradix",
        icon_name: str = ICON_NORMAL,
        on_activate=None,
        on_secondary=None,
        on_context_menu=None,
    ) -> None:
        self.service_name = service_name
        self.app_id = app_id
        self._title = title
        self._status = SNI_STATUS_PASSIVE
        self._icon_name = icon_name
        self._tooltip_title = title
        self._tooltip_body = ""
        self._on_activate = on_activate
        self._on_secondary = on_secondary
        self._on_context_menu = on_context_menu
        self._object = None  # the exported dbus.service.Object, after export

    # -- State (pure; testable without a bus) --------------------------------

    @property
    def status(self) -> str:
        """The current SNI status."""
        return self._status

    @property
    def icon_name(self) -> str:
        """The current icon name."""
        return self._icon_name

    def set_status(self, status: str) -> None:
        """Change the SNI status, emitting ``NewStatus`` when it changes."""
        if status == self._status:
            return
        self._status = status
        if self._object is not None:
            # update the readable property, then announce the change —
            # hosts re-read the property when the signal arrives
            self._object._set_prop("Status", status)
            self._object.NewStatus(status)

    def set_icon(self, icon_name: str) -> None:
        """Change the icon, emitting ``NewIcon`` when it changes."""
        if icon_name == self._icon_name:
            return
        self._icon_name = icon_name
        if self._object is not None:
            self._object._set_prop("IconName", icon_name)
            self._object.NewIcon()

    def set_tooltip(self, title: str, body: str = "") -> None:
        """Change the tooltip, emitting ``NewToolTip`` when it changes."""
        if (title, body) == (self._tooltip_title, self._tooltip_body):
            return
        self._tooltip_title = title
        self._tooltip_body = body
        if self._object is not None:
            self._object._set_prop("ToolTip",
                                   _tooltip_struct(title, body))
            self._object.NewToolTip()

    # -- Bus wiring ----------------------------------------------------------

    def export(self, bus, watcher: bool = True) -> None:
        """Export the item on *bus* and register with the SNI watcher.

        Args:
            bus: The session bus connection (dbus-python).
            watcher: Register with ``org.kde.StatusNotifierWatcher``
                (disable in tests).
        """
        import dbus
        import dbus.service

        # Claim the service name the item registers under.  dbus-python
        # does not do this for us: without the claim the watcher accepts
        # the registration but plasmashell cannot reach the item (no
        # icon).  KDE clients use the same org.kde.StatusNotifierItem-
        # PID-instance convention.
        try:
            import dbus.bus

            bus.request_name(self.service_name,
                             dbus.bus.NAME_FLAG_DO_NOT_QUEUE)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                "Could not claim the SNI service name %s: %s",
                self.service_name, exc)

        class _SniObject(dbus.service.Object):
            """The exported SNI object: properties, signals, methods."""

            def __init__(self, icon: StatusIcon) -> None:
                super().__init__(bus, "/StatusNotifierItem")
                self._icon = icon
                self._props = {
                    "Category": SNI_CATEGORY_APP_STATUS,
                    "Id": icon.app_id,
                    "Title": icon._title,
                    "Status": icon._status,
                    "WindowId": 0,
                    "IconName": icon._icon_name,
                    "IconPixmap": dbus.Array([], signature="(iiay)"),
                    "OverlayIconName": "",
                    "AttentionIconName": "",
                    "AttentionMovieName": "",
                    "ToolTip": _tooltip_struct(icon._tooltip_title,
                                               icon._tooltip_body),
                    "ItemIsMenu": dbus.Boolean(False),
                    "Menu": _menu_struct(icon.service_name),
                }

            def _set_prop(self, name: str, value) -> None:
                """Keep the readable property in sync with the state."""
                self._props[name] = value

            @dbus.service.method(
                "org.freedesktop.DBus.Properties", in_signature="ss",
                out_signature="v")
            def Get(self, interface_name: str, property_name: str):
                if interface_name == SNI_IFACE and property_name in self._props:
                    return self._props[property_name]
                raise dbus.exceptions.DBusException(
                    f"No such property {interface_name}.{property_name}",
                    name="org.freedesktop.DBus.Error.InvalidArgs")

            @dbus.service.method(
                "org.freedesktop.DBus.Properties", in_signature="s",
                out_signature="a{sv}")
            def GetAll(self, interface_name: str):
                if interface_name != SNI_IFACE:
                    return {}
                return self._props

            @dbus.service.method(SNI_IFACE, in_signature="ssv", out_signature="")
            def Set(self, interface_name: str, property_name: str,
                    value: object):
                raise dbus.exceptions.DBusException(
                    "Properties are read-only",
                    name="org.freedesktop.DBus.Error.PropertyReadOnly")

            @dbus.service.signal(SNI_IFACE, signature="s")
            def NewStatus(self, status: str) -> str:
                return status

            @dbus.service.signal(SNI_IFACE, signature="")
            def NewIcon(self) -> None:
                return None

            @dbus.service.signal(SNI_IFACE, signature="")
            def NewToolTip(self) -> None:
                return None

            @dbus.service.method(SNI_IFACE, in_signature="ii", out_signature="",
                                 sender_keyword="sender")
            def Activate(self, x: int, y: int, sender=None) -> None:
                if self._icon._on_activate:
                    self._icon._on_activate()

            @dbus.service.method(SNI_IFACE, in_signature="ii", out_signature="",
                                 sender_keyword="sender")
            def SecondaryActivate(self, x: int, y: int, sender=None) -> None:
                if self._icon._on_secondary:
                    self._icon._on_secondary()

            @dbus.service.method(SNI_IFACE, in_signature="ii", out_signature="",
                                 sender_keyword="sender")
            def ContextMenu(self, x: int, y: int, sender=None) -> None:
                if self._icon._on_context_menu:
                    self._icon._on_context_menu()

        self._object = _SniObject(self)
        if watcher:
            try:
                watcher_obj = bus.get_object(SNI_WATCHER, SNI_WATCHER_PATH)
                watcher_obj.RegisterStatusNotifierItem(
                    self.service_name, dbus_interface=SNI_WATCHER)
            except Exception as exc:
                # A host without a watcher is not fatal at export time;
                # the applet probes the watcher before launching anyway.
                import logging
                logging.getLogger(__name__).debug(
                    "SNI watcher registration failed: %s", exc)
