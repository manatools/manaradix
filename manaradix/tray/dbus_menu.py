"""Minimal ``com.canonical.dbusmenu`` context menu for the tray icon.

The StatusNotifierItem spec points the host at a dbusmenu object via
the ``Menu`` property; the host calls ``GetLayout`` to read the tree
and ``Event`` to deliver clicks.  Only the subset every host needs is
implemented.  Item building is a pure function (:func:`build_menu`)
so the layout is testable without a bus; :class:`MenuModel` is the
pure item store behind the wire class.
"""

from __future__ import annotations

from manaradix.i18n import _

MENU_IFACE = "com.canonical.dbusmenu"
MENU_PATH = "/MenuBar"

#: Menu item ids.
ID_OPEN = 1
ID_CHECK_NOW = 2
ID_SEPARATOR = 3
ID_QUIT = 4


def build_menu(open_label: str = None, check_label: str = None,
               quit_label: str = None) -> list[tuple[int, dict]]:
    """The menu tree as ``(item_id, properties)`` pairs.

    Args:
        open_label: Label for the Open action (default: translated).
        check_label: Label for the Check now action.
        quit_label: Label for the Quit action.

    Returns:
        Flat list of ``(id, props)`` in display order; the root item
        (id 0) is implicit.
    """
    return [
        (ID_OPEN, {
            "label": open_label or _("Open manaradix"),
            "icon-name": "manaradix",
            "enabled": True,
            "visible": True,
        }),
        (ID_CHECK_NOW, {
            "label": check_label or _("Check now"),
            "enabled": True,
            "visible": True,
        }),
        (ID_SEPARATOR, {
            "type": "separator",
            "enabled": True,
            "visible": True,
        }),
        (ID_QUIT, {
            "label": quit_label or _("Quit"),
            "enabled": True,
            "visible": True,
        }),
    ]


class MenuModel:
    """Pure dbusmenu item store: layout, property lookup, click dispatch.

    Args:
        on_action: Callable taking the action name — ``"open"``,
            ``"check"`` or ``"quit"``.
    """

    def __init__(self, on_action) -> None:
        self._on_action = on_action
        self._items = build_menu()
        self._by_id = {item_id: props for item_id, props in self._items}

    def layout(self, recursion_depth: int = 1) -> list[tuple[int, dict]]:
        """The menu tree, flat at depth 1 (no submenus exist)."""
        return list(self._items)

    def get(self, item_id: int) -> dict:
        """The properties dict of *item_id*, or {} when unknown."""
        return dict(self._by_id.get(item_id, {}))

    def event(self, item_id: int, event_id: str) -> bool:
        """Handle a dbusmenu ``Event``; True when the event was consumed.

        ``"clicked"`` on the action items dispatches to the callback.
        """
        if event_id != "clicked":
            return False
        actions = {
            ID_OPEN: "open",
            ID_CHECK_NOW: "check",
            ID_QUIT: "quit",
        }
        action = actions.get(item_id)
        if action is None:
            return False
        self._on_action(action)
        return True


class DbusMenu(object):
    """The exported dbusmenu object (wire only; logic lives in MenuModel).

    Args:
        bus: The session bus connection.
        model: The :class:`MenuModel` backing the object.
        service_name: The owning service name, used by the SNI ``Menu``
            property (the applet owns it before exporting).
    """

    def __init__(self, bus, model: MenuModel, *, service_name: str) -> None:
        self._model = model
        self._service_name = service_name
        self._object = None
        self._export(bus)

    def _export(self, bus) -> None:
        import dbus.service

        class _MenuObject(dbus.service.Object):
            def __init__(self, menu: DbusMenu) -> None:
                super().__init__(bus, MENU_PATH)
                self._menu = menu
                self._props = {
                    "version": dbus.Int32(3),
                    "text-direction": dbus.String("ltr"),
                    "status": dbus.String("normal"),
                    "icon-theme-path": dbus.String(""),
                }

            @dbus.service.method(
                "org.freedesktop.DBus.Properties", in_signature="ss",
                out_signature="v")
            def Get(self, interface_name: str, property_name: str):
                if interface_name == MENU_IFACE and property_name in self._props:
                    return self._props[property_name]
                raise dbus.exceptions.DBusException(
                    f"No such property {interface_name}.{property_name}",
                    name="org.freedesktop.DBus.Error.InvalidArgs")

            @dbus.service.method(
                "org.freedesktop.DBus.Properties", in_signature="s",
                out_signature="a{sv}")
            def GetAll(self, interface_name: str):
                if interface_name != MENU_IFACE:
                    return {}
                return self._props

            @dbus.service.method(
                MENU_IFACE, in_signature="iias", out_signature="ia(ia{sv})")
            def GetLayout(self, parent_id: int, recursion_depth: int,
                          property_names: list) -> tuple:
                layout = self._menu._model.layout(recursion_depth)
                return dbus.Int32(1), dbus.Array(
                    [(dbus.Int32(item_id), props)
                     for item_id, props in layout],
                    signature="(ia{sv})")

            @dbus.service.method(
                MENU_IFACE, in_signature="ias", out_signature="a{sv}")
            def Get(self, item_id: int, property_names: list):
                return self._menu._model.get(item_id)

            @dbus.service.method(MENU_IFACE, in_signature="isvu",
                                 out_signature="", sender_keyword="sender")
            def Event(self, item_id: int, event_id: str, data: object,
                      timestamp: int, sender=None) -> None:
                self._menu._model.event(int(item_id), event_id)

        self._object = _MenuObject(self)
