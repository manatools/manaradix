"""Tests for the TrayApp wiring (injected bus, loop, timers)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from manaradix.core.config_store import ConfigStore
from manaradix.core.health import HealthFinding
from manaradix.tray.app import (
    DRAIN_INTERVAL_MS,
    INSTANCE_NAME,
    TrayApp,
)
from manaradix.tray.dbus_menu import MenuModel
from manaradix.tray.status_icon import (
    SNI_STATUS_ACTIVE,
    SNI_STATUS_NEEDS_ATTENTION,
    SNI_STATUS_PASSIVE,
)


def _finding(check: str, finding_id: str, severity: str = "warning") -> HealthFinding:
    return HealthFinding(check=check, finding_id=finding_id,
                         severity=severity, summary=f"{check} problem",
                         details="more", action="do something")


def _spec(name="space", enabled=True, interval_min=10, run=None) -> SimpleNamespace:
    spec = SimpleNamespace(name=name, enabled=enabled, interval_min=interval_min)
    if run is not None:
        spec.run = run
    return spec


class _Daemon:
    def __init__(self, watcher: bool) -> None:
        self.watcher = watcher

    def NameHasOwner(self, name, dbus_interface=None):
        return bool(self.watcher)


class _FakeBus:
    def __init__(self, *, watcher=True, instance_free=True) -> None:
        self.watcher = watcher
        self.instance_free = instance_free
        self.requested_names = []
        self.exported = []

    def get_object(self, name, path):
        return _Daemon(self.watcher)

    def request_name(self, name, flags):
        self.requested_names.append(name)
        return 1 if self.instance_free else 3  # PRIMARY_OWNER / EXISTS


class _RecordingLoop:
    def __init__(self) -> None:
        self.ran = False
        self.quit_called = False

    def run(self) -> None:
        self.ran = True

    def quit(self) -> None:
        self.quit_called = True


class _TimerRecorder:
    def __init__(self) -> None:
        self.calls = []
        self._next = 1

    def __call__(self, callback, interval):
        self.calls.append((self._next, callback, interval))
        handle = self._next
        self._next += 1
        return handle


class _NoopClient:
    def btrfs_mounts(self):
        return []

    def get_device_stats(self):
        return []

    def list_snapshots(self):
        return {"snapshots": []}

    def scrub_status(self, mount):
        return {}


class _IconRecorder:
    def __init__(self) -> None:
        self.status = None
        self.icon_name = None
        self.tooltip = None

    def set_status(self, status):
        self.status = status

    def set_icon(self, icon_name):
        self.icon_name = icon_name

    def set_tooltip(self, title, body=""):
        self.tooltip = (title, body)

    def export(self, bus, watcher=True):
        bus.exported.append(self)


class _NotifierRecorder:
    def __init__(self) -> None:
        self.calls = []
        self.action_callback = None
        self.closed = []

    def notify(self, summary, body, *, urgency="normal", actions=None):
        self.calls.append({"summary": summary, "body": body,
                           "urgency": urgency, "actions": actions})
        return len(self.calls)

    def close(self, nid):
        self.closed.append(nid)

    def on_action(self, callback):
        self.action_callback = callback


def _store(**overrides):
    store = ConfigStore.__new__(ConfigStore)
    tray = MagicMock()
    tray.enabled = True
    tray.autostart = True
    tray.hide_when_ok = True
    tray.quiet_hours = ""
    tray.space_enabled = True
    tray.device_enabled = True
    tray.retention_enabled = True
    tray.scrub_enabled = True
    tray.space_warn_pct = 85
    tray.space_critical_pct = 95
    tray.space_interval_min = 10
    tray.device_interval_min = 60
    tray.retention_interval_min = 1440
    tray.scrub_interval_min = 60
    for key, value in overrides.items():
        setattr(tray, key, value)
    store._tray = tray
    store._policies = {}
    store._applied_profile = ""
    return store


def _app(store=None, **kwargs):
    defaults = dict(
        bus=_FakeBus(),
        client_factory=_NoopClient,
        icon=_IconRecorder(),
        notifier=_NotifierRecorder(),
        menu_model=MenuModel(on_action=lambda action: None),
        loop=_RecordingLoop(),
        timeout=_TimerRecorder(),
        timeout_ms=_TimerRecorder(),
        source_remove=MagicMock(),
        open_gui=lambda: None,
        export_icon=False,
    )
    defaults.update(kwargs)
    return TrayApp(store or _store(), **defaults)


class TestRunShortCircuits:
    def test_disabled_returns_zero(self):
        store = _store(enabled=False)
        assert _app(store).run() == 0

    def test_no_watcher_returns_zero(self):
        store = _store()
        app = _app(store, bus=_FakeBus(watcher=False))
        assert app.run() == 0

    def test_instance_taken_returns_zero(self):
        store = _store()
        app = _app(store, bus=_FakeBus(instance_free=False))
        assert app.run() == 0
        assert app._bus.requested_names == [INSTANCE_NAME]

    def test_success_path_runs_loop(self):
        store = _store()
        app = _app(store)
        assert app.run() == 0
        assert app._loop.ran
        assert app._bus.requested_names == [INSTANCE_NAME]


class TestScheduling:
    def test_immediate_enqueue_and_interval_timers(self):
        app = _app()
        app.run()
        specs = app._specs
        assert len(specs) == 4
        # every enabled check enqueued immediately + a drain timer
        timeout_calls = [c for c in app._timeout.calls
                         if getattr(c[1], "__name__", None) != "_drain"]
        assert len(timeout_calls) == 4
        intervals = {c[2] for c in timeout_calls}
        assert intervals == {10 * 60, 60 * 60, 1440 * 60}
        # drain scheduled on the millisecond timer
        assert any(getattr(c[1], "__name__", None) == "_drain"
                   and c[2] == DRAIN_INTERVAL_MS
                   for c in app._timeout_ms.calls)

    def test_disabled_check_not_enqueued(self):
        store = _store(space_enabled=False)
        app = _app(store)
        app.run()
        specs = {s.name: s for s in app._specs}
        assert specs["space"].enabled is False
        timeout_calls = [c for c in app._timeout.calls
                         if getattr(c[1], "__name__", None) != "_drain"]
        assert len(timeout_calls) == 3

    def test_enqueue_generation_counters(self):
        app = _app()
        spec = _spec()
        app._enqueue(spec)
        app._enqueue(spec)
        assert app._gens["space"] == 2
        assert app._queue.qsize() == 2

    def test_drain_discards_stale_generations(self):
        app = _app()
        spec = _spec()
        app._enqueue(spec)  # gen 1
        app._queue.put(("result", "space", 1, [_finding("space", "space:/")]))
        app._enqueue(spec)  # gen 2 supersedes gen 1
        app._drain()
        # gen 1 result dropped; gen 2 has no result yet
        assert app._results == {}

    def test_drain_applies_current_generation(self):
        app = _app()
        app._enqueue(spec := _spec())
        app._queue.put(("result", "space", 1, [_finding("space", "space:/")]))
        app._drain()
        assert "space" in app._results


class TestHandleResults:
    def test_no_findings_passive_and_clean(self):
        app = _app()
        app._handle_results("space", [])
        assert app._icon.status == SNI_STATUS_PASSIVE
        assert app._notifier.calls == []
        assert app._results == {}

    def test_warning_active_and_notified(self):
        app = _app()
        app._handle_results("space", [_finding("space", "space:/", "warning")])
        assert app._icon.status == SNI_STATUS_ACTIVE
        assert len(app._notifier.calls) == 1
        call = app._notifier.calls[0]
        assert call["urgency"] == "normal"
        assert call["actions"][0] == "open"

    def test_critical_needs_attention(self):
        app = _app()
        app._handle_results("space", [_finding("space", "space:/", "critical")])
        assert app._icon.status == SNI_STATUS_NEEDS_ATTENTION
        assert app._notifier.calls[0]["urgency"] == "critical"

    def test_merge_across_checks(self):
        app = _app()
        app._handle_results("space", [_finding("space", "space:/")])
        app._handle_results(
            "devices", [_finding("devices", "devices:/dev/sda", "critical")])
        assert app._icon.status == SNI_STATUS_NEEDS_ATTENTION

    def test_cleared_check_removes_its_findings(self):
        app = _app()
        app._handle_results("space", [_finding("space", "space:/")])
        app._handle_results("space", [])
        assert app._icon.status == SNI_STATUS_PASSIVE

    def test_quiet_hours_suppresses_notification(self, monkeypatch):
        monkeypatch.setattr("manaradix.tray.app.in_quiet_hours",
                            lambda spec, now=None: True)
        app = _app()
        app._handle_results("space", [_finding("space", "space:/")])
        assert app._notifier.calls == []

    def test_hide_when_ok_off_keeps_notifying_but_clearing_is_silent(self):
        store = _store(hide_when_ok=False)
        app = _app(store)
        app._handle_results("space", [_finding("space", "space:/")])
        app._handle_results("space", [])
        # icon goes passive; clearing is not a notification
        assert app._icon.status == SNI_STATUS_PASSIVE
        assert len(app._notifier.calls) == 1

    def test_tooltip_lists_top_findings(self):
        app = _app()
        app._handle_results(
            "space",
            [_finding("space", "space:/", "critical"),
             _finding("scrub", "scrub:stale", "warning")])
        title, body = app._icon.tooltip
        assert "2" in title
        assert "space problem" in body


class TestActions:
    def test_menu_open(self):
        opened = []
        app = _app(open_gui=lambda: opened.append(1))
        app._on_menu_action("open")
        assert opened == [1]

    def test_menu_check_enqueues_all(self):
        app = _app()
        app._specs = [_spec("space", enabled=True),
                      _spec("devices", enabled=False)]
        app._on_menu_action("check")
        assert app._queue.qsize() == 1  # only the enabled one

    def test_menu_quit_shuts_down(self):
        app = _app()
        app._on_menu_action("quit")
        assert app._loop.quit_called is True

    def test_notification_action_opens_and_closes(self):
        app = _app()
        opened = []
        app._open_gui = lambda: opened.append(1)
        app._on_notification_action("open", 7)
        assert opened == [1]
        assert app._notifier.closed == [7]

    def test_notification_unknown_action_ignored(self):
        app = _app()
        app._on_notification_action("whatever", 7)
        assert app._notifier.closed == []


class TestWorker:
    def test_worker_runs_check_with_fresh_client(self):
        app = _app()
        seen = []
        run = MagicMock(side_effect=lambda sources: seen.append(sources) or
                        [_finding("space", "space:/")])
        app._queue.put(("space", 1, _spec(run=run)))
        app._queue.put(None)
        app._worker_main()
        assert len(seen) == 1
        assert seen[0]._client is not None

    def test_worker_failure_degrades(self):
        app = _app()
        run = MagicMock(side_effect=RuntimeError("boom"))
        app._queue.put(("space", 1, _spec(run=run)))
        app._queue.put(None)
        app._worker_main()
        assert app._queue.get() == ("result", "space", 1, [])

    def test_worker_stop_breaks_loop(self):
        app = _app()
        app._queue.put(None)
        app._worker_main()  # returns without hanging


class TestShutdown:
    def test_shutdown_quits_loop(self):
        app = _app()
        app._shutdown()
        assert app._loop.quit_called is True

    def test_cleanup_stops_worker(self):
        app = _app()
        app.run()
        app._cleanup()
        assert not app._worker.is_alive()
