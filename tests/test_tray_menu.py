"""Tests for the dbusmenu context menu (pure model)."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

dbus = pytest.importorskip("dbus", reason="python3-dbus")

REPO_ROOT = str(Path(__file__).resolve().parent.parent)

from manaradix.tray.dbus_menu import (  # noqa: E402
    DbusMenu,
    ID_ABOUT,
    ID_CHECK_NOW,
    ID_OPEN,
    ID_QUIT,
    ID_SEPARATOR,
    MENU_IFACE,
    MenuModel,
    build_menu,
)


class TestBuildMenu:
    def test_layout_order(self):
        items = build_menu()
        ids = [item_id for item_id, _props in items]
        assert ids == [ID_OPEN, ID_CHECK_NOW, ID_ABOUT, ID_SEPARATOR, ID_QUIT]

    def test_separator_props(self):
        props = dict(build_menu()).get(ID_SEPARATOR)
        assert props["type"] == "separator"

    def test_translated_labels_used_by_default(self):
        items = build_menu()
        open_props = [props for i, props in items if i == ID_OPEN][0]
        assert open_props["label"]  # translated, so non-empty
        about_props = [props for i, props in items if i == ID_ABOUT][0]
        assert about_props["label"]  # translated, so non-empty

    def test_label_overrides(self):
        items = build_menu(open_label="Apri", check_label="Controlla",
                           about_label="Informazioni", quit_label="Esci")
        props = {i: p for i, p in items}
        assert props[ID_OPEN]["label"] == "Apri"
        assert props[ID_CHECK_NOW]["label"] == "Controlla"
        assert props[ID_ABOUT]["label"] == "Informazioni"
        assert props[ID_QUIT]["label"] == "Esci"


class TestMenuModel:
    def test_layout_flat(self):
        model = MenuModel(on_action=lambda action: None)
        assert [i for i, _p in model.layout()] == [
            ID_OPEN, ID_CHECK_NOW, ID_ABOUT, ID_SEPARATOR, ID_QUIT]

    def test_get_known_and_unknown(self):
        model = MenuModel(on_action=lambda action: None)
        assert model.get(ID_OPEN)["label"]
        assert model.get(9999) == {}

    def test_event_clicked_dispatches(self):
        dispatched = []
        model = MenuModel(on_action=dispatched.append)
        assert model.event(ID_OPEN, "clicked") is True
        assert model.event(ID_CHECK_NOW, "clicked") is True
        assert model.event(ID_ABOUT, "clicked") is True
        assert model.event(ID_QUIT, "clicked") is True
        assert dispatched == ["open", "check", "about", "quit"]

    def test_event_non_clicked_ignored(self):
        dispatched = []
        model = MenuModel(on_action=dispatched.append)
        assert model.event(ID_OPEN, "hovered") is False
        assert dispatched == []

    def test_event_unknown_item_ignored(self):
        dispatched = []
        model = MenuModel(on_action=dispatched.append)
        assert model.event(9999, "clicked") is False
        assert dispatched == []


class TestExport:
    def test_real_dbus_decorator_signatures(self):
        """Regression: dbus-python validates ``in_signature`` against the
        positional parameters at decoration time; 0.2.0 shipped a
        GetLayout with ``ias`` (2 complete types) for a 3-parameter
        method, so the applet crashed at startup on any real bus.  Real
        decorators only run with python3-dbus installed."""
        pytest.importorskip("dbus")
        menu = DbusMenu(
            MagicMock(), MenuModel(on_action=lambda action: None),
            service_name="org.kde.StatusNotifierItem-1-1")
        assert menu._object is not None

    def test_both_get_methods_dispatch_by_interface(self):
        """Regression: dbus-python resolves an incoming call by Python
        attribute name, so defining ``Get`` for both Properties and
        dbusmenu in one class body left only the dbusmenu one and
        Properties.Get on /MenuBar answered UnknownMethod.  The host
        reads the menu's ``status``/``version`` through that call before
        popping the menu up."""
        pytest.importorskip("dbus")
        from dbus.service import _method_lookup

        menu = DbusMenu(
            MagicMock(), MenuModel(on_action=lambda action: None),
            service_name="org.kde.StatusNotifierItem-1-1")
        obj = menu._object

        def resolve(interface, name):
            candidate, parent = _method_lookup(obj, name, interface)
            assert parent._dbus_interface == interface
            return candidate

        props_get = resolve("org.freedesktop.DBus.Properties", "Get")
        assert props_get(obj, MENU_IFACE, "version") == 3
        assert resolve("org.freedesktop.DBus.Properties", "GetAll")(
            obj, MENU_IFACE)["status"] == "normal"

        menu_get = resolve(MENU_IFACE, "Get")
        assert menu_get(obj, ID_OPEN, [])["label"]

    def test_host_popup_methods_are_exported(self):
        """Hosts call GetGroupProperties + AboutToShow before every
        popup; the icon is a menu button now, so an UnknownMethod there
        costs the user the menu."""
        pytest.importorskip("dbus")
        menu = DbusMenu(
            MagicMock(), MenuModel(on_action=lambda action: None),
            service_name="org.kde.StatusNotifierItem-1-1")
        obj = menu._object

        revision, root = obj.GetLayout(0, -1, [])
        assert int(revision) >= 1
        _root_id, _root_props, children = root
        assert [int(child[0]) for child in children] == [
            ID_OPEN, ID_CHECK_NOW, ID_ABOUT, ID_SEPARATOR, ID_QUIT]
        # a non-root parent returns that item, childless
        _rev, node = obj.GetLayout(ID_OPEN, -1, [])
        assert int(node[0]) == ID_OPEN
        assert list(node[2]) == []

        group = obj.GetGroupProperties([], [])
        assert [int(i) for i, _props in group] == [
            ID_OPEN, ID_CHECK_NOW, ID_ABOUT, ID_SEPARATOR, ID_QUIT]
        subset = obj.GetGroupProperties([ID_ABOUT], [])
        assert [int(i) for i, _props in subset] == [ID_ABOUT]

        assert bool(obj.AboutToShow(0)) is False  # static layout

    def test_getlayout_reply_shape(self):
        """Regression: the GetLayout reply signature must be exactly
        ``u(ia{sv}av)`` as ``com.canonical.dbusmenu.xml`` declares — a
        uint32 revision and the root item as a PLAIN STRUCT whose
        children are each VARIANT-wrapped.

        Both halves broke once, each costing the menu:

        * through 0.2.2-2 the reply was ``i`` + ``a(ia{sv})``: plasma
          could not cast the children;
        * in 0.2.2-3 the root was variant-wrapped (``uv``), which makes
          ``QDBusPendingReply<uint, DBusMenuLayoutItem>`` invalid, so
          ``slotGetLayoutFinished`` bailed out and emitted an empty menu
          with nothing logged.

        dbus-python applies variant wrapping at marshalling time, so the
        shape is asserted through a real bus round trip in a subprocess:
        an in-process call bypasses the wire entirely.
        """
        pytest.importorskip("dbus")
        import subprocess
        import sys
        import time

        server = subprocess.Popen(
            [sys.executable, "-c", (
                "import sys; sys.path.insert(0, %r)\n"
                "import dbus\n"
                "from dbus.mainloop.glib import DBusGMainLoop\n"
                "DBusGMainLoop(set_as_default=True)\n"
                "from gi.repository import GLib\n"
                "bus = dbus.SessionBus()\n"
                "bus.request_name('test.manaradix.menushape', "
                "dbus.bus.NAME_FLAG_DO_NOT_QUEUE)\n"
                "from manaradix.tray.dbus_menu import DbusMenu, MenuModel\n"
                "DbusMenu(bus, MenuModel(on_action=lambda a: None), "
                "service_name='test.manaradix.menushape')\n"
                "GLib.MainLoop().run()\n"
            ) % REPO_ROOT],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            time.sleep(2.0)
            from dbus.mainloop.glib import DBusGMainLoop

            DBusGMainLoop(set_as_default=True)
            bus = dbus.SessionBus()
            revision, layout = bus.call_blocking(
                "test.manaradix.menushape", "/MenuBar",
                MENU_IFACE, "GetLayout", "iias", [0, -1, []])
            assert isinstance(revision, dbus.UInt32)
            assert isinstance(layout, dbus.Struct)
            # the ROOT is a plain struct, never a variant: a variant here
            # invalidates plasma's QDBusPendingReply and kills the menu
            assert layout.variant_level == 0
            item_id, _props, children = layout
            assert int(item_id) == 0
            # ...while every CHILD is variant-wrapped (the "av" in the
            # signature), which is how plasma reads each child back
            assert all(child.variant_level == 1 for child in children)
            assert [int(child[0]) for child in children] == [
                ID_OPEN, ID_CHECK_NOW, ID_ABOUT, ID_SEPARATOR, ID_QUIT]
        finally:
            server.terminate()
            server.wait(timeout=10)
