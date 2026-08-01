"""Unit tests for the declarative UI model (manaradix.ui.model)."""

from manaradix.ui.model import PageModel, Widget


class TestPageModel:
    def test_empty_sections(self) -> None:
        assert PageModel().sections == []

    def test_stores_sections(self) -> None:
        model = PageModel(sections=[{"title": "A", "fields": []}])
        assert model.sections == [{"title": "A", "fields": []}]


class TestWidgetDescriptors:
    def test_label(self) -> None:
        assert Widget.label(id="l1", text="hi") == {
            "widget": "label", "id": "l1", "text": "hi",
        }

    def test_button(self) -> None:
        cb = lambda: None  # noqa: E731
        assert Widget.button(id="b1", label="Go", callback=cb) == {
            "widget": "button", "id": "b1", "label": "Go", "callback": cb,
        }

    def test_tree(self) -> None:
        rows = [{"ID": "s1"}]
        on_select = lambda row: None  # noqa: E731
        desc = Widget.tree(id="t1", rows=rows, selectable=True, on_select=on_select)
        assert desc["widget"] == "tree"
        assert desc["id"] == "t1"
        assert desc["rows"] == rows
        assert desc["selectable"] is True
        assert desc["on_select"] is on_select
        # Rows are defensively copied
        rows.append({"ID": "s2"})
        assert desc["rows"] == [{"ID": "s1"}]

    def test_text(self) -> None:
        assert Widget.text(id="t1", label="Desc", value="x") == {
            "widget": "text", "id": "t1", "label": "Desc", "value": "x",
        }

    def test_checkbox(self) -> None:
        on_change = lambda: None  # noqa: E731
        assert Widget.checkbox(
            id="c1", label="On", checked=True, on_change=on_change,
        ) == {
            "widget": "checkbox", "id": "c1", "label": "On",
            "checked": True, "on_change": on_change,
        }

    def test_dropdown(self) -> None:
        on_change = lambda: None  # noqa: E731
        desc = Widget.dropdown(
            id="d1", label="Pick", options=["a", "b"], selected="a",
            on_change=on_change,
        )
        assert desc["widget"] == "dropdown"
        assert desc["options"] == ["a", "b"]
        assert desc["selected"] == "a"
        assert desc["on_change"] is on_change

    def test_spinner(self) -> None:
        on_change = lambda: None  # noqa: E731
        desc = Widget.spinner(
            id="s1", label="N", value=5, min_val=0, max_val=999,
            on_change=on_change,
        )
        assert desc["widget"] == "spinner"
        assert desc["min_val"] == 0
        assert desc["max_val"] == 999
        assert desc["value"] == 5
        assert desc["on_change"] is on_change
