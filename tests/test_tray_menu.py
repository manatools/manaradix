"""Tests for the dbusmenu context menu (pure model)."""

from unittest.mock import MagicMock

import pytest

from manaradix.tray.dbus_menu import (
    DbusMenu,
    ID_ABOUT,
    ID_CHECK_NOW,
    ID_OPEN,
    ID_QUIT,
    ID_SEPARATOR,
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
