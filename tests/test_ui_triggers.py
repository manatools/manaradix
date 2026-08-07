"""Tests for the Triggers page (view + enable/disable of snapshot triggers)."""

from unittest.mock import MagicMock

from manaradix.core.config_store import ConfigStore
from manaradix.model.policy import SnapshotPolicy
from manaradix.ui.pages.triggers import _UNIT_RE, TriggersPage


def _policy(subvolume="/", *schedules: str, enabled: bool = True) -> SnapshotPolicy:
    return SnapshotPolicy(
        subvolume=subvolume,
        enabled=enabled,
        schedule_hourly="hourly" in schedules,
        schedule_daily="daily" in schedules,
        schedule_weekly="weekly" in schedules,
        schedule_monthly="monthly" in schedules,
        schedule_boot="boot" in schedules,
    )


def _make_page(
    tmp_path,
    *,
    policies=None,
    units=None,
    unit_rows=None,
    snapshot_before_upgrade=False,
):
    """A TriggersPage over a real ConfigStore and a mocked engine."""
    store = ConfigStore(str(tmp_path / "config.toml"))
    for policy in policies or []:
        store.set_policy(policy)
    store.snapshot_before_upgrade = snapshot_before_upgrade

    engine = MagicMock()
    engine.list_generated_units.return_value = units or []
    rows = unit_rows or {}

    def fake_unit_status(subvolume, schedule):
        return rows.get(
            (subvolume, schedule),
            {"state": "unknown", "last_trigger": None, "next_trigger": None},
        )

    engine.unit_status.side_effect = fake_unit_status
    snapshots = MagicMock()
    snapshots.list_all.return_value = []

    app = MagicMock()
    app.config_store = store
    app.policy_engine = engine
    app.snapshot_manager = snapshots

    # ActivityStatus reads run records from this path.
    import manaradix.core.activity_status as activity_status_mod

    activity_status_mod.DEFAULT_RECORDS_PATH = str(tmp_path / "runs.jsonl")

    return TriggersPage(app), app


def _row(page, subvolume, schedule):
    for row in page.build_model().sections[0]["fields"][0]["rows"]:
        if row["Subvolume"] == subvolume and row["Schedule"] == schedule:
            return row
    return None


class TestUnitNameParsing:
    def test_root_subvolume(self) -> None:
        m = _UNIT_RE.match("manaradix-snapshot-root-daily.timer")
        assert m is not None
        assert m.group("subvol") == "root"
        assert m.group("schedule") == "daily"

    def test_nested_subvolume(self) -> None:
        m = _UNIT_RE.match("manaradix-snapshot-home-weekly.service")
        assert m.group("subvol") == "home"
        assert m.group("schedule") == "weekly"

    def test_boot_service(self) -> None:
        m = _UNIT_RE.match("manaradix-snapshot-root-boot.service")
        assert m.group("schedule") == "boot"

    def test_unrelated_unit_rejected(self) -> None:
        assert _UNIT_RE.match("manaradix-dbus.service") is None


