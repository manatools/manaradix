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
        # Injectable factory for tests; defaults to the real YUI factory.
        self._factory = factory
        self._handlers: dict[str, tuple[str, Callable]] = {}
        self._table_headers: dict[Any, Any] = {}
        self._tabs = None
        self._replace_point = None
        self._dialog = None
        self._current_index = 0

    # -- Lifecycle ----------------------------------------------------------

    def run(self) -> None:
        """Build the main dialog and run the event loop until closed."""
        from manatools.aui.yui import YUI  # lazy: keep CLI importable without manatools

        factory = self._factory or YUI.widgetFactory()

        if self._factory is None:
            try:
                YUI.application().setApplicationTitle("Manaradix")
            except Exception as exc:  # cosmetic only
                logger.debug("Could not set application title: %s", exc)
            self._set_app_icon()

        dialog = factory.createMainDialog()
        self._dialog = dialog
        root = factory.createVBox(dialog)
        factory.createHeading(root, "Manaradix")

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

        dialog.open()
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

        content = factory.createVBox(replace_point)
        factory.createHeading(content, page.title)

        model = page.build_model()
        for section in model.sections:
            frame = factory.createFrame(content, section.get("title", ""))
            box = factory.createVBox(frame)
            for field in section.get("fields", []):
                self._render_field(factory, box, field)

        replace_point.showChild()
        self._current_index = index
        self._fit_window()

    def _render_field(self, factory, parent, field: dict[str, Any]) -> None:
        """Create the YUI widget for one field descriptor."""
        kind = field.get("widget")

        if kind == "label":
            widget = factory.createLabel(parent, field.get("text", ""))
            if field.get("wrap") and hasattr(widget, "setAutoWrap"):
                widget.setAutoWrap(True)

        elif kind == "button":
            widget = factory.createPushButton(parent, field.get("label", ""))
            self._wire(widget, field.get("id"), "button", field.get("callback"))

        elif kind == "checkbox":
            widget = factory.createCheckBox(
                parent, field.get("label", ""), field.get("checked", False)
            )
            self._wire(widget, field.get("id"), "checkbox", field.get("on_change"))

        elif kind == "image":
            factory.createImage(parent, field.get("path", ""))

        elif kind == "text":
            widget = factory.createInputField(parent, field.get("label", ""))
            if field.get("id"):
                widget._id = field["id"]  # noqa: SLF001 -- YWidget has no setId()
            widget.setValue(str(field.get("value", "")))

        elif kind == "dropdown":
            widget = factory.createComboBox(parent, field.get("label", ""), editable=False)
            for option in field.get("options", []):
                widget.addItem(option)
            widget.setValue(str(field.get("selected", "")))
            self._wire(widget, field.get("id"), "dropdown", field.get("on_change"))

        elif kind == "spinner":
            widget = factory.createIntField(
                parent,
                field.get("label", ""),
                field.get("min_val", 0),
                field.get("max_val", 999),
                field.get("value", 0),
            )
            self._wire(widget, field.get("id"), "spinner", field.get("on_change"))

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
            factory.createLabel(parent, _("(no data)"))
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
        # instead of rendering at its natural (content) width.
        widget.setStretchable(YUIDimension.YD_HORIZ, True)
        for row in rows:
            item = YTableItem()
            for column in columns:
                item.addCell(str(row.get(column, "")))
            widget.addItem(item)

        if field.get("id"):
            widget._id = field["id"]  # noqa: SLF001 -- YWidget has no setId()
        if field.get("selectable"):
            self._handlers[widget.id()] = ("tree", field["on_select"])
            widget.setNotify(True)
        self._table_headers[widget] = header

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

    def _fit_window(self) -> None:
        """Fit the window to the current page content.

        The manatools Qt backend deliberately grows the window to its
        largest content and never shrinks it back (``showChild`` uses
        ``max()``), so after every content swap we resize to the current
        size hint.  ``adjustSize()`` alone is not enough — it applies an
        expanding minimum and a screen-fraction cap — so we activate the
        layout and resize to the exact ``sizeHint()``.  Backends without
        this API (curses) are a natural no-op.
        """
        dialog = self._dialog
        if dialog is None:
            return
        try:
            get_backend = getattr(dialog, "get_backend_widget", None)
            backend = get_backend() if callable(get_backend) else None
            if backend is None:
                return
            size_hint = getattr(backend, "sizeHint", None)
            resize = getattr(backend, "resize", None)
            if not callable(size_hint) or not callable(resize):
                return
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
            resize(size_hint())
        except Exception as exc:
            logger.debug("Could not fit window to content: %s", exc)

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
            callback()
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
