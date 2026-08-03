"""Navigation structure for the manaradix UI.

Builds the top-level page list consumed by ``AuiRenderer``, which hosts
the pages as tabs in the manatools YUI main dialog.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from manaradix.core.setup_wizard import needs_setup
from manaradix.ui.pages.about import AboutPage
from manaradix.ui.pages.dashboard import DashboardPage
from manaradix.ui.pages.policies import PoliciesPage
from manaradix.ui.pages.settings import SettingsPage
from manaradix.ui.pages.setup import SetupWizardPage
from manaradix.ui.pages.snapshots import SnapshotsPage

if TYPE_CHECKING:
    from manaradix.module import AppContext


def build_navigation(app_context: AppContext) -> list:
    """Build the navigation structure for the application.

    The *app_context* is passed to every page so they can access shared
    services.  When the system has no snapshots and no snapshot policies
    (spec.md §15), a first-run Setup Wizard page is prepended.

    Returns:
        A list of page instances consumed by ``AuiRenderer``, which hosts
        them as tabs in the manatools YUI main dialog.
    """
    pages = [
        DashboardPage(app_context),
        SnapshotsPage(app_context),
        PoliciesPage(app_context),
        SettingsPage(app_context),
        AboutPage(app_context),
    ]
    if needs_setup(app_context):
        pages.insert(0, SetupWizardPage(app_context))
    return pages
