"""Navigation structure for the manaradix UI.

Builds the top-level page list and navigation tree used by the
manatools aui ``Application``.
"""

from __future__ import annotations

from manaradix.ui.pages.overview import OverviewPage
from manaradix.ui.pages.policies import PoliciesPage
from manaradix.ui.pages.settings import SettingsPage
from manaradix.ui.pages.snapshots import SnapshotsPage

# Import AppContext for type hints (avoid circular import at runtime)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from manaradix.module import AppContext


def build_navigation(app_context: AppContext) -> list:
    """Build the navigation structure for the application.

    Each page reference is lazy-initialized by the aui framework.
    The *app_context* is passed to every page so they can access
    shared services.

    Args:
        app_context: The shared application context.

    Returns:
        A list of ``NavigationItem``-compatible dicts (or Page instances)
        that the aui ``Application`` renders as a sidebar / tab bar.
    """
    return [
        OverviewPage(app_context),
        SnapshotsPage(app_context),
        PoliciesPage(app_context),
        SettingsPage(app_context),
    ]
