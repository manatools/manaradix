"""Tests for the dbusmenu context menu (pure model)."""

from manaradix.tray.dbus_menu import (
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
        assert ids == [ID_OPEN, ID_CHECK_NOW, ID_SEPARATOR, ID_QUIT]

    def test_separator_props(self):
        props = dict(build_menu()).get(ID_SEPARATOR)
        assert props["type"] == "separator"

    def test_translated_labels_used_by_default(self):
        items = build_menu()
        open_props = [props for i, props in items if i == ID_OPEN][0]
        assert open_props["label"]  # translated, so non-empty

    def test_label_overrides(self):
        items = build_menu(open_label="Apri", check_label="Controlla",
                           quit_label="Esci")
        props = {i: p for i, p in items}
        assert props[ID_OPEN]["label"] == "Apri"
        assert props[ID_CHECK_NOW]["label"] == "Controlla"
        assert props[ID_QUIT]["label"] == "Esci"


class TestMenuModel:
    def test_layout_flat(self):
        model = MenuModel(on_action=lambda action: None)
        assert [i for i, _p in model.layout()] == [
            ID_OPEN, ID_CHECK_NOW, ID_SEPARATOR, ID_QUIT]

    def test_get_known_and_unknown(self):
        model = MenuModel(on_action=lambda action: None)
        assert model.get(ID_OPEN)["label"]
        assert model.get(9999) == {}

    def test_event_clicked_dispatches(self):
        dispatched = []
        model = MenuModel(on_action=dispatched.append)
        assert model.event(ID_OPEN, "clicked") is True
        assert model.event(ID_CHECK_NOW, "clicked") is True
        assert model.event(ID_QUIT, "clicked") is True
        assert dispatched == ["open", "check", "quit"]

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
