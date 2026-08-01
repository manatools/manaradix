"""Manaradix — First-run Setup Wizard page.

Proposed when the system has no snapshots and no snapshot policies
(spec.md §15): shows the recommended baseline and lets the user apply
it in one step.  After applying (or skipping) the page shows the result
and the user can continue to the normal pages.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from manaradix.core.setup_wizard import apply_recommended, recommended_plan
from manaradix.i18n import _
from manaradix.ui.model import PageModel, Widget

if TYPE_CHECKING:
    from manaradix.module import AppContext

logger = logging.getLogger(__name__)

_SCHEDULE_LABELS = {
    "hourly": _("Hourly"),
    "daily": _("Daily"),
    "weekly": _("Weekly"),
    "monthly": _("Monthly"),
    "boot": _("Boot"),
}


class SetupWizardPage:
    """First-run wizard proposing a recommended snapshot baseline."""

    page_id = "setup"
    title = _("Setup Wizard")

    def __init__(self, app_context: AppContext) -> None:
        self.app = app_context
        self._summary: dict[str, Any] | None = None
        self._skipped = False

    # -- build_model --------------------------------------------------------

    def build_model(self):
        """Return a ``PageModel`` for the wizard."""
        if self._summary is not None:
            return self._summary_section(self._summary)
        if self._skipped:
            return PageModel(sections=[
                {
                    "title": _("Setup Skipped"),
                    "fields": [
                        Widget.label(
                            id="setup_skip_note",
                            text=_(
                                "You can configure snapshots and policies later "
                                "from the Policies and Settings pages."
                            ),
                        ),
                    ],
                },
            ])

        plan = recommended_plan()
        retention = plan["retention"]
        rows = [
            {"Item": _("Subvolume"), "Value": "/"},
            {"Item": _("Snapshots"), "Value": ", ".join(
                _SCHEDULE_LABELS.get(s, s) for s in plan["schedules"]
            )},
            {"Item": _("Retention"), "Value": ", ".join(
                f"{_SCHEDULE_LABELS.get(k.removeprefix('keep_'), k)}={v}"
                for k, v in retention.items()
            )},
            {"Item": _("Snapshot directory"), "Value": plan["snapshot_dir"]},
            {"Item": _("Boot-from-snapshot (GRUB menu)"), "Value": _("Enabled")},
        ]

        return PageModel(sections=[
            {
                "title": _("Recommended Setup"),
                "fields": [
                    Widget.label(
                        id="setup_intro",
                        text=_(
                            "No snapshots or snapshot policies are configured yet. "
                            "Apply the recommended baseline to protect this system:"
                        ),
                    ),
                    Widget.tree(id="setup_plan", rows=rows, selectable=False),
                ],
            },
            {
                "title": _("Actions"),
                "fields": [
                    Widget.button(
                        id="btn_setup_apply",
                        label=_("Apply Recommended Setup"),
                        callback=self.on_apply,
                    ),
                    Widget.button(
                        id="btn_setup_skip",
                        label=_("Skip"),
                        callback=self.on_skip,
                    ),
                ],
            },
        ])

    # -- Callbacks ----------------------------------------------------------

    def on_apply(self) -> None:
        """Apply the recommended setup and show the result summary."""
        try:
            self._summary = apply_recommended(self.app)
        except Exception as exc:
            logger.error("Setup failed: %s", exc)
            self._summary = {"error": str(exc)}

    def on_skip(self) -> None:
        """Skip the wizard; the regular pages remain available."""
        self._skipped = True

    # -- Helpers ------------------------------------------------------------

    def _summary_section(self, summary: dict[str, Any]) -> PageModel:
        if "error" in summary:
            return PageModel(sections=[
                {
                    "title": _("Setup Failed"),
                    "fields": [
                        Widget.label(
                            id="setup_error",
                            text=_("The recommended setup could not be applied: %s")
                            % summary["error"],
                        ),
                    ],
                },
            ])

        schedules = ", ".join(
            _SCHEDULE_LABELS.get(s, s) for s in summary.get("schedules", [])
        )
        retention = summary.get("retention", {})
        retention_text = ", ".join(
            f"{_SCHEDULE_LABELS.get(k.removeprefix('keep_'), k)}={v}"
            for k, v in retention.items()
        )
        grub = _("installed") if summary.get("grub_installed") else _("not installed")

        return PageModel(sections=[
            {
                "title": _("Setup Applied"),
                "fields": [
                    Widget.label(
                        id="setup_done",
                        text=_("The recommended setup is now in place:"),
                    ),
                    Widget.label(
                        id="setup_summary_schedules",
                        text=_("Snapshots: %s") % schedules,
                    ),
                    Widget.label(
                        id="setup_summary_retention",
                        text=_("Retention: %s") % retention_text,
                    ),
                    Widget.label(
                        id="setup_summary_grub",
                        text=_("GRUB recovery menu: %s") % grub,
                    ),
                ],
            },
        ])
