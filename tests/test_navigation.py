"""Unit tests for the UI navigation structure (ui/navigation.py)."""

from unittest.mock import MagicMock, patch

from manaradix.ui.navigation import build_navigation


def _ctx():
    ctx = MagicMock()
    ctx.snapshot_manager.list_all.return_value = []
    ctx.config_store.list_policies.return_value = []
    return ctx


class TestBuildNavigation:
    def test_configured_system_has_five_pages_with_about_last(self) -> None:
        ctx = _ctx()
        with patch("manaradix.ui.navigation.needs_setup", return_value=False):
            pages = build_navigation(ctx)
        assert [p.page_id for p in pages] == [
            "dashboard", "snapshots", "policies", "settings", "about",
        ]

    def test_unconfigured_system_prepends_wizard(self) -> None:
        ctx = _ctx()
        with patch("manaradix.ui.navigation.needs_setup", return_value=True):
            pages = build_navigation(ctx)
        assert pages[0].page_id == "setup"
        assert pages[-1].page_id == "about"
        assert len(pages) == 6

    def test_about_page_shows_author_and_license(self) -> None:
        ctx = _ctx()
        with patch("manaradix.ui.navigation.needs_setup", return_value=False):
            about = build_navigation(ctx)[-1]
        model = about.build_model()
        texts = [
            f.get("text", "")
            for section in model.sections
            for f in section["fields"]
        ]
        assert any("Matteo Pasotti" in t for t in texts)
        assert any("me@matteopasotti.it" in t for t in texts)
        assert any("© 2026" in t for t in texts)
        assert any("GPLv2+" in t for t in texts)
        # The logo is shown at the top of the page (PNG raster preferred,
        # SVG fallback).
        paths = [
            f.get("path", "")
            for section in model.sections
            for f in section["fields"]
        ]
        assert any("manaradix." in p for p in paths)
