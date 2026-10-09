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
ID_ABOUT = 5

#: Layout revision announced by :meth:`GetLayout` and the
#: ``LayoutUpdated`` signal; static menu, so it never changes.
REVISION = 1


def _layout_item(item_id, props: dict, children) -> object:
    """One canonical dbusmenu layout node: ``(i, a{sv}, av)``.

    Imported lazily so :mod:`manaradix.tray.dbus_menu` stays importable
    without python3-dbus (the pure :class:`MenuModel` does not need it).
    """
    import dbus

    return dbus.Struct(
        (dbus.Int32(item_id),
         dbus.Dictionary(
             {dbus.String(k): v for k, v in props.items()},
             signature="sv"),
         dbus.Array(children, signature="v")),
        signature="ia{sv}av")


def build_menu(open_label: str = None, check_label: str = None,
               about_label: str = None,
               quit_label: str = None) -> list[tuple[int, dict]]:
    """The menu tree as ``(item_id, properties)`` pairs.

    Args:
        open_label: Label for the Open action (default: translated).
        check_label: Label for the Check now action.
        about_label: Label for the About action (default: translated).
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
        (ID_ABOUT, {
            "label": about_label or _("About"),
            "icon-name": "help-about",
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
            ``"check"``, ``"about"`` or ``"quit"``.
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
            ID_ABOUT: "about",
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

        # Both interfaces name a method ``Get``, and dbus-python resolves
        # an incoming call by *Python attribute name*: two ``Get`` defs in
        # one class body would leave only the last one, so
        # Properties.Get on /MenuBar would answer UnknownMethod.  The
        # Properties pair therefore lives in a base class — the MRO walk
        # in dbus.service._method_lookup skips the derived (dbusmenu)
        # ``Get`` when the requested interface does not match and finds
        # the base one.
        class _PropsObject(dbus.service.Object):
            """Properties surface for the menu (base of _MenuObject)."""

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

        class _MenuObject(_PropsObject):
            """The com.canonical.dbusmenu surface."""

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
                MENU_IFACE, in_signature="iias",
                out_signature="u(ia{sv}av)")
            def GetLayout(self, parent_id: int, recursion_depth: int,
                          property_names: list) -> tuple:
                """Canonical layout: ``(revision, root item)``.

                The out signature is ``u(ia{sv}av)`` — see
                ``com.canonical.dbusmenu.xml``: the ROOT item is a plain
                struct ``(id, props, av)``, and only its CHILDREN are
                variant-wrapped (plasma's demarshaller reads each child
                as a ``QDBusVariant`` holding another such struct).

                Both halves matter and each was wrong once.  A flat
                ``i``/``a(ia{sv})`` reply (through 0.2.2-2) left plasma
                unable to cast the children, and wrapping the root in a
                variant (``uv``, 0.2.2-3) made
                ``QDBusPendingReply<uint, DBusMenuLayoutItem>`` itself
                invalid, so the importer silently emitted an empty menu.
                Regression: ``test_getlayout_reply_shape``.
                """
                items = self._menu._model.layout(recursion_depth)
                if int(parent_id) != 0:
                    # a non-root parent has no children in this flat menu
                    for item_id, props in items:
                        if int(item_id) == int(parent_id):
                            return dbus.UInt32(REVISION), _layout_item(
                                item_id, props, [])
                    return dbus.UInt32(REVISION), _layout_item(0, {}, [])
                children = dbus.Array(
                    [_layout_item(item_id, props, [])
                     for item_id, props in items],
                    signature="v")
                root_props = {"children-display": dbus.String("submenu")}
                return dbus.UInt32(REVISION), _layout_item(
                    0, root_props, children)

            @dbus.service.method(
                MENU_IFACE, in_signature="ias", out_signature="a{sv}")
            def Get(self, item_id: int, property_names: list):
                return self._menu._model.get(item_id)

            @dbus.service.method(
                MENU_IFACE, in_signature="aias", out_signature="a(ia{sv})")
            def GetGroupProperties(self, item_ids: list,
                                   property_names: list) -> object:
                ids = [int(i) for i in item_ids] or [
                    item_id for item_id, _props in self._menu._model.layout()]
                return dbus.Array(
                    [dbus.Struct(
                        (dbus.Int32(i),
                         dbus.Dictionary(
                             {dbus.String(k): v
                              for k, v in self._menu._model.get(i).items()},
                             signature="sv")),
                        signature="ia{sv}")
                     for i in ids],
                    signature="(ia{sv})")

            @dbus.service.method(MENU_IFACE, in_signature="i",
                                 out_signature="b")
            def AboutToShow(self, item_id: int) -> bool:
                # The layout is static, so the host never has to re-read
                # it before popping the menu.  Hosts (libdbusmenu-qt,
                # Plasma) call this before every popup; answering
                # UnknownMethod makes them log and sometimes give up.
                return dbus.Boolean(False)

            @dbus.service.method(MENU_IFACE, in_signature="isvu",
                                 out_signature="", sender_keyword="sender")
            def Event(self, item_id: int, event_id: str, data: object,
                      timestamp: int, sender=None) -> None:
                self._menu._model.event(int(item_id), event_id)

            @dbus.service.signal(MENU_IFACE, signature="ui")
            def LayoutUpdated(self, revision: int, parent_id: int) -> None:
                return None

            @dbus.service.signal(MENU_IFACE, signature="ib")
            def ItemPropertyUpdated(self, item_id: int,
                                    properties_changed: bool) -> None:
                return None

        self._object = _MenuObject(self)
