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
        return {"widget": "image", "id": id, "path": path}

    @staticmethod
    def button(
        *,
        id: str | None = None,
        label: str,
        callback: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        return {"widget": "button", "id": id, "label": label, "callback": callback}

    @staticmethod
    def tree(
        *,
        id: str | None = None,
        rows: list[dict[str, Any]] | None = None,
        selectable: bool = False,
        on_select: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        return {
            "widget": "tree",
            "id": id,
            "rows": list(rows or []),
            "selectable": selectable,
            "on_select": on_select,
        }

    @staticmethod
    def text(
        *,
        id: str | None = None,
        label: str = "",
        value: str = "",
    ) -> dict[str, Any]:
        return {"widget": "text", "id": id, "label": label, "value": value}

    @staticmethod
    def checkbox(
        *,
        id: str | None = None,
        label: str = "",
        checked: bool = False,
        on_change: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
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
        return {
            "widget": "spinner",
            "id": id,
            "label": label,
            "value": value,
            "min_val": min_val,
            "max_val": max_val,
            "on_change": on_change,
        }
