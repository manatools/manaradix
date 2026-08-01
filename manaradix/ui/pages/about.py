"""Manaradix — About page.

Static information about the application: version, author, copyright,
and license.  Always present as the last tab, independent of the
system's snapshot state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from manaradix import __version__
from manaradix.core.assets import logo_path, logo_png_path
from manaradix.i18n import _
from manaradix.ui.model import PageModel, Widget

if TYPE_CHECKING:
    from manaradix.module import AppContext

_AUTHOR = "Matteo Pasotti (xquiet)"
_EMAIL = "me@matteopasotti.it"
_COPYRIGHT = "© 2026 Matteo Pasotti"
_LICENSE = "GPL-2.0-or-later"
_WEBSITE = "https://github.com/mageia/manaradix"

_GPL_NOTICE = _(
    "Manaradix is free software: you can redistribute it and/or modify it "
    "under the terms of the GNU General Public License as published by the "
    "Free Software Foundation, either version 2 of the License, or (at your "
    "option) any later version."
)


class AboutPage:
    """Static about/information page (author, copyright, license)."""

    page_id = "about"
    title = _("About")

    def __init__(self, app_context) -> None:
        self.app = app_context

    def build_model(self):
        """Return a ``PageModel`` with the application information."""
        fields = []
        # The PNG raster renders at its real size in the YUI image widget
        # (vector SVG icons fall back to 32x32); the SVG is a fallback.
        icon = logo_png_path() or logo_path()
        if icon:
            fields.append(Widget.image(id="about_logo", path=icon))
        fields.extend([
            Widget.label(
                id="about_description",
                text=_(
                    "Btrfs snapshot, subvolume, and filesystem "
                    "management utility for Mageia"
                ),
            ),
            Widget.label(
                id="about_version",
                text=_("Version: %s") % __version__,
            ),
            Widget.label(
                id="about_author",
                text=_("Author: %s") % _AUTHOR,
            ),
            Widget.label(
                id="about_email",
                text=_("Email: %s") % _EMAIL,
            ),
            Widget.label(
                id="about_copyright",
                text=_("Copyright: %s") % _COPYRIGHT,
            ),
            Widget.label(
                id="about_license",
                text=_("License: %s") % _LICENSE,
            ),
            Widget.label(
                id="about_website",
                text=_("Website: %s") % _WEBSITE,
            ),
        ])
        return PageModel(sections=[
            {
                "title": "Manaradix",
                "fields": fields,
            },
            {
                "title": _("License"),
                "fields": [
                    Widget.label(id="about_gpl", text=_GPL_NOTICE),
                ],
            },
        ])
