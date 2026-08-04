"""Page behaviour tests (dashboard navigation, snapshots error surfacing).

The page callbacks are plain methods over the core services, so they can
be exercised directly with a stubbed ``AppContext`` (no renderer, no
display) — the renderer itself is covered by ``test_ui_renderer``.
"""

from unittest.mock import MagicMock

from manaradix.model.snapshot import SnapshotType
from manaradix.ui.pages.about import AboutPage
from manaradix.ui.pages.dashboard import DashboardPage
from manaradix.ui.pages.snapshots import SnapshotsPage


class FakeRenderer:
    """Minimal renderer stub recording navigation calls."""

    def __init__(self):
        self.navigations = []

    def navigate(self, page_id):
        self.navigations.append(page_id)


# ---------------------------------------------------------------------------
# About page
# ---------------------------------------------------------------------------


class TestAboutPageLayout:
    def test_two_columns_logo_left_info_right(self) -> None:
        page = AboutPage(app_context=None)
        model = page.build_model()
        assert model.columns == 2
        sections = model.sections
        # Column 0: just the logo, small at its raster size.
        assert sections[0]["column"] == 0
        assert [f["widget"] for f in sections[0]["fields"]] == ["image"]
        # Column 1: everything else, license included.
        assert all(s["column"] == 1 for s in sections[1:])
        assert any(s["title"] == "License" for s in sections)
        info = next(s for s in sections if s["title"] == "About")
        info_labels = [f["id"] for f in info["fields"] if f["widget"] == "label"]
        assert "about_license" in info_labels
        assert "about_website" in info_labels


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


def _mounts_app() -> MagicMock:
    """An app whose system exposes two btrfs mounts (/ and /home)."""
    app = _app()
    app.btrfs_ops.btrfs_mounts.return_value = [
        {"target": "/", "source": "/dev/nvme0n1p3", "uuid": "u1"},
        {"target": "/home", "source": "/dev/mapper/crypt", "uuid": "u2"},
    ]
    return app


def _mounts_with_browse_app() -> MagicMock:
    """The same, plus a stale browse mount of a snapshot."""
    app = _mounts_app()
    app.btrfs_ops.btrfs_mounts.return_value.append(
        {
            "target": "/mnt/manaradix-snapshot",
            "source": "/.snapshots/2026-08-04_12-31-46/root",
            "uuid": "u1",
        },
    )
    return app


class TestSnapshotsPageLayout:
    def test_actions_are_first_row_and_horizontal(self) -> None:
        page = SnapshotsPage(app_context=_app())
        model = page.build_model()
        actions = model.sections[0]
        assert actions["title"] == "Actions"
        assert actions["horizontal"] is True
        buttons = [f for f in actions["fields"] if f["widget"] == "button"]
        assert [b["id"] for b in buttons] == [
            "btn_create", "btn_delete", "btn_restore",
            "btn_browse", "btn_export",
        ]

    def test_error_banner_precedes_actions(self) -> None:
        app = _app()
        app.snapshot_manager.create.side_effect = PermissionError(13, "nope")
        page = SnapshotsPage(app_context=app)
        page.on_create_snapshot()
        model = page.build_model()
        assert model.sections[0]["title"] == "Error"
        assert model.sections[1]["title"] == "Actions"
        assert model.sections[1]["horizontal"] is True


class TestSnapshotsPageTarget:
    """The page works on a chosen btrfs mount (default /)."""

    def test_target_dropdown_lists_mounts(self) -> None:
        page = SnapshotsPage(app_context=_mounts_app())
        model = page.build_model()
        actions = model.sections[0]
        target = next(
            f for f in actions["fields"] if f["id"] == "create_target"
        )
        assert target["widget"] == "dropdown"
        assert target["options"] == ["/", "/home"]
        assert target["selected"] == "/"

    def test_create_defaults_to_root(self) -> None:
        app = _mounts_app()
        page = SnapshotsPage(app_context=app)
        page.on_create_snapshot()
        app.snapshot_manager.create.assert_called_once_with(
            subvolume="/",
            mount="/",
            description="Manual snapshot",
            read_only=True,
            snapshot_type=SnapshotType.MANUAL,
        )

    def test_create_uses_selected_target(self) -> None:
        app = _mounts_app()
        page = SnapshotsPage(app_context=app)
        page.on_create_target_change("/home")
        page.on_create_snapshot()
        app.snapshot_manager.create.assert_called_once_with(
            subvolume="/home",
            mount="/home",
            description="Manual snapshot",
            read_only=True,
            snapshot_type=SnapshotType.MANUAL,
        )

    def test_list_follows_selected_target(self) -> None:
        app = _mounts_app()
        page = SnapshotsPage(app_context=app)
        page.on_create_target_change("/home")
        page.build_model()
        app.snapshot_manager.list_all.assert_called_with(mount="/home")

    def test_targets_fallback_to_root_when_mounts_unreadable(self) -> None:
        # _app() leaves btrfs_mounts returning a non-iterable MagicMock
        page = SnapshotsPage(app_context=_app())
        assert page._create_targets() == ["/"]

    def test_browse_mount_is_not_a_target(self) -> None:
        """A snapshot mounted for browsing must not appear in the target
        list: it is a temporary view, not a place to create snapshots."""
        page = SnapshotsPage(app_context=_mounts_with_browse_app())
        assert page._create_targets() == ["/", "/home"]


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
