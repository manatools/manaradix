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
    """A page's declarative content: a list of titled sections."""

    def __init__(self, sections: list[dict[str, Any]] | None = None) -> None:
        self.sections = list(sections or [])


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
