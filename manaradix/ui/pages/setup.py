"""Manaradix — First-run Setup Wizard page.

Proposed when the system has no snapshots and no snapshot policies
(spec.md §15): lists the setup profiles available on this system, auto-
selects a default from the detected role (graphical → desktop, headless
→ server) and lets the user switch with a dropdown.  The plan is adapted
to the actual layout — only btrfs subvolumes get policies, and boot
snapshots / the GRUB recovery menu only when the root filesystem is
btrfs.  Applying runs the setup in one step; after applying (or
skipping) the page shows the result and the user can continue to the
normal pages.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from manaradix.core.setup_wizard import (
    apply_profile,
    build_plan,
    detect_role,
    select_default_profile,
    system_layout,
)
from manaradix.core.templates import SetupProfile, load_profiles
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
    """First-run wizard proposing a setup profile for this system."""

    page_id = "setup"
    title = _("Setup Wizard")

    def __init__(self, app_context: AppContext) -> None:
        self.app = app_context
        self._summary: dict[str, Any] | None = None
        self._skipped = False
        self._profiles: list[SetupProfile] = []
        self._profile_id: str | None = None
        self._layout: dict[str, Any] | None = None

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

        self._ensure_state()
        profile = self._selected_profile()

        if not self._profiles or profile is None:
            return PageModel(sections=[
                {
                    "title": _("Setup Wizard"),
                    "fields": [
                        Widget.label(
                            id="setup_no_profiles",
                            text=_("No setup profiles are available on this system."),
                        ),
                    ],
                },
                self._actions_section(with_apply=False),
            ])

        if not self._layout.get("btrfs_targets"):
            return PageModel(sections=[
                {
                    "title": _("Setup Wizard"),
                    "fields": [
                        Widget.label(
                            id="setup_no_btrfs",
                            text=_(
                                "No btrfs filesystem was found on this system. "
                                "Manaradix only manages btrfs storage, so there "
                                "is nothing to set up here."
                            ),
                        ),
                    ],
                },
                self._actions_section(with_apply=False),
            ])

        plan = build_plan(self.app, profile)

        fields: list[dict[str, Any]] = [
            Widget.label(
                id="setup_intro",
                text=_(
                    "No snapshots or snapshot policies are configured yet. "
                    "Choose a setup profile for this system and apply it:"
                ),
            ),
            Widget.dropdown(
                id="setup_profile",
                label=_("Profile"),
                options=[_(p.name) for p in self._profiles],
                selected=_(profile.name),
                on_change=self.on_profile_change,
            ),
            Widget.tree(id="setup_plan", rows=self._plan_rows(plan), selectable=False),
        ]
        for note in plan.get("notes", []):
            fields.append(Widget.label(id=f"setup_note_{len(fields)}", text=note))

        return PageModel(sections=[
            {
                "title": _("Setup Plan"),
                "fields": fields,
            },
            self._actions_section(with_apply=plan["applicable"]),
        ])

    # -- Callbacks ----------------------------------------------------------

    def on_profile_change(self, value: str | None) -> None:
        """Called when the profile dropdown changes; updates the plan."""
        for profile in self._profiles:
            if _(profile.name) == value:
                self._profile_id = profile.id
                return

    def on_apply(self) -> None:
        """Apply the selected profile and show the result summary."""
        try:
            self._summary = apply_profile(self.app, self._profile_id)
        except Exception as exc:
            logger.error("Setup failed: %s", exc)
            self._summary = {"error": str(exc)}

    def on_skip(self) -> None:
        """Skip the wizard; the regular pages remain available."""
        self._skipped = True

    # -- Helpers ------------------------------------------------------------

    def _ensure_state(self) -> None:
        """Load profiles, pick the default, probe the layout — once."""
        if not self._profiles:
            self._profiles = load_profiles()
            if self._profile_id is None:
                default = select_default_profile(self._profiles, detect_role())
                if default is not None:
                    self._profile_id = default.id
        if self._layout is None:
            self._layout = system_layout(self.app)

    def _selected_profile(self) -> SetupProfile | None:
        for profile in self._profiles:
            if profile.id == self._profile_id:
                return profile
        return None

    def _actions_section(self, *, with_apply: bool) -> dict[str, Any]:
        fields = []
        if with_apply:
            fields.append(Widget.button(
                id="btn_setup_apply",
                label=_("Apply Setup"),
                callback=self.on_apply,
            ))
        fields.append(Widget.button(
            id="btn_setup_skip",
            label=_("Skip"),
            callback=self.on_skip,
        ))
        return {"title": _("Actions"), "fields": fields}

    @staticmethod
    def _plan_rows(plan: dict[str, Any]) -> list[dict[str, str]]:
        """The plan as a table of Item/Value rows."""
        rows: list[dict[str, str]] = []
        for entry in plan["policies"]:
            rows.append({
                "Item": _("Subvolume: %s") % entry["subvolume"],
                "Value": (
                    _("Not available on this system")
                    if not entry["available"]
                    else ", ".join(
                        _SCHEDULE_LABELS.get(s, s) for s in entry["schedules"]
                    )
                ),
            })
            if entry["available"]:
                rows.append({
                    "Item": _("Retention"),
                    "Value": ", ".join(
                        f"{_SCHEDULE_LABELS.get(k.removeprefix('keep_'), k)}={v}"
                        for k, v in entry["retention"].items()
                    ),
                })
        rows.append({
            "Item": _("Snapshot directory"),
            "Value": plan["snapshot_dir"],
        })
        rows.append({
            "Item": _("Boot-from-snapshot (GRUB menu)"),
            "Value": (
                _("Enabled")
                if plan["boot_from_snapshot_effective"]
                else _("Not available on this system")
            ),
        })
        return rows

    def _summary_section(self, summary: dict[str, Any]) -> PageModel:
        if "error" in summary:
            return PageModel(sections=[
                {
                    "title": _("Setup Failed"),
                    "fields": [
                        Widget.label(
                            id="setup_error",
                            text=_("The setup could not be applied: %s")
                            % summary["error"],
                        ),
                    ],
                },
            ])

        fields = [
            Widget.label(
                id="setup_done",
                text=_("The setup is now in place:"),
            ),
        ]
        for entry in summary.get("policies", []):
            schedules = ", ".join(
                _SCHEDULE_LABELS.get(s, s) for s in entry.get("schedules", [])
            )
            retention = ", ".join(
                f"{_SCHEDULE_LABELS.get(k.removeprefix('keep_'), k)}={v}"
                for k, v in entry.get("retention", {}).items()
            )
            fields.append(Widget.label(
                id=f"setup_summary_policy_{entry.get('subvolume', 'root')}",
                text=_("Subvolume: %s — %s (retention: %s)")
                % (entry.get("subvolume", "?"), schedules, retention),
            ))
        grub = _("installed") if summary.get("grub_installed") else _("not installed")
        fields.append(Widget.label(
            id="setup_summary_grub",
            text=_("GRUB recovery menu: %s") % grub,
        ))
        return PageModel(sections=[
            {
                "title": _("Setup Applied"),
                "fields": fields,
            },
        ])
