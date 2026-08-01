"""Template metadata strings, re-declared for xgettext extraction.

Profile ``name``/``description`` values live in ``data/templates/*.toml``,
which the xgettext invocation cannot scan.  This module re-declares the
exact shipped strings wrapped in ``_()`` so they land in the pot; the UI
and CLI translate them at display time (the profile ``id`` is a stable
machine key and is never translated).  ``tests/test_i18n.py`` verifies the
pot covers every shipped template's metadata.

Keep this list in sync with the TOML files under ``data/templates/``.
"""

from manaradix.i18n import _


def _anchor_template_strings() -> None:
    """Anchor for xgettext: re-declare the shipped template metadata."""
    _("Desktop workstation")
    _(
        "Daily and weekly snapshots with a boot-time recovery point, plus "
        "the GRUB recovery menu — suited for a graphical workstation."
    )
    _("Server")
    _(
        "Frequent hourly snapshots with long retention, extra coverage for "
        "data subvolumes, a boot-time recovery point and the GRUB recovery menu."
    )
    _("Kiosk")
    _(
        "Minimal setup for a single-purpose device: a snapshot at every boot "
        "with the GRUB recovery menu, no periodic snapshots."
    )
