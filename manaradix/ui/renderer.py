"""Render manaradix declarative page models onto the manatools YUI API.

The released python-manatools exposes a libyui-style API (``YUI``,
``YWidgetFactory``, ``YDialog``) rather than a PageModel/Widget abstraction.
This module bridges the two: it walks the
:class:`~manaradix.ui.model.PageModel` descriptors produced by the pages and
builds real YUI widgets via ``YUI.widgetFactory()``, then drives the dialog
event loop, dispatching widget events to the callbacks stored on the
descriptors.

Notes on the real API (manatools 0.99.3):
- ``manatools.aui`` itself is an empty package; everything lives in
  ``manatools.aui.yui`` (``YUI``) and ``manatools.aui.yui_common``
  (widget bases, events, ``YTableHeader``/``YTableItem``).
- Backend selection: the ``MUI_BACKEND`` env var (qt/gtk/ncurses) or
  auto-detection — there is no web backend.
- ``YWidget`` has no ``setId()``; ids are assigned by setting ``_id``
  directly.
- ``YDumbTab`` holds exactly one child; page bodies are swapped into a
  ``YReplacePoint`` on tab switches and after state-changing callbacks.

The event loop is duck-typed against the real classes (``eventType()``,
``reason()``, ``widget().id()``), so tests can drive it with fakes and the
pure-Python event objects from ``yui_common``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from manaradix.core.assets import logo_path
from manaradix.i18n import _

logger = logging.getLogger(__name__)

_TABS_ID = "main_tabs"

# Widget kinds wired to callbacks, and the event reasons each backend posts.
# Verified against manatools 0.99.3: pushbutton -> Activated; checkbox /
# intfield -> ValueChanged; combobox -> SelectionChanged (ValueChanged when
# editable); table selection -> SelectionChanged; dumbtab -> Activated.
_BUTTON_REASONS = ("Activated",)
_CHANGE_REASONS = ("Activated", "ValueChanged", "SelectionChanged")
_SELECT_REASONS = ("SelectionChanged",)


class AuiRenderer:
    """Renders a list of pages onto a YUI main dialog with tab navigation.

    Each page in *pages* must expose ``title`` and ``build_model()``
    (returning a :class:`~manaradix.ui.model.PageModel`).  Widget events
    are dispatched to the callbacks stored on the field descriptors; after
    any dispatched callback the current page is re-rendered so state
    changes (e.g. a deleted snapshot) appear immediately.
    """

    def __init__(self, pages, *, factory=None) -> None:
        self._pages = list(pages)
        # Every page gets a renderer reference so its callbacks can
        # navigate (e.g. the dashboard's Create Snapshot quick action
        # jumps to the Snapshots page).
        for page in self._pages:
            page.renderer = self
        # Injectable factory for tests; defaults to the real YUI factory.
        self._factory = factory
        # The factory actually in use while the dialog runs (set in
        # run()); None until then, so navigate() before run() is a no-op.
        self._active_factory = None
        self._handlers: dict[str, tuple[str, Callable]] = {}
        self._table_headers: dict[Any, Any] = {}
        self._tabs = None
        self._replace_point = None
        self._dialog = None
        self._current_index = 0
        # Widgets whose vertical size policy must be pinned to Fixed once
        # their Qt backend widgets exist (see _apply_pinned_policies).
        self._pinned: list = []
        self._opened = False

    # -- Lifecycle ----------------------------------------------------------

    def run(self) -> None:
        """Build the main dialog and run the event loop until closed."""
        from manatools.aui.yui import YUI  # lazy: keep CLI importable without manatools

        factory = self._factory or YUI.widgetFactory()
        self._active_factory = factory

        if self._factory is None:
            try:
                YUI.application().setApplicationTitle("Manaradix")
            except Exception as exc:  # cosmetic only
                logger.debug("Could not set application title: %s", exc)
            self._set_app_icon()

        dialog = factory.createMainDialog()
        self._dialog = dialog
        root = factory.createVBox(dialog)
        heading = factory.createHeading(root, "Manaradix")
        self._pin_vertical(heading)

        tabs = factory.createDumbTab(root)
        tabs._id = _TABS_ID  # noqa: SLF001 -- YWidget has no setId()
        tabs.setNotify(True)
        for page in self._pages:
            tabs.addItem(page.title)  # strings are wrapped into YItem
        self._tabs = tabs

        # The DumbTab accepts a single content child; page bodies are
        # swapped into this replace point on tab switch / refresh.
        replace_point = factory.createReplacePoint(tabs)
        self._replace_point = replace_point

        if self._pages:
            self._show_page(factory, 0)
        # _show_page resets the pinned list; re-register the persistent
        # main heading so its policy is applied with the rest after open.
        self._pin_vertical(heading)

        dialog.open()
        self._opened = True
        self._apply_pinned_policies()
        # Open at a size that fits the largest tab, so switching tabs
        # never resizes the window (the Qt backend starts at the first
        # page's minimum and only grows).
        self._startup_fit_to_largest(factory)
        try:
            while True:
                event = dialog.waitForEvent(0)
                event_type = event.eventType().name
                if event_type == "CancelEvent":
                    break
                if event_type != "WidgetEvent":
                    continue
                widget = event.widget()
                widget_id = widget.id()
                if widget_id == _TABS_ID:
                    self._switch_tab(factory)
                    continue
                self._dispatch(factory, event)
        finally:
            try:
                dialog.destroy()
            except Exception as exc:  # teardown must not mask real errors
                logger.debug("Dialog teardown failed: %s", exc)

    # -- Rendering ----------------------------------------------------------

    def _show_page(self, factory, index: int) -> None:
        """Render one page's model into the replace point."""
        page = self._pages[index]
        replace_point = self._replace_point

        replace_point.deleteChildren()
        self._handlers.clear()
        self._table_headers.clear()
        self._pinned.clear()

        # Lazy: keep manatools optional for CLI-only imports.
        from manatools.aui.yui_common import YUIDimension

        # No page heading: the tab bar already carries the page title.
        content = factory.createVBox(replace_point)

        model = page.build_model()
        rows = getattr(model, "rows", None)
        if rows is not None:
            self._render_grid(factory, content, model)
        else:
            columns = getattr(model, "columns", 1)
            if columns > 1:
                # Side-by-side sections (HBox of VBoxes) keep dense pages
                # from stretching the window past small screens.  A
                # section pins itself with a "column" key; unpinned
                # sections fill the shortest column so heights stay
                # balanced.
                row = factory.createHBox(content)
                column_boxes = [factory.createVBox(row) for _ in range(columns)]
                heights = [0] * columns
                for section in model.sections:
                    col = section.get("column")
                    if col is None:
                        col = min(range(columns), key=heights.__getitem__)
                    else:
                        col = max(0, min(col, columns - 1))
                    self._render_section(factory, column_boxes[col], section)
                    heights[col] += len(section.get("fields", []))
                # Top-align each column's content: the row stretches the
                # columns to its full height, so a stretchable spacer in
                # every column absorbs the leftover instead of inflating
                # the frames (and centering their controls).
                for column_box in column_boxes:
                    factory.createSpacing(
                        column_box, YUIDimension.YD_VERT, stretchable=True
                    )
            else:
                for section in model.sections:
                    self._render_section(factory, content, section)

            # Extra vertical space from a grown window collects below
            # the page content instead of inflating the containers in
            # place: the stretchable spacer is the only child that
            # absorbs it.  Grid pages skip this — their rows share the
            # height equally instead (each capped at 25% with 4 rows).
            factory.createSpacing(content, YUIDimension.YD_VERT, stretchable=True)

        replace_point.showChild()
        self._current_index = index
        if self._opened:
            # Backend widgets exist now (showChild walks the tree); apply
            # the Qt size policies for the fresh page widgets.
            self._apply_pinned_policies()
        self._fit_window()

    def _render_grid(self, factory, parent, model) -> None:
        """Render a row/column grid (``PageModel.rows``).

        Each row is an HBox of cells; a cell is a section id or a list of
        section ids stacked in that cell.  One-cell rows span the full
        width.  Every cell is horizontally stretchable, so two-cell rows
        split 50/50 and full-width rows fill the window.  Sections are
        looked up by their ``"id"`` key; unknown ids are skipped.
        """
        from manatools.aui.yui_common import YUIDimension

        by_id = {s.get("id"): s for s in model.sections}
        for row in model.rows:
            row_box = factory.createHBox(parent)
            # Equal vertical weights make every row share the window
            # height equally — with the dashboard's 5 rows that caps
            # each at 20%, so a maximized window never overflows the
            # monitor (rows compress instead; the tables scroll).
            weight = getattr(row_box, "setWeight", None)
            if callable(weight):
                weight(YUIDimension.YD_VERT, 1)
            for cell in row:
                cell_box = factory.createVBox(row_box)
                stretch = getattr(cell_box, "setStretchable", None)
                if callable(stretch):
                    stretch(YUIDimension.YD_HORIZ, True)
                # Equal weights split the row exactly 50/50 (the Qt
                # backend maps YBox weights to QBoxLayout stretch
                # factors); stretchable alone would split by sizeHint.
                weight = getattr(cell_box, "setWeight", None)
                if callable(weight):
                    weight(YUIDimension.YD_HORIZ, 1)
                section_ids = cell if isinstance(cell, list) else [cell]
                for section_id in section_ids:
                    section = by_id.get(section_id)
                    if section is not None:
                        self._render_section(factory, cell_box, section)
                # Top-align the cell content: the stretchable spacer
                # absorbs the row's leftover height instead of letting
                # the frames inflate (and the controls spread out).
                factory.createSpacing(cell_box, YUIDimension.YD_VERT, stretchable=True)

    def _render_section(self, factory, parent, section: dict[str, Any]) -> None:
        """Render one titled section (a frame) into *parent*.

        Fields stack in a VBox, or flow side by side when the section
        carries ``"horizontal": True`` (e.g. the snapshots Actions row).
        """
        frame = factory.createFrame(parent, section.get("title", ""))
        if section.get("horizontal"):
            box = factory.createHBox(frame)
        else:
            box = factory.createVBox(frame)
        for field in section.get("fields", []):
            self._render_field(factory, box, field)

    def _render_field(self, factory, parent, field: dict[str, Any]) -> None:
        """Create the YUI widget for one field descriptor."""
        kind = field.get("widget")

        if kind == "label":
            widget = factory.createLabel(parent, field.get("text", ""))
            if field.get("wrap") and hasattr(widget, "setAutoWrap"):
                widget.setAutoWrap(True)
            self._pin_vertical(widget)

        elif kind == "button":
            widget = factory.createPushButton(parent, field.get("label", ""))
            self._wire(widget, field.get("id"), "button", field.get("callback"))
            self._pin_vertical(widget)

        elif kind == "checkbox":
            widget = factory.createCheckBox(
                parent, field.get("label", ""), field.get("checked", False)
            )
            self._wire(widget, field.get("id"), "checkbox", field.get("on_change"))
            self._pin_vertical(widget)

        elif kind == "image":
            self._pin_vertical(factory.createImage(parent, field.get("path", "")))

        elif kind == "text":
            widget = factory.createInputField(parent, field.get("label", ""))
            if field.get("id"):
                widget._id = field["id"]  # noqa: SLF001 -- YWidget has no setId()
            widget.setValue(str(field.get("value", "")))
            self._pin_vertical(widget)

        elif kind == "dropdown":
            widget = factory.createComboBox(parent, field.get("label", ""), editable=False)
            for option in field.get("options", []):
                widget.addItem(option)
            widget.setValue(str(field.get("selected", "")))
            self._wire(widget, field.get("id"), "dropdown", field.get("on_change"))
            self._pin_vertical(widget)

        elif kind == "spinner":
            widget = factory.createIntField(
                parent,
                field.get("label", ""),
                field.get("min_val", 0),
                field.get("max_val", 999),
                field.get("value", 0),
            )
            self._wire(widget, field.get("id"), "spinner", field.get("on_change"))
            self._pin_vertical(widget)

        elif kind == "tree":
            self._render_tree(factory, parent, field)

        else:
            logger.warning("Unknown widget kind: %r", kind)

    def _render_tree(self, factory, parent, field: dict[str, Any]) -> None:
        """Multi-column tree rows map onto a YUI table."""
        from manatools.aui.yui_common import (  # pure Python
            YTableHeader,
            YTableItem,
            YUIDimension,
        )

        rows = field.get("rows", [])
        if not rows:
            self._pin_vertical(factory.createLabel(parent, _("(no data)")))
            return

        # Columns: union of row keys, first-appearance order.
        columns: list[str] = []
        for row in rows:
            for key in row:
                if key not in columns:
                    columns.append(key)

        header = YTableHeader()
        for column in columns:
            header.addColumn(column)

        widget = factory.createTable(parent, header)
        # Stretch horizontally so the table fills its section frame
        # instead of rendering at its natural (content) width; the
        # vertical axis stays at natural height but can compress
        # (scrollbars) when the window is smaller than the content.
        widget.setStretchable(YUIDimension.YD_HORIZ, True)
        self._shrink_vertical(widget)
        # The page rebuilds after every dispatched callback, which
        # destroys this table; marking the previously selected row
        # selected before addItem restores the highlight in the fresh
        # table (the backends apply YTableItem.selected() on insert).
        selected_row = field.get("selected_row") or {}
        for row in rows:
            item = YTableItem()
            for column in columns:
                item.addCell(str(row.get(column, "")))
            if selected_row and all(
                row.get(key) == value for key, value in selected_row.items()
            ):
                item.setSelected(True)
            widget.addItem(item)

        if field.get("id"):
            widget._id = field["id"]  # noqa: SLF001 -- YWidget has no setId()
        if field.get("selectable"):
            self._handlers[widget.id()] = ("tree", field["on_select"])
            widget.setNotify(True)
        self._table_headers[widget] = header

    def _pin_vertical(self, widget) -> None:
        """Keep a static widget at its natural height (Fixed policy).

        libyui widgets default to stretchable in both directions, so a
        maximized window hands the extra height to every child and
        labels/headings inflate into large blank areas (text centered).
        Pinning the vertical axis keeps them at content height, aligned
        at the top of their box.
        """
        from manatools.aui.yui_common import YUIDimension  # lazy: manatools optional

        if hasattr(widget, "setStretchable"):
            widget.setStretchable(YUIDimension.YD_VERT, False)
        self._pinned.append(widget)

    def _shrink_vertical(self, widget) -> None:
        """Keep a widget at natural height, but let it compress.

        Same as :meth:`_pin_vertical`, but no Qt size policy is forced:
        the widget keeps the backend's Preferred policy, whose minimum
        is the widget's minimumSizeHint — so a window smaller than the
        content shrinks it (tables get scrollbars) instead of letting
        it overflow the monitor.  A Fixed policy would pin the sizeHint
        as the minimum and defeat this.  Used for tables.
        """
        from manatools.aui.yui_common import YUIDimension  # lazy: manatools optional

        if hasattr(widget, "setStretchable"):
            widget.setStretchable(YUIDimension.YD_VERT, False)

    def _apply_pinned_policies(self) -> None:
        """Force a Fixed vertical Qt size policy on pinned widgets.

        The stretchable flag is not enough on the Qt backend: its labels
        keep a QSizePolicy of Preferred, which *grows* to fill extra space
        even when the widget is not stretchable (the flag only weights the
        space distribution), so a maximized window still inflates headings
        and labels.  Pinning the policy on the real Qt widgets keeps them
        at content height; backends without a size-policy API (gtk,
        curses) are a natural no-op.
        """
        if not self._pinned:
            return
        try:
            from PySide6 import QtWidgets  # Qt backend only
        except ImportError:
            return
        for widget in self._pinned:
            self._set_vertical_policy(widget, QtWidgets.QSizePolicy.Policy.Fixed)

    @staticmethod
    def _set_vertical_policy(widget, policy) -> None:
        """Apply *policy* as the widget's vertical size policy."""
        try:
            backend = widget.get_backend_widget()
            set_policy = getattr(backend, "setSizePolicy", None)
            if not callable(set_policy):
                return
            backend_policy = backend.sizePolicy()
            backend_policy.setVerticalPolicy(policy)
            set_policy(backend_policy)
        except Exception as exc:
            logger.debug("Could not set vertical size policy: %s", exc)
        # Reflow the layout with the new policies.
        try:
            get_backend = getattr(self._dialog, "get_backend_widget", None)
            backend = get_backend() if callable(get_backend) else None
            if backend is not None and hasattr(backend, "layout"):
                layout = backend.layout()
                if layout is not None and hasattr(layout, "activate"):
                    layout.activate()
        except Exception as exc:
            logger.debug("Could not reflow layout after pinning: %s", exc)

    def _wire(self, widget, widget_id, kind: str, callback) -> None:
        """Assign the descriptor id and register the event handler."""
        if widget_id:
            widget._id = widget_id  # noqa: SLF001 -- YWidget has no setId()
        if callback is not None:
            widget.setNotify(True)
            self._handlers[widget.id()] = (kind, callback)

    # -- Application icon ---------------------------------------------------

    def _set_app_icon(self) -> None:
        """Apply the manaradix logo as the application/window icon.

        The logo ships as ``data/images/manaradix.svg`` (installed to
        ``/usr/share/manaradix/images/``) and is resolved by
        :func:`manaradix.core.assets.logo_path`.  The absolute path is
        passed straight to ``setApplicationIcon``: the Qt icon resolver
        treats bare names as theme icons (its icon base path is not
        consulted for files), while an absolute path loads directly on
        every backend.  Failure is cosmetic only.
        """
        icon_path = logo_path()
        if not icon_path:
            logger.debug("No manaradix logo found; skipping the window icon")
            return
        try:
            from manatools.aui.yui import YUI

            YUI.application().setApplicationIcon(icon_path)
        except Exception as exc:  # cosmetic only
            logger.debug("Could not set application icon: %s", exc)

    # -- Events -------------------------------------------------------------

    def _backend(self):
        """Return (size_hint, resize) for the dialog's backend widget.

        Backends without the sizing API (curses) yield None.
        """
        dialog = self._dialog
        if dialog is None:
            return None
        try:
            get_backend = getattr(dialog, "get_backend_widget", None)
            backend = get_backend() if callable(get_backend) else None
            if backend is None:
                return None
            size_hint = getattr(backend, "sizeHint", None)
            resize = getattr(backend, "resize", None)
            if not callable(size_hint) or not callable(resize):
                return None
            return (size_hint, resize)
        except Exception:
            return None

    @staticmethod
    def _size_of(hint) -> tuple[int, int]:
        """Normalize a backend size hint (QSize or (w, h)) to ints."""
        width = getattr(hint, "width", None)
        if callable(width):
            return (int(width()), int(hint.height()))
        return (int(hint[0]), int(hint[1]))

    def _fit_window(self) -> None:
        """Fit the window to the current page content.

        The manatools Qt backend deliberately grows the window to its
        largest content and never shrinks it back (``showChild`` uses
        ``max()``), so after every content swap we resize to the current
        size hint.  ``adjustSize()`` alone is not enough — it applies an
        expanding minimum and a screen-fraction cap — so we activate the
        layout and resize to the exact ``sizeHint()``, clamped to the
        screen so the window never extends past the monitor.  Backends
        without this API (curses) are a natural no-op.
        """
        sizing = self._backend()
        if sizing is None:
            return
        size_hint, resize = sizing
        try:
            backend = self._dialog.get_backend_widget()
            # adjustSize is documented to do nothing useful on maximized
            # or fullscreen windows; leave those alone.
            if getattr(backend, "isMaximized", lambda: False)() or getattr(
                backend, "isFullScreen", lambda: False
            )():
                return
            layout = getattr(backend, "layout", None)
            activate = getattr(layout(), "activate", None) if callable(layout) else None
            if callable(activate):
                activate()
            # resize signatures vary: fake backends take a single
            # (w, h) tuple, Qt accepts separate ints — try both.
            width, height = self._clamp_to_screen(backend, *self._size_of(size_hint()))
            try:
                resize((width, height))
            except TypeError:
                try:
                    resize(width, height)
                except Exception as exc:
                    logger.debug("Could not fit window to content: %s", exc)
        except Exception as exc:
            logger.debug("Could not fit window to content: %s", exc)

    @staticmethod
    def _clamp_to_screen(backend, width: int, height: int) -> tuple[int, int]:
        """Clamp a requested window size to the backend's screen.

        A window must never open larger than the monitor: content that
        does not fit compresses (rows share the height, tables scroll)
        instead of overflowing off-screen.  Backends without a screen
        API (or fake backends in tests) pass the size through.
        """
        try:
            screen = backend.screen()
            if screen is None:
                return width, height
            geo = screen.availableGeometry()
            return min(width, geo.width()), min(height, geo.height())
        except Exception:
            return width, height

    def _startup_fit_to_largest(self, factory) -> None:
        """Size the window to the largest page at startup.

        The Qt backend opens the main dialog at the layout minimum of the
        first page — the dashboard — leaving a window that is too small
        until a wider tab is visited.  Rendering every page once up front
        (core data is cached, so this is cheap) and resizing to the
        largest hint opens a window that already fits every tab.
        """
        if len(self._pages) <= 1:
            return
        sizing = self._backend()
        if sizing is None:
            return
        size_hint, resize = sizing
        first = self._current_index
        sizes: list[tuple[int, int]] = []
        for index in list(range(len(self._pages))) + [first]:
            self._show_page(factory, index)
            try:
                hint = size_hint()
            except Exception:
                hint = None
            if hint is not None:
                sizes.append(self._size_of(hint))
        if not sizes:
            return
        backend = self._dialog.get_backend_widget()
        width, height = self._clamp_to_screen(
            backend, max(w for w, _ in sizes), max(h for _, h in sizes)
        )
        # resize signatures vary: Qt accepts (w, h) ints, fake backends a
        # single (w, h) tuple — try both.
        try:
            resize((width, height))
        except TypeError:
            try:
                resize(width, height)
            except Exception as exc:
                logger.debug("Could not resize window to largest page: %s", exc)
        except Exception as exc:
            logger.debug("Could not resize window to largest page: %s", exc)

    def _switch_tab(self, factory) -> None:
        """Render the page belonging to the newly selected tab."""
        selected = self._tabs.selectedItem()
        if selected is None:
            return
        label = selected.label()
        for index, page in enumerate(self._pages):
            if page.title == label:
                self._show_page(factory, index)
                return

    # -- Navigation ---------------------------------------------------------

    def navigate(self, page_id: str) -> bool:
        """Switch to the page with *page_id*, rendering it immediately.

        Pages reach this through their injected ``renderer`` reference
        (e.g. the dashboard's Create Snapshot quick action jumps to the
        Snapshots page).  The tab selection is moved too, so the tab bar
        stays in sync with the visible content.  Returns False when no
        page has that id, or before the dialog is running.
        """
        factory = self._active_factory
        if factory is None or self._tabs is None:
            return False
        for index, page in enumerate(self._pages):
            if getattr(page, "page_id", None) != page_id:
                continue
            self._select_tab(page.title)
            self._show_page(factory, index)
            return True
        logger.warning("No page with id %r to navigate to", page_id)
        return False

    def _select_tab(self, label: str) -> None:
        """Mark the tab item with *label* as the current selection.

        Real backends expose ``selectItem`` (which also moves the tab
        bar); the test fake only has ``select``.
        """
        items = getattr(self._tabs, "_items", None)  # noqa: SLF001 -- YSelectionWidget stores its items in _items
        if items is None:
            return
        for item in items:
            if item.label() != label:
                continue
            select_item = getattr(self._tabs, "selectItem", None)
            if callable(select_item):
                select_item(item, True)
            else:
                select = getattr(self._tabs, "select", None)
                if callable(select):
                    select(item)
            return

    def _dispatch(self, factory, event) -> None:
        """Dispatch a widget event to the descriptor's callback."""
        widget = event.widget()
        entry = self._handlers.get(widget.id())
        if entry is None:
            return
        kind, callback = entry
        reason = event.reason().name

        if kind == "tree":
            if reason in _SELECT_REASONS:
                callback(self._selected_row(widget))
            else:
                return
        elif kind == "button":
            if reason not in _BUTTON_REASONS:
                return
            callback()
        elif kind == "dropdown":
            if reason not in _CHANGE_REASONS:
                return
            # The combo box's current selection is not stored back on the
            # descriptor (the page is re-built after every dispatch), so
            # hand the widget's value to the callback.
            get_value = getattr(widget, "value", None)
            callback(get_value() if callable(get_value) else None)
        elif kind in ("checkbox", "spinner"):
            if reason not in _CHANGE_REASONS:
                return
            # The descriptor's checked/value is stale (the page is rebuilt
            # after every dispatch), but the widget holds its current
            # value — hand it to the callback, same contract as dropdowns.
            get_value = getattr(widget, "value", None)
            callback(get_value() if callable(get_value) else None)
        else:
            return

        # Callbacks may mutate state (create/delete snapshots, apply
        # policies); re-render the current page so the UI reflects it.
        self._show_page(factory, self._current_index)

    def _selected_row(self, table) -> dict[str, Any]:
        """Return the row dict of the table's selected item."""
        header = self._table_headers.get(table)
        if header is None:
            return {}
        selected = getattr(table, "selectedItem", None)
        item = selected() if selected else None
        if item is None:
            return {}
        row: dict[str, Any] = {}
        for column in range(header.columns()):
            try:
                row[header.header(column)] = item.cell(column).label()
            except Exception:
                continue
        return row