class TestCollectRows:
    def test_policy_schedules_become_rows(self, tmp_path) -> None:
        page, _ = _make_page(
            tmp_path,
            policies=[_policy("/", "daily", "weekly"), _policy("/home", "daily")],
            unit_rows={
                ("/", "daily"): {"state": "ok", "last_trigger": "1",
                                 "next_trigger": "2",
                                 "last_exec": "1786000000", "last_result": "success"},
                ("/", "weekly"): {"state": "never", "last_trigger": None,
                                  "next_trigger": "2"},
                ("/home", "daily"): {"state": "ok", "last_trigger": "1",
                                     "next_trigger": "2",
                                     "last_exec": "1786000000", "last_result": "success"},
            },
        )
        rows = page.build_model().sections[0]["fields"][0]["rows"]
        assert [(r["Subvolume"], r["Schedule"]) for r in rows] == [
            ("/", "daily"), ("/", "pre-upgrade"),
            ("/", "weekly"), ("/home", "daily"),
        ]
        assert rows[0]["Mechanism"] == "systemd timer"
        # Only a start attempt (systemd evidence, no record/snapshot) —
        # a fired timer with no proven run is "never", not "ok".
        assert rows[0]["State"] == "never"
        assert rows[0]["Next run"] != "-"
        # The pre-upgrade row sorts in between; the weekly row follows.
        assert rows[2]["State"] == "never"
        assert rows[2]["Schedule"] == "weekly"

    def test_orphan_units_become_rows(self, tmp_path) -> None:
        page, _ = _make_page(
            tmp_path,
            units=["manaradix-snapshot-root-monthly.timer",
                   "manaradix-snapshot-root-monthly.service"],
        )
        row = _row(page, "/", "monthly")
        assert row is not None
        assert row["State"] == "unknown"

    def test_disabled_policy_has_no_rows(self, tmp_path) -> None:
        page, _ = _make_page(
            tmp_path, policies=[_policy("/", "daily", enabled=False)]
        )
        assert _row(page, "/", "daily") is None

    def test_boot_tier_never_without_evidence(self, tmp_path) -> None:
        page, _ = _make_page(
            tmp_path,
            policies=[_policy("/", "boot")],
            unit_rows={
                ("/", "boot"): {"state": "ok", "last_trigger": None,
                                "next_trigger": None},
            },
        )
        row = _row(page, "/", "boot")
        assert row["State"] == "never"
        assert row["Last run"] == "never"
        assert row["Next run"] == "next boot"

    def test_pre_upgrade_row_flag_off(self, tmp_path) -> None:
        page, _ = _make_page(tmp_path, snapshot_before_upgrade=False)
        row = _row(page, "/", "pre-upgrade")
        assert row is not None
        assert row["Mechanism"] == "rpm file trigger"
        assert row["State"] == "disabled"
        assert row["Next run"] == "next package update"

    def test_pre_upgrade_row_flag_on_script_missing(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("os.path.isfile", lambda path: False)
        page, _ = _make_page(tmp_path, snapshot_before_upgrade=True)
        row = _row(page, "/", "pre-upgrade")
        assert row["State"] == "script missing"

    def test_pre_upgrade_row_flag_on_script_installed(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("os.path.isfile", lambda path: True)
        page, _ = _make_page(tmp_path, snapshot_before_upgrade=True)
        row = _row(page, "/", "pre-upgrade")
        assert row["State"] == "enabled"


class TestTriggerActions:
    def test_enable_systemd_trigger(self, tmp_path) -> None:
        page, app = _make_page(
            tmp_path, policies=[_policy("/", "daily")]
        )
        page._selected = {"Subvolume": "/", "Schedule": "weekly"}
        page.on_enable_trigger()
        policy = app.config_store.get_policy("/")
        assert policy.schedule_weekly is True
        assert policy.enabled is True
        app.policy_engine.apply_policy.assert_called_once()

    def test_disable_keeps_remaining_schedules(self, tmp_path) -> None:
        page, app = _make_page(
            tmp_path, policies=[_policy("/", "daily", "weekly")]
        )
        page._selected = {"Subvolume": "/", "Schedule": "weekly"}
        page.on_disable_trigger()
        policy = app.config_store.get_policy("/")
        assert policy.schedule_weekly is False
        assert policy.enabled is True
        app.policy_engine.apply_policy.assert_called_once()

    def test_disable_last_schedule_disables_policy_and_removes_units(
        self, tmp_path
    ) -> None:
        page, app = _make_page(
            tmp_path, policies=[_policy("/", "weekly")]
        )
        page._selected = {"Subvolume": "/", "Schedule": "weekly"}
        page.on_disable_trigger()
        policy = app.config_store.get_policy("/")
        assert policy.active_schedules == []
        assert policy.enabled is False
        app.policy_engine.apply_policy.assert_not_called()
        app.policy_engine.remove_policy.assert_called_once_with("/")

    def test_enable_orphan_creates_policy(self, tmp_path) -> None:
        page, app = _make_page(tmp_path)
        page._selected = {"Subvolume": "/", "Schedule": "monthly"}
        page.on_enable_trigger()
        policy = app.config_store.get_policy("/")
        assert policy.schedule_monthly is True
        assert policy.enabled is True
        app.policy_engine.apply_policy.assert_called_once()

    def test_disable_orphan_removes_units(self, tmp_path) -> None:
        page, app = _make_page(tmp_path)
        page._selected = {"Subvolume": "/", "Schedule": "monthly"}
        page.on_disable_trigger()
        app.policy_engine.remove_policy.assert_called_once_with("/")
        assert app.config_store.get_policy("/") is None

    def test_enable_pre_upgrade_sets_flag(self, tmp_path) -> None:
        page, app = _make_page(tmp_path, snapshot_before_upgrade=False)
        page._selected = {"Subvolume": "/", "Schedule": "pre-upgrade"}
        page.on_enable_trigger()
        assert app.config_store.snapshot_before_upgrade is True
        app.policy_engine.apply_policy.assert_not_called()

    def test_disable_pre_upgrade_clears_flag(self, tmp_path) -> None:
        page, app = _make_page(tmp_path, snapshot_before_upgrade=True)
        page._selected = {"Subvolume": "/", "Schedule": "pre-upgrade"}
        page.on_disable_trigger()
        assert app.config_store.snapshot_before_upgrade is False

    def test_no_selection_is_noop(self, tmp_path) -> None:
        page, app = _make_page(
            tmp_path, policies=[_policy("/", "daily")]
        )
        page.on_enable_trigger()
        app.policy_engine.apply_policy.assert_not_called()

    def test_select_stores_row(self, tmp_path) -> None:
        page, _ = _make_page(tmp_path)
        page.on_select_trigger({"Subvolume": "/", "Schedule": "daily"})
        assert page._selected["Schedule"] == "daily"

    def test_selection_survives_rebuild(self, tmp_path) -> None:
        """The stored row is re-synced onto the freshly built rows and
        handed back to the tree, so the re-render after a click restores
        the highlight instead of wiping it."""
        page, _ = _make_page(
            tmp_path, policies=[_policy("/", "daily", "weekly")]
        )
        page.on_select_trigger(_row(page, "/", "daily"))
        tree = page.build_model().sections[0]["fields"][0]
        assert tree["selected_row"] is not None
        assert tree["selected_row"]["Subvolume"] == "/"
        assert tree["selected_row"]["Schedule"] == "daily"

    def test_selection_cleared_when_trigger_vanishes(self, tmp_path) -> None:
        """A trigger that disappears (last schedule disabled behind the
        page's back) clears the stored selection: Enable/Disable must not
        stay aimed at a dead row."""
        page, app = _make_page(tmp_path, policies=[_policy("/", "daily")])
        page.on_select_trigger(_row(page, "/", "daily"))
        policy = app.config_store.get_policy("/")
        policy.schedule_daily = False
        app.config_store.save()
        page.build_model()
        assert page._selected is None

    def test_failed_row_shows_systemd_exec_evidence(self, tmp_path) -> None:
        # No records and no snapshots, but systemd knows the service ran
        # and failed: the row shows when, instead of "never" with no
        # evidence.
        from manaradix.core.policy_engine import PolicyEngine

        page, app = _make_page(
            tmp_path,
            policies=[_policy("/", "daily")],
            unit_rows={
                ("/", "daily"): {
                    "state": "failed",
                    "last_trigger": None,
                    "next_trigger": None,
                    "last_exec": "1786053742",
                    "last_result": "failed",
                },
            },
        )
        app.policy_engine._parse_epoch = PolicyEngine._parse_epoch
        row = _row(page, "/", "daily")
        assert row["State"] == "failed"
        assert row["Last run"].endswith("(failed)")

    def test_never_row_stays_never_without_evidence(self, tmp_path) -> None:
        # A service that never executed shows "never", not "failed".
        page, _ = _make_page(
            tmp_path,
            policies=[_policy("/", "weekly")],
            unit_rows={
                ("/", "weekly"): {
                    "state": "never",
                    "last_trigger": None,
                    "next_trigger": None,
                    "last_exec": None,
                    "last_result": None,
                },
            },
        )
        row = _row(page, "/", "weekly")
        assert row["State"] == "never"
        assert row["Last run"] == "never"

    def test_start_attempt_row_shows_attempt_not_ok(self, tmp_path) -> None:
        # ExecMainStartTimestamp set but no record/snapshot: a start
        # attempt (203 or reset-failed placeholder), not a proven run.
        from manaradix.core.policy_engine import PolicyEngine

        page, app = _make_page(
            tmp_path,
            policies=[_policy("/", "daily")],
            unit_rows={
                ("/", "daily"): {
                    "state": "ok",
                    "last_trigger": "1786053742",
                    "next_trigger": None,
                    "last_exec": "@1786053742",
                    "last_result": "success",
                },
            },
        )
        app.policy_engine._parse_epoch = PolicyEngine._parse_epoch
        row = _row(page, "/", "daily")
        assert row["State"] == "never"
        assert row["Last run"].endswith("(attempt)")
        assert row["Last run"].startswith("2026-08-07")
