"""Internationalisation helpers for manaradix.

Provides a ``_()`` translation function backed by Python's ``gettext``.
Call ``init_i18n()`` once at startup to bind the ``manaradix`` text domain.

Locale lookup order
-------------------
1. ``MANARADIX_LOCALE_DIR`` environment variable.
2. ``/usr/share/locale`` (system install).
3. ``<project>/locale/`` (development).

Usage::

    from manaradix.i18n import _

    print(_("Snapshot created successfully."))
"""

from __future__ import annotations

import gettext
import os

DOMAIN = "manaradix"
"""gettext domain for this application."""


def _find_locale_dir() -> str:
    """Return the best-guess locale directory path."""
    env_dir = os.environ.get("MANARADIX_LOCALE_DIR")
    if env_dir:
        return env_dir

    system_dir = "/usr/share/locale"
    if os.path.isdir(system_dir):
        return system_dir

    local_dir = os.path.join(os.path.dirname(__file__), "..", "locale")
    return os.path.abspath(local_dir)


def init_i18n() -> None:
    """Bind the ``manaradix`` gettext domain.

    Called once at package import time.  If no translations are
    available the ``_()`` function returns strings unchanged.
    """
    try:
        locale_dir = _find_locale_dir()
        gettext.bindtextdomain(DOMAIN, locale_dir)
        gettext.textdomain(DOMAIN)
    except Exception:
        # gettext is always available in the stdlib, but bindtextdomain
        # may fail in minimal environments — degrade gracefully.
        pass


def _(message: str) -> str:
    """Mark *message* for translation.

    Returns the translated string if a ``.mo`` file is available for
    the current locale, otherwise returns *message* unchanged.
    """
    return gettext.dgettext(DOMAIN, message)
