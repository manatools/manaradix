"""Declarative UI model for manaradix pages.

The released python-manatools provides a libyui-style YUI widget API with no
PageModel/Widget abstraction, so manaradix owns a thin declarative layer:
pages describe their content as pure data (``PageModel`` → sections →
fields), and the renderer in :mod:`manaradix.ui.renderer` maps that data onto
real YUI widgets.  Keeping the model pure data keeps pages testable without
a display and preserves the CLI-first, frontend-agnostic design.
"""

from __future__ import annotations

from typing import Any, Callable


class PageModel:
    """A page's declarative content: a list of titled sections.

    Two layout modes:

    - ``columns`` > 1 (default mode): sections side by side, each column
      an HBox VBox — keeps dense pages from stretching the window past
      small screens.  Sections may pin themselves to a column with a
      ``"column"`` key; unpinned sections fill the shortest column.
    - ``rows`` (grid mode): explicit row/column placement.  Each row is a
      list of cells; a cell is a section id (string) or a list of section
      ids stacked vertically in that cell.  A one-cell row spans the full
      width; equal-width cells split 50/50 (all cells are horizontally
      stretchable).  Sections are addressed by their ``"id"`` key.

    ``rows`` takes precedence over ``columns`` when both are set.
    """

    def __init__(
        self,
        sections: list[dict[str, Any]] | None = None,
        columns: int = 1,
        rows: list[list] | None = None,
    ) -> None:
        self.sections = list(sections or [])
        self.columns = columns
        self.rows = rows


