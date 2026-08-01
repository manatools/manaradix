"""Renderer tests driven by a fake widget factory (no display needed).

The renderer is duck-typed against the manatools YUI API, and the event /
table-item classes from ``manatools.aui.yui_common`` are pure Python, so a
scripted fake factory can exercise the full widget build + event loop.
"""

from manatools.aui.yui_common import YCancelEvent, YEventReason, YItem, YWidgetEvent

from manaradix.module import _build_parser
from manaradix.ui.model import PageModel, Widget
from manaradix.ui.renderer import AuiRenderer


# ---------------------------------------------------------------------------
# Fake factory
# ---------------------------------------------------------------------------


class FakeWidget:
    """Mirrors the subset of the YWidget API the renderer touches."""

    def __init__(self, kind, parent=None, label=""):
        self.kind = kind
        self.parent = parent
        self.children = []
        self._items = []
        self._selected = None
        self._value = ""
        self._id = None
        self.notify_enabled = False
        self.stretch_horiz = False
        self.label_text = label

    def id(self):
        return self._id

    def setNotify(self, on=True):
        self.notify_enabled = on

    def setStretchable(self, dim, new_stretch):
        self.stretch_horiz = new_stretch

    def stretchable(self, dim):
        return self.stretch_horiz

    def setValue(self, value):
        self._value = value

    def value(self):
        return self._value

    def addItem(self, item):
        if isinstance(item, str):
            item = YItem(item)
        self._items.append(item)
        if item.selected():
            self._selected = item

    def select(self, item):
        self._selected = item

    def selectedItem(self):
        return self._selected

    def selectedItems(self):
        return [self._selected] if self._selected is not None else []

    def deleteChildren(self):
        self.children = []

    def showChild(self):
        pass


class ScriptedBackend:
    """Backend widget recording resize() calls with a stateful sizeHint.

    ``sizeHint`` returns progressively taller hints per call so tests can
    assert the window is re-fitted to the *current* page's hint after each
    rebuild, not just that a fit happened.
    """

    def __init__(self):
        self.resizes = []
        self._hint_calls = 0

    def sizeHint(self):
        self._hint_calls += 1
        return (300, 100 * self._hint_calls)

    def resize(self, size):
        self.resizes.append(size)

    def layout(self):
        return self

    def activate(self):
        pass

    def isMaximized(self):
        return False

    def isFullScreen(self):
        return False


class FakeDialog(FakeWidget):
    """Dialog whose event queue is scripted by the test."""

    def __init__(self, factory, script):
        super().__init__("dialog")
        self.factory = factory
        self.script = list(script)
        self.open_called = False
        self.destroy_called = False
        self.backend = ScriptedBackend()

    def open(self):
        self.open_called = True

    def destroy(self):
        self.destroy_called = True

    def get_backend_widget(self):
        return self.backend

    def waitForEvent(self, timeout_millisec=0):
        if not self.script:
            raise RuntimeError("event script exhausted")
        item = self.script.pop(0)
        # Callables resolve lazily so they can reference widgets created
        # during the render.
        return item(self.factory) if callable(item) else item


class FakeFactory:
    """Records every create* call and returns scripted fake widgets."""

    def __init__(self, script):
        self.script = script
        self.widgets = []
        self.dialog = None

    def _create(self, kind, parent=None, **kw):
        if kind == "dialog":
            widget = FakeDialog(self, self.script)
            self.dialog = widget
        else:
            widget = FakeWidget(kind, parent, kw.get("label", ""))
            if parent is not None:
                parent.children.append(widget)
        self.widgets.append(widget)
        return widget

    def createMainDialog(self, color_mode=None):
        return self._create("dialog")

    def createVBox(self, parent):
        return self._create("vbox", parent)

    def createHeading(self, parent, label):
        return self._create("heading", parent, label=label)

    def createLabel(self, parent, text):
        return self._create("label", parent, label=text)

    def createPushButton(self, parent, label):
        return self._create("pushbutton", parent, label=label)

    def createCheckBox(self, parent, label, is_checked=False):
        return self._create("checkbox", parent, label=label)

    def createInputField(self, parent, label):
        return self._create("inputfield", parent, label=label)

    def createComboBox(self, parent, label, editable=False):
        return self._create("combobox", parent, label=label)

    def createIntField(self, parent, label, minVal, maxVal, initialVal):
        return self._create("intfield", parent, label=label)

    def createTable(self, parent, header):
        return self._create("table", parent)

    def createDumbTab(self, parent):
        return self._create("dumbtab", parent)

    def createReplacePoint(self, parent):
        return self._create("replacepoint", parent)

    def createFrame(self, parent, label):
        return self._create("frame", parent, label=label)


def widgets_of(factory, kind):
    return [w for w in factory.widgets if w.kind == kind]


