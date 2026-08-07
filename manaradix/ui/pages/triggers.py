"""Manaradix — Triggers page.

Operational view of every snapshot trigger on the system: the systemd
timer units generated for policies, the boot oneshot, and the rpm
pre-upgrade file trigger.  Rows show the live systemd state plus the
last-run evidence; Enable/Disable flip one trigger — updating both the
config policy and the systemd units — without touching the rest of the
policy.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from typing import TYPE_CHECKING

from manaradix.core import activity_status as activity_status_mod
from manaradix.core.activity_status import ActivityStatus, merge_never
from manaradix.i18n import _
from manaradix.model.policy import SnapshotPolicy
from manaradix.ui.model import PageModel, Widget

if TYPE_CHECKING:
    from manaradix.module import AppContext

logger = logging.getLogger(__name__)

#: Pre-upgrade trigger script installed by the RPM (see
#: data/libexec/manaradix-pre-upgrade).
_PRE_UPGRADE_SCRIPT = "/usr/libexec/manaradix/pre-upgrade"

#: Parse generated unit names back into (subvolume, schedule).  The
#: sanitized fragment is reversible: "/" becomes "root", "/home" becomes
#: "home" (PolicyEngine._sanitize_name).
_UNIT_RE = re.compile(
    r"^manaradix-snapshot-(?P<subvol>.+?)-"
    r"(?P<schedule>hourly|daily|weekly|monthly|boot)"
    r"\.(?:service|timer)$"
)


def _mechanism_for(schedule: str) -> str:
    """Human mechanism label for a schedule tier."""
    if schedule == "boot":
        return _("systemd service (boot)")
    return _("systemd timer")


class TriggersPage:
    """Snapshot trigger overview and control page."""

    page_id = "triggers"
    title = _("Triggers")

    def __init__(self, app_context: AppContext) -> None:
        self.app = app_context
        # The currently selected trigger row (None until a selection).
        self._selected: dict | None = None
        self._activity = ActivityStatus(
            app_context.policy_engine,
            app_context.snapshot_manager,
            app_context.config_store,
            records_path=activity_status_mod.DEFAULT_RECORDS_PATH,
        )

    # -- build_model --------------------------------------------------------

    def build_model(self):
        """Return a ``PageModel`` with one row per trigger."""
        rows = self._collect_rows()
        sections = [
            self._triggers_section(rows),
            self._actions_section(bool(rows)),
        ]
        return PageModel(sections=[s for s in sections if s is not None])

    # -- Row collection -----------------------------------------------------

    def _collect_rows(self) -> list[dict]:
        """One row per (subvolume, schedule) with state and run evidence.

        Sources: every enabled policy × its active schedules, orphan
        units found on disk (units without a matching policy), and the
        rpm pre-upgrade trigger.  Disabled policies have no units, so
        they contribute nothing here — the Policies page is where a
        disabled policy is re-enabled.
        """
        rows: dict[tuple[str, str], dict] = {}

        try:
            policies = self.app.config_store.list_policies()
        except Exception as exc:
            logger.warning("Could not list policies: %s", exc)
            policies = []
        for policy in policies:
            if not policy.enabled:
                continue
            for schedule in policy.active_schedules:
                rows[(policy.subvolume, schedule)] = self._trigger_row(
                    policy.subvolume, schedule
                )

        try:
            units = self.app.policy_engine.list_generated_units()
        except Exception as exc:
            logger.warning("Could not list systemd units: %s", exc)
            units = []
        for unit in units:
            m = _UNIT_RE.match(unit)
            if not m:
                continue
            subvol = "/" if m.group("subvol") == "root" else "/" + m.group("subvol")
            pair = (subvol, m.group("schedule"))
            if pair not in rows:
                rows[pair] = self._trigger_row(*pair)

        rows[("/", "pre-upgrade")] = self._pre_upgrade_row()
        return [rows[key] for key in sorted(rows)]

    def _trigger_row(self, subvolume: str, schedule: str) -> dict:
        """One systemd-backed trigger row: live state + run evidence."""
        try:
            status = self.app.policy_engine.unit_status(subvolume, schedule)
        except Exception as exc:
            logger.warning(
                "Could not read unit status for %s/%s: %s",
                subvolume, schedule, exc,
            )
            status = {"state": "unknown", "last_trigger": None, "next_trigger": None}
        last = None
        try:
            last = self._activity.last_run_with_fallback(
                subvolume, schedule, status
            )
        except Exception as exc:
            logger.warning(
                "Could not read last run for %s/%s: %s",
                subvolume, schedule, exc,
            )
        return {
            "Subvolume": subvolume,
            "Schedule": schedule,
            "Mechanism": _mechanism_for(schedule),
            "State": merge_never(
                status["state"], last,
                status.get("last_trigger"),
            ),
            "Last run": self._fmt_run(last),
            "Next run": self._fmt_next(status.get("next_trigger"), schedule),
        }

    def _pre_upgrade_row(self) -> dict:
        """The rpm file trigger: config flag + installed script + evidence."""
        enabled = bool(self.app.config_store.snapshot_before_upgrade)
        if enabled and not os.path.isfile(_PRE_UPGRADE_SCRIPT):
            state = _("script missing")
        else:
            state = _("enabled") if enabled else _("disabled")
        last = None
        try:
            last = self._activity.last_run("/", "pre-upgrade")
        except Exception as exc:
            logger.warning("Could not read pre-upgrade last run: %s", exc)
        return {
            "Subvolume": "/",
            "Schedule": "pre-upgrade",
            "Mechanism": _("rpm file trigger"),
            "State": state,
            "Last run": self._fmt_run(last),
            "Next run": _("next package update"),
        }

    # -- Sections -----------------------------------------------------------

    def _triggers_section(self, rows: list[dict]) -> dict:
        """Table of triggers with a hint under it."""
        fields: list[dict] = []
        if rows:
            fields.append(
                Widget.tree(
                    id="triggers_tree",
                    rows=rows,
                    selectable=True,
                    on_select=self.on_select_trigger,
                )
            )
        else:
            fields.append(
                Widget.label(
                    id="no_triggers",
                    text=_("No triggers are configured."),
                )
            )
        fields.append(
            Widget.label(
                id="triggers_hint",
                text=_(
                    "Select a trigger and use Enable/Disable. Schedules and "
                    "retention are edited on the Policies tab."
                ),
            )
        )
        return {"title": _("Configured Triggers"), "fields": fields}

    def _actions_section(self, has_rows: bool) -> dict | None:
        """Enable/Disable actions for the selected trigger."""
        if not has_rows:
            return None
        return {
            "title": _("Actions"),
            "fields": [
                Widget.button(
                    id="btn_enable_trigger",
                    label=_("Enable Selected"),
                    callback=self.on_enable_trigger,
                ),
                Widget.button(
                    id="btn_disable_trigger",
                    label=_("Disable Selected"),
                    callback=self.on_disable_trigger,
                ),
                Widget.button(
                    id="btn_refresh",
                    label=_("Refresh"),
                    callback=self.on_refresh,
                ),
            ],
        }

    # -- Callbacks ----------------------------------------------------------

    def on_select_trigger(self, row: dict) -> None:
        """Remember the selected trigger row."""
        self._selected = row or None

    def on_enable_trigger(self) -> None:
        """Enable the selected trigger."""
        self._set_trigger(True)

    def on_disable_trigger(self) -> None:
        """Disable the selected trigger."""
        self._set_trigger(False)

    def on_refresh(self) -> None:
        """Request a UI refresh (the renderer re-builds the page)."""
        pass

    # -- Trigger state changes ---------------------------------------------

    def _set_trigger(self, enabled: bool) -> None:
        """Flip the selected trigger's state (config + systemd units)."""
        row = self._selected
        if row is None:
            return
        subvolume, schedule = row["Subvolume"], row["Schedule"]
        try:
            if schedule == "pre-upgrade":
                self.app.config_store.snapshot_before_upgrade = enabled
                self.app.config_store.save()
                return
            updated = self.app.config_store.toggle_schedule(
                subvolume, schedule, enabled
            )
            if updated is None:
                # An orphan unit with no policy: create one for it (or,
                # when disabling, just tear the units down).
                if not enabled:
                    self.app.policy_engine.remove_policy(subvolume)
                    return
                policy = SnapshotPolicy(subvolume=subvolume, schedule_daily=False)
                setattr(policy, f"schedule_{schedule}", True)
                self.app.config_store.set_policy(policy)
                updated = policy
            self.app.config_store.save()
            if not enabled and not updated.active_schedules:
                # Last schedule disabled: the policy is off — remove the
                # units rather than regenerate from a schedule-less policy.
                self.app.policy_engine.remove_policy(subvolume)
            else:
                self.app.policy_engine.apply_policy(updated)
        except Exception as exc:
            logger.error(
                "Failed to %s trigger %s/%s: %s",
                "enable" if enabled else "disable",
                subvolume, schedule, exc,
            )

    # -- Formatting ---------------------------------------------------------

    @staticmethod
    def _fmt_run(last: dict | None) -> str:
        """Format a last-run evidence dict, marking non-successes."""
        if last is None:
            return _("never")
        ts = last["ts"]
        if last.get("result") not in (None, "success"):
            return _("%s (%s)") % (ts, last["result"])
        return ts

    @staticmethod
    def _fmt_next(next_trigger: str | None, schedule: str) -> str:
        """Format the next timer fire as local time."""
        if schedule == "boot":
            return _("next boot")
        if not next_trigger:
            return "-"
        try:
            return datetime.fromtimestamp(int(next_trigger)).strftime("%Y-%m-%d %H:%M")
        except (ValueError, OSError):
            return "-"
