"""Page behaviour tests (dashboard navigation, snapshots error surfacing).

The page callbacks are plain methods over the core services, so they can
be exercised directly with a stubbed ``AppContext`` (no renderer, no
display) — the renderer itself is covered by ``test_ui_renderer``.
"""

from unittest.mock import MagicMock

from manaradix.ui.pages.dashboard import DashboardPage
from manaradix.ui.pages.snapshots import SnapshotsPage


class FakeRenderer:
    """Minimal renderer stub recording navigation calls."""

    def __init__(self):
        self.navigations = []

    def navigate(self, page_id):
        self.navigations.append(page_id)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


class TestDashboardQuickActions:
    def test_create_snapshot_navigates_to_snapshots_page(self) -> None:
        page = DashboardPage(app_context=None)
        page.renderer = FakeRenderer()
        page.on_create_snapshot()
        assert page.renderer.navigations == [SnapshotsPage.page_id]

    def test_create_snapshot_without_renderer_is_noop(self) -> None:
        page = DashboardPage(app_context=None)
        page.on_create_snapshot()  # must not raise


# ---------------------------------------------------------------------------
# Snapshots page
# ---------------------------------------------------------------------------


def _app(snapshot_manager=None) -> MagicMock:
    app = MagicMock()
    app.snapshot_manager = snapshot_manager or MagicMock()
    app.snapshot_manager.list_all.return_value = []
    return app


class TestSnapshotsPageErrors:
    def test_failed_create_is_surfaced_in_model(self) -> None:
        app = _app()
        app.snapshot_manager.create.side_effect = PermissionError(
            13, "Permission denied"
        )
        page = SnapshotsPage(app_context=app)
        page.on_create_snapshot()

        model = page.build_model()
        assert model.sections[0]["title"] == "Error"
        labels = [
            f for f in model.sections[0]["fields"] if f["widget"] == "label"
        ]
        assert "Permission denied" in labels[0]["text"]

    def test_successful_create_clears_previous_error(self) -> None:
        app = _app()
        page = SnapshotsPage(app_context=app)
        # First attempt fails, then succeeds.
        app.snapshot_manager.create.side_effect = PermissionError(13, "nope")
        page.on_create_snapshot()
        assert page.build_model().sections[0]["title"] == "Error"

        app.snapshot_manager.create.side_effect = None
        page.on_create_snapshot()
        assert all(
            s["title"] != "Error" for s in page.build_model().sections
        )