class Widget:
    """Factory for field descriptors (plain dicts consumed by the renderer).

    Every descriptor carries a ``widget`` kind key; the remaining keys follow
    the widget type.  Callbacks are stored on the descriptor and invoked by
    the renderer's event loop.
    """

    @staticmethod
    def label(*, id: str | None = None, text: str, wrap: bool = False) -> dict[str, Any]:
        """Static text.  ``wrap`` enables word wrapping (long lines wrap
        instead of stretching the window; supported by all backends)."""
        return {"widget": "label", "id": id, "text": text, "wrap": wrap}

    @staticmethod
    def image(*, id: str | None = None, path: str = "") -> dict[str, Any]:
        """Image from a file path; renders as a YUI ``createImage``.

        Args:
            id: Optional widget id for the descriptor.
            path: Absolute path to the image file (default "").

        Returns:
            The widget descriptor dict (``widget`` kind ``"image"``).
        """
        return {"widget": "image", "id": id, "path": path}

    @staticmethod
    def button(
        *,
        id: str | None = None,
        label: str,
        callback: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        """Clickable button; renders as a YUI push button wired with
        ``setNotify`` so ``callback`` fires on activation.

        Args:
            id: Optional widget id for the descriptor.
            label: Button text.
            callback: Invoked (with no arguments) when the button is
                activated, or None for a display-only button.

        Returns:
            The widget descriptor dict (``widget`` kind ``"button"``).
        """
        return {"widget": "button", "id": id, "label": label, "callback": callback}

    @staticmethod
    def tree(
        *,
        id: str | None = None,
        rows: list[dict[str, Any]] | None = None,
        selectable: bool = False,
        on_select: Callable[[dict[str, Any]], None] | None = None,
        selected_row: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Multi-column table of rows; renders as a YUI ``YTable`` with a
        ``YTableHeader``.

        Args:
            id: Optional widget id for the descriptor.
            rows: Row dicts; column names are the union of row keys in
                first-appearance order.
            selectable: When True the table notifies (``setNotify``) on
                row selection and ``on_select`` receives the row dict.
            on_select: Invoked with the selected row dict on selection,
                or None.
            selected_row: Column→value dict; when the table renders, the
                row matching every key/value is pre-selected.  The page
                rebuilds after every dispatched callback, so this is how
                a selection survives a re-render (the native row
                highlight would otherwise be wiped with the old widget).
                Pass the row's natural key (e.g. ``{"ID": id}``), not the
                full row, so the match stays stable across state changes.

        Returns:
            The widget descriptor dict (``widget`` kind ``"tree"``).
        """
        return {
            "widget": "tree",
            "id": id,
            "rows": list(rows or []),
            "selectable": selectable,
            "on_select": on_select,
            "selected_row": selected_row,
        }

    @staticmethod
    def pie(
        *,
        id: str | None = None,
        label: str = "",
        slices: list[dict[str, Any]] | None = None,
        center_text: str = "",
        donut_ratio: float = 0.0,
        value_format: Callable[[float], str] | None = None,
    ) -> dict[str, Any]:
        """Pie / donut chart; renders as a YUI ``YChartPie``
        (``createChartPie``, python-manatools with chart support).

        Backends without chart support get a plain table of the slices
        instead, so the data is never lost.

        Args:
            id: Optional widget id for the descriptor.
            label: Chart title (default "", no title).
            slices: Slice dicts with ``label`` (str), ``value`` (number
                ``>= 0``) and an optional ``color`` (``"#rrggbb"``;
                omitted means the chart palette).
            center_text: Text drawn in the donut hole (donut mode only).
            donut_ratio: Hole radius as a fraction of the pie radius,
                0 (full pie, default) to 0.9.
            value_format: Formats a slice value for the legend, tooltip
                and table fallback (e.g. bytes to "1.5 GiB"), or None
                for the plain number.

        Returns:
            The widget descriptor dict (``widget`` kind ``"pie"``).
        """
        return {
            "widget": "pie",
            "id": id,
            "label": label,
            "slices": list(slices or []),
            "center_text": center_text,
            "donut_ratio": donut_ratio,
            "value_format": value_format,
        }

    @staticmethod
    def text(
        *,
        id: str | None = None,
        label: str = "",
        value: str = "",
    ) -> dict[str, Any]:
        """Single-line text input; renders as a YUI input field
        (``createInputField``).

        Args:
            id: Optional widget id for the descriptor.
            label: Field label.
            value: Initial field value (default "").

        Returns:
            The widget descriptor dict (``widget`` kind ``"text"``).
        """
        return {"widget": "text", "id": id, "label": label, "value": value}

    @staticmethod
    def checkbox(
        *,
        id: str | None = None,
        label: str = "",
        checked: bool = False,
        on_change: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        """Checkbox; renders as a YUI ``createCheckBox`` wired with
        ``setNotify`` so ``on_change`` fires on state changes.

        Args:
            id: Optional widget id for the descriptor.
            label: Checkbox text.
            checked: Initial checked state (default False).
            on_change: Invoked (with no arguments) when the state
                changes, or None.

        Returns:
            The widget descriptor dict (``widget`` kind ``"checkbox"``).
        """
        return {
            "widget": "checkbox",
            "id": id,
            "label": label,
            "checked": checked,
            "on_change": on_change,
        }

    @staticmethod
    def dropdown(
        *,
        id: str | None = None,
        label: str = "",
        options: list[str] | None = None,
        selected: str = "",
        on_change: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        """Dropdown selection; renders as a YUI combo box
        (``createComboBox``, not editable) wired with ``setNotify``.

        Args:
            id: Optional widget id for the descriptor.
            label: Dropdown label.
            options: Selectable option strings.
            selected: Initially selected option (default "").
            on_change: Invoked with the selected option's value when the
                selection changes, or None.

        Returns:
            The widget descriptor dict (``widget`` kind ``"dropdown"``).
        """
        return {
            "widget": "dropdown",
            "id": id,
            "label": label,
            "options": list(options or []),
            "selected": selected,
            "on_change": on_change,
        }

    @staticmethod
    def spinner(
        *,
        id: str | None = None,
        label: str = "",
        value: int = 0,
        min_val: int = 0,
        max_val: int = 999,
        on_change: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        """Integer spinner; renders as a YUI int field (``createIntField``)
        wired with ``setNotify`` so ``on_change`` fires on value changes.

        Args:
            id: Optional widget id for the descriptor.
            label: Spinner label.
            value: Initial integer value (default 0).
            min_val: Minimum allowed value (default 0).
            max_val: Maximum allowed value (default 999).
            on_change: Invoked (with no arguments) when the value
                changes, or None.

        Returns:
            The widget descriptor dict (``widget`` kind ``"spinner"``).
        """
        return {
            "widget": "spinner",
            "id": id,
            "label": label,
            "value": value,
            "min_val": min_val,
            "max_val": max_val,
            "on_change": on_change,
        }