# ---------------------------------------------------------------------------
# Test pages
# ---------------------------------------------------------------------------


class PageA:
    title = "Page A"

    def __init__(self):
        self.clicks = 0
        self.changed = 0
        self.selected = None
        self.last_drop = None
        self.build_count = 0

    def build_model(self):
        self.build_count += 1
        return PageModel(sections=[
            {
                "title": "Actions",
                "fields": [
                    Widget.button(id="btn_go", label="Go", callback=self.on_go),
                    Widget.checkbox(
                        id="chk", label="On", checked=True, on_change=self.on_toggle,
                    ),
                    Widget.dropdown(
                        id="dd", label="Pick", options=["a", "b"],
                        selected="a", on_change=self.on_drop,
                    ),
                    Widget.spinner(
                        id="sp", label="N", value=5, min_val=0, max_val=10,
                        on_change=self.on_spin,
                    ),
                    Widget.text(id="txt", label="Desc", value="hello"),
                    Widget.label(id="lbl", text="some text"),
                    Widget.tree(
                        id="tbl",
                        rows=[
                            {"ID": "s1", "Type": "manual"},
                            {"ID": "s2", "Type": "timed"},
                        ],
                        selectable=True,
                        on_select=self.on_select,
                    ),
                ],
            },
        ])

    def on_go(self):
        self.clicks += 1

    def on_toggle(self):
        self.changed += 1

    def on_drop(self, value=None):
        self.changed += 1
        self.last_drop = value

    def on_spin(self):
        self.changed += 1

    def on_select(self, row):
        self.selected = row


class PageB:
    title = "Page B"

    def build_model(self):
        return PageModel(sections=[{"title": "Info", "fields": []}])


def make_renderer(pages, script):
    factory = FakeFactory(script)
    return AuiRenderer(pages, factory=factory), factory


# ---------------------------------------------------------------------------
# Widget build
# ---------------------------------------------------------------------------


class TestBuild:
    def test_full_widget_tree(self) -> None:
        page = PageA()
        renderer, factory = make_renderer([page], [YCancelEvent()])
        renderer.run()

        assert factory.dialog.open_called
        assert factory.dialog.destroy_called

        # Replace point got a vbox with heading + sections
        rp = widgets_of(factory, "replacepoint")[0]
        assert rp.children and rp.children[0].kind == "vbox"

        # Every descriptor id was assigned to the matching widget
        assert widgets_of(factory, "pushbutton")[0]._id == "btn_go"
        assert widgets_of(factory, "checkbox")[0]._id == "chk"
        assert widgets_of(factory, "combobox")[0]._id == "dd"
        assert widgets_of(factory, "intfield")[0]._id == "sp"
        assert widgets_of(factory, "inputfield")[0]._id == "txt"
        assert widgets_of(factory, "table")[0]._id == "tbl"

    def test_tree_maps_to_table(self) -> None:
        page = PageA()
        renderer, factory = make_renderer([page], [YCancelEvent()])
        renderer.run()

        table = widgets_of(factory, "table")[0]
        header = renderer._table_headers[table]
        assert header.columns() == 2
        assert header.header(0) == "ID"
        assert header.header(1) == "Type"
        assert len(table._items) == 2
        assert table._items[0].cell(0).label() == "s1"
        assert table._items[1].cell(1).label() == "timed"

    def test_selectable_wires_handler(self) -> None:
        page = PageA()
        renderer, factory = make_renderer([page], [YCancelEvent()])
        renderer.run()
        table = widgets_of(factory, "table")[0]
        assert table.notify_enabled is True
        assert renderer._handlers["tbl"][0] == "tree"

    def test_not_selectable_registers_no_handler(self) -> None:
        class Page:
            title = "P"

            def build_model(self):
                return PageModel(sections=[{
                    "title": "S",
                    "fields": [Widget.tree(id="t", rows=[{"A": "1"}])],
                }])

        renderer, factory = make_renderer([Page()], [YCancelEvent()])
        renderer.run()
        assert "t" not in renderer._handlers

    def test_table_stretches_horizontally(self) -> None:
        page = PageA()
        renderer, factory = make_renderer([page], [YCancelEvent()])
        renderer.run()
        table = widgets_of(factory, "table")[0]
        assert table.stretch_horiz is True  # fills the section frame width


# ---------------------------------------------------------------------------
# Event dispatch
# ---------------------------------------------------------------------------


def btn_activated(factory):
    return YWidgetEvent(widgets_of(factory, "pushbutton")[0], YEventReason.Activated)


def chk_changed(factory):
    return YWidgetEvent(widgets_of(factory, "checkbox")[0], YEventReason.ValueChanged)


def dd_changed(factory):
    return YWidgetEvent(widgets_of(factory, "combobox")[0], YEventReason.SelectionChanged)


