"""Manaradix — Policies page.

Configure automated snapshot schedules and retention rules per subvolume.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from manaradix.i18n import _
from manaradix.model.policy import RetentionPolicy, SnapshotPolicy
from manaradix.ui.model import PageModel, Widget

if TYPE_CHECKING:
    from manaradix.module import AppContext

logger = logging.getLogger(__name__)

_SCHEDULE_KEYS = ["hourly", "daily", "weekly", "monthly", "boot"]
_RETENTION_KEYS = [
    "keep_hourly",
    "keep_daily",
    "keep_weekly",
    "keep_monthly",
    "keep_boot",
    "keep_pre_upgrade",
]

_SCHEDULE_LABELS = {
    "hourly": _("Hourly"),
    "daily": _("Daily"),
    "weekly": _("Weekly"),
    "monthly": _("Monthly"),
    "boot": _("Boot"),
}

_RETENTION_LABELS = {
    "keep_hourly": _("Keep hourly"),
    "keep_daily": _("Keep daily"),
    "keep_weekly": _("Keep weekly"),
    "keep_monthly": _("Keep monthly"),
    "keep_boot": _("Keep boot"),
    "keep_pre_upgrade": _("Keep pre-upgrade"),
}


class PoliciesPage:
    """Snapshot policy configuration page."""

    page_id = "policies"
    title = _("Policies")

    def __init__(self, app_context: AppContext) -> None:
        self.app = app_context
        # Currently selected subvolume for editing
        self._selected_subvolume: str | None = None

    # -- build_model --------------------------------------------------------

    def build_model(self):
        """Return a ``PageModel`` for policy management."""
        policies = self._list_policies()
        sections = [
            self._policies_list_section(policies),
            self._policy_editor_section(policies),
            self._systemd_status_section(),
        ]
        return PageModel(sections=[s for s in sections if s is not None])

    # -- Section builders ---------------------------------------------------

    def _policies_list_section(self, policies: list[SnapshotPolicy]) -> dict:
        """Table of configured policies."""
        if not policies:
            return {
                "title": _("Configured Policies"),
                "fields": [Widget.label(id="no_policies", text=_("No policies configured."))],
            }

        return {
            "title": _("Configured Policies"),
            "fields": [
                Widget.tree(
                    id="policies_tree",
                    rows=[
                        {
                            "Subvolume": p.subvolume,
                            "Enabled": str(p.enabled),
                            "Schedules": ", ".join(p.active_schedules) if p.active_schedules else "none",
                            "Keep H/D/W/M/B/P": (
                                f"{p.retention.keep_hourly}/{p.retention.keep_daily}/"
                                f"{p.retention.keep_weekly}/{p.retention.keep_monthly}/"
                                f"{p.retention.keep_boot}/{p.retention.keep_pre_upgrade}"
                            ),
                        }
                        for p in policies
                    ],
                    selectable=True,
                    on_select=self.on_select_policy,
                    selected_row=(
                        {"Subvolume": self._selected_subvolume}
                        if self._selected_subvolume else None
                    ),
                ),
            ],
        }

    def _policy_editor_section(self, policies: list[SnapshotPolicy]) -> dict | None:
        """Editor form for the selected policy, or a form to add a new one."""
        policy = self._get_selected_policy(policies)

        if policy is None:
            # No policy selected — show "add policy" form
            return {
                "title": _("Add Policy"),
                "fields": [
                    Widget.text(id="new_subvolume", label=_("Subvolume path"), value="/"),
                    Widget.button(
                        id="btn_add_policy",
                        label=_("Add Policy"),
                        callback=self.on_add_policy,
                    ),
                ],
            }

        # Schedule toggles — each checkbox carries a closure so its
        # callback knows which schedule it belongs to (the renderer hands
        # the new checked value to every on_change).
        schedule_fields = []
        for key in _SCHEDULE_KEYS:
            active = key in policy.active_schedules
            schedule_fields.append(
                Widget.checkbox(
                    id=f"schedule_{key}",
                    label=_("On %s") % _SCHEDULE_LABELS[key],
                    checked=active,
                    on_change=lambda value, k=key: self.on_schedule_toggle(k, value),
                )
            )

        # Retention spinners
        retention_fields = []
        for key in _RETENTION_KEYS:
            current = getattr(policy.retention, key, 0)
            retention_fields.append(
                Widget.spinner(
                    id=f"retention_{key}",
                    label=_RETENTION_LABELS[key],
                    value=current,
                    min_val=0,
                    max_val=999,
                    on_change=lambda value, k=key: self.on_retention_change(k, value),
                )
            )

        return {
            "title": _("Edit Policy: %s") % policy.subvolume,
            "fields": [
                Widget.checkbox(
                    id="policy_enabled",
                    label=_("Enabled"),
                    checked=policy.enabled,
                    on_change=self.on_enabled_toggle,
                ),
                *schedule_fields,
                *retention_fields,
                Widget.button(
                    id="btn_apply_policy",
                    label=_("Apply Policy"),
                    callback=self.on_apply_policy,
                ),
                Widget.button(
                    id="btn_apply_dry_run",
                    label=_("Dry-Run Preview"),
                    callback=self.on_dry_run_policy,
                ),
                Widget.button(
                    id="btn_remove_policy",
                    label=_("Remove Policy"),
                    callback=self.on_remove_policy,
                ),
            ],
        }

    def _systemd_status_section(self) -> dict:
        """Show installed systemd units."""
        try:
            units = self.app.policy_engine.list_generated_units()
        except Exception as exc:
            logger.warning("Could not list systemd units: %s", exc)
            units = []

        if not units:
            return {
                "title": _("systemd Units"),
                "fields": [Widget.label(id="no_units", text=_("No units installed."))],
            }

        return {
            "title": _("systemd Units"),
            "fields": [
                Widget.tree(
                    id="systemd_units",
                    rows=[
                        {
                            "Unit": u,
                            "Status": self.app.policy_engine.unit_state(u),
                        }
                        for u in units
                    ],
                    selectable=False,
                ),
            ],
        }

    # -- Callbacks: selection -----------------------------------------------

    def on_select_policy(self, row: dict) -> None:
        """Called when a policy row is selected."""
        self._selected_subvolume = row.get("Subvolume")

    # -- Callbacks: policy CRUD ---------------------------------------------

    def on_add_policy(self) -> None:
        """Add a new policy for the entered subvolume.

        In a full UI the subvolume value would be read from the text widget.
        """
        try:
            policy = SnapshotPolicy(subvolume="/")
            self.app.config_store.set_policy(policy)
            self._selected_subvolume = "/"
            logger.info("Added policy for /")
        except Exception as exc:
            logger.error("Failed to add policy: %s", exc)

    def on_schedule_toggle(self, schedule: str, value: bool | None = None) -> None:
        """Set one schedule on/off for the selected policy and re-apply.

        Args:
            schedule: The schedule tier (hourly/daily/weekly/monthly/boot).
            value: The new checked state (handed by the renderer); None
                toggles the current state.
        """
        policy = self._get_selected_policy(self._list_policies())
        if policy is None:
            return
        if value is None:
            value = schedule in policy.active_schedules
        updated = self.app.config_store.toggle_schedule(
            policy.subvolume, schedule, bool(value)
        )
        if updated is None:
            return
        self._save_and_apply(updated)

    def on_retention_change(self, key: str, value: int | None = None) -> None:
        """Update one retention value for the selected policy.

        Retention is read from the config at prune time, so a config
        save is all that is needed — no unit regeneration.

        Args:
            key: The retention field (``keep_hourly`` ... ``keep_pre_upgrade``).
            value: The new value (handed by the renderer); None keeps
                the current value.
        """
        policy = self._get_selected_policy(self._list_policies())
        if policy is None or not hasattr(policy.retention, key):
            return
        try:
            setattr(policy.retention, key, int(value))
        except (TypeError, ValueError) as exc:
            logger.error("Invalid retention value %r for %s: %s", value, key, exc)
            return
        try:
            self.app.config_store.save()
        except Exception as exc:
            logger.error("Failed to save retention change: %s", exc)

    def on_enabled_toggle(self, value: bool | None = None) -> None:
        """Toggle the policy enabled state and re-apply.

        Args:
            value: The new checked state (handed by the renderer); None
                toggles the current state.
        """
        policy = self._get_selected_policy(self._list_policies())
        if policy is None:
            return
        if value is None:
            value = not policy.enabled
        policy.enabled = bool(value)
        self._save_and_apply(policy)

    def on_apply_policy(self) -> None:
        """Write the selected policy to config and generate systemd units."""
        policy = self._get_selected_policy(self._list_policies())
        if policy is None:
            return
        self._save_and_apply(policy)

    def on_dry_run_policy(self) -> None:
        """Preview what systemd units would be created/removed."""
        policy = self._get_selected_policy(self._list_policies())
        if policy is None:
            return
        try:
            written = self.app.policy_engine.apply_policy(policy, dry_run=True)
            for path in written:
                logger.info("[DRY RUN] %s", path)
        except Exception as exc:
            logger.error("Dry-run failed: %s", exc)

    def on_remove_policy(self) -> None:
        """Remove the selected policy and its systemd units."""
        policy = self._get_selected_policy(self._list_policies())
        if policy is None:
            return
        try:
            self.app.policy_engine.remove_policy(policy.subvolume)
            self.app.config_store.remove_policy(policy.subvolume)
            self.app.config_store.save()
            self._selected_subvolume = None
            logger.info("Removed policy for %s", policy.subvolume)
        except Exception as exc:
            logger.error("Failed to remove policy: %s", exc)

    # -- Helpers ------------------------------------------------------------

    def _list_policies(self) -> list[SnapshotPolicy]:
        try:
            return self.app.config_store.list_policies()
        except Exception as exc:
            logger.warning("Could not list policies: %s", exc)
            return []

    def _get_selected_policy(
        self, policies: list[SnapshotPolicy]
    ) -> SnapshotPolicy | None:
        if not self._selected_subvolume:
            return None
        for p in policies:
            if p.subvolume == self._selected_subvolume:
                return p
        return None

    def _save_and_apply(self, policy: SnapshotPolicy) -> None:
        """Persist *policy* and bring its systemd units in line.

        A policy with no schedules left is disabled — there is nothing
        to apply, so its units are removed instead of regenerated.
        """
        try:
            self.app.config_store.save()
            if policy.active_schedules:
                written = self.app.policy_engine.apply_policy(policy)
                logger.info(
                    "Applied policy for %s: %d units written",
                    policy.subvolume, len(written),
                )
            else:
                self.app.policy_engine.remove_policy(policy.subvolume)
                logger.info(
                    "Removed units for %s (no schedules left)",
                    policy.subvolume,
                )
        except Exception as exc:
            logger.error("Failed to apply policy change: %s", exc)