def sp_changed(factory):
    return YWidgetEvent(widgets_of(factory, "intfield")[0], YEventReason.ValueChanged)


def table_selected(factory):
    table = widgets_of(factory, "table")[0]
    table.select(table._items[1])
    return YWidgetEvent(table, YEventReason.SelectionChanged)


def tab_switched(factory):
    tabs = widgets_of(factory, "dumbtab")[0]
    tabs.select(tabs._items[1])
    return YWidgetEvent(tabs, YEventReason.Activated)


class TestDispatch:
    def test_button_calls_callback_and_rebuilds(self) -> None:
        page = PageA()
        renderer, factory = make_renderer([page], [btn_activated, YCancelEvent()])
        renderer.run()
        assert page.clicks == 1
        assert page.build_count == 2  # initial render + rebuild after callback

    def test_checkbox_on_change(self) -> None:
        page = PageA()
        renderer, factory = make_renderer([page], [chk_changed, YCancelEvent()])
        renderer.run()
        assert page.changed == 1

    def test_dropdown_on_change(self) -> None:
        page = PageA()
        renderer, factory = make_renderer([page], [dd_changed, YCancelEvent()])
        renderer.run()
        assert page.changed == 1
        # The callback receives the combo box's current value.
        assert page.last_drop == "a"

    def test_spinner_on_change(self) -> None:
        page = PageA()
        renderer, factory = make_renderer([page], [sp_changed, YCancelEvent()])
        renderer.run()
        assert page.changed == 1

    def test_tree_selection_reconstructs_row(self) -> None:
        page = PageA()
        renderer, factory = make_renderer([page], [table_selected, YCancelEvent()])
        renderer.run()
        assert page.selected == {"ID": "s2", "Type": "timed"}

    def test_tab_switch_renders_target_page(self) -> None:
        renderer, factory = make_renderer(
            [PageA(), PageB()], [tab_switched, YCancelEvent()]
        )
        renderer.run()
        headings = widgets_of(factory, "heading")
        assert headings[-1].label_text == "Page B"

    def test_cancel_closes_dialog(self) -> None:
        renderer, factory = make_renderer([PageA()], [YCancelEvent()])
        renderer.run()
        assert factory.dialog.destroy_called

    def test_unregistered_widget_is_ignored(self) -> None:
        page = PageA()
        stray = FakeWidget("stray")

        def stray_event(factory):
            return YWidgetEvent(stray, YEventReason.Activated)

        renderer, factory = make_renderer([page], [stray_event, YCancelEvent()])
        renderer.run()
        assert page.clicks == 0
        assert page.build_count == 1  # no rebuild

    def test_rebuild_preserves_tabs(self) -> None:
        renderer, factory = make_renderer(
            [PageA(), PageB()], [btn_activated, YCancelEvent()]
        )
        renderer.run()
        tabs = widgets_of(factory, "dumbtab")[0]
        assert len(tabs._items) == 2


# ---------------------------------------------------------------------------
# CLI frontend choices
# ---------------------------------------------------------------------------


class TestFrontendChoices:
    def test_web_is_rejected(self) -> None:
        import pytest

        with pytest.raises(SystemExit):
            _build_parser().parse_args(["--frontend", "web"])


# ---------------------------------------------------------------------------
# Window fit after rebuilds
# ---------------------------------------------------------------------------


class TestWindowFit:
    def test_fits_after_initial_render(self) -> None:
        renderer, factory = make_renderer([PageA()], [YCancelEvent()])
        renderer.run()
        assert factory.dialog.backend.resizes == [(300, 100)]

    def test_fits_after_button_rebuild(self) -> None:
        renderer, factory = make_renderer([PageA()], [btn_activated, YCancelEvent()])
        renderer.run()
        # initial render + rebuild after the callback; each fit uses the
        # current content's size hint
        assert factory.dialog.backend.resizes == [(300, 100), (300, 200)]

    def test_fits_after_tab_switch(self) -> None:
        renderer, factory = make_renderer(
            [PageA(), PageB()], [tab_switched, YCancelEvent()]
        )
        renderer.run()
        # the swap re-fits the window to the target page's hint
        assert factory.dialog.backend.resizes[-1] == (300, 200)

    def test_backend_without_fit_api_is_noop(self) -> None:
        class PlainDialog(FakeDialog):
            pass

        PlainDialog.get_backend_widget = None  # e.g. curses backend

        factory = FakeFactory([YCancelEvent()])
        factory.dialog = PlainDialog(factory, factory.script)
        # Make createMainDialog return the plain dialog
        factory.createMainDialog = lambda color_mode=None: factory.dialog

        renderer = AuiRenderer([PageA()], factory=factory)
        renderer.run()  # must not raise
        assert renderer._dialog is factory.dialog
