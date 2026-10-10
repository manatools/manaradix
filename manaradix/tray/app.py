"""The tray applet: SNI icon + health checks + notifications (wiring).

The applet runs on the session bus as the logged-in user.  Following
dnfdragora's updater patterns, checks run in a worker thread and their
results are drained on the main GLib loop via a small queue + a 100 ms
timer; generation counters discard stale results.  The applet:

1. probes for ``org.kde.StatusNotifierWatcher`` and exits 0 silently
   when the desktop has no tray host (same hide-until-problem spirit
   as dnfdragora's ``hide_menu``);
2. claims a single-instance name (``org.mageia.manaradix.Tray``) so a
   second launch exits immediately;
3. exports the StatusNotifierItem + dbusmenu objects, registers with
   the watcher;
4. runs the enabled health checks on their configured intervals,
   updates the icon state (passive / active / needs-attention) and
   notifies on transitions (see :mod:`manaradix.tray.scheduler`);
5. shows its menu when the icon is clicked (``ItemIsMenu``) and opens
   the GUI from the menu's *Open manaradix* item or a notification
   action;
6. shuts down cleanly on SIGINT/SIGTERM.
"""

from __future__ import annotations

import logging
import os
import queue
import signal
import subprocess
import threading
from typing import Callable, Optional

from manaradix import __version__
from manaradix.core.config_store import ConfigStore
from manaradix.core.health import SEVERITY_ORDER, in_quiet_hours
from manaradix.dbus.client import ManaradixClient  # noqa: F401  (type hint)
from manaradix.i18n import _
from manaradix.tray.checks import CheckSpec, TraySources, build_check_registry
from manaradix.tray.dbus_menu import MenuModel
from manaradix.tray.notifier import Notifier
from manaradix.tray.scheduler import FindingTracker
from manaradix.tray.status_icon import StatusIcon, icon_state

logger = logging.getLogger(__name__)

#: Single-instance bus name claimed by the applet (``tray status``
#: probes it via NameHasOwner).
INSTANCE_NAME = "org.mageia.manaradix.Tray"

#: How often the main loop drains the worker's result queue (ms).
DRAIN_INTERVAL_MS = 100

_SEVERITY_RANK = {level: i for i, level in enumerate(SEVERITY_ORDER)}


def _default_open_gui() -> None:
    """Launch the manaradix GUI in its own process group."""
    try:
        subprocess.Popen(["manaradix"], start_new_session=True)
    except OSError as exc:
        logger.warning("Could not launch the GUI: %s", exc)


def tray_running() -> bool:
    """True when a tray applet owns the single-instance name.

    Used by ``manaradix tray status``; probing is additive — when the
    session bus or python3-dbus is unavailable the answer is False.
    """
    try:
        import dbus

        bus = dbus.SessionBus()
        daemon = bus.get_object("org.freedesktop.DBus",
                                "/org/freedesktop/DBus")
        return bool(daemon.NameHasOwner(
            INSTANCE_NAME, dbus_interface="org.freedesktop.DBus"))
    except Exception:
        return False


class TrayApp:
    """Wires the health checks to the SNI icon and notifications.

    Every dependency is injectable so tests drive the applet without a
    bus, a tray host, or a GLib mainloop:

    Args:
        store: The config store (``[tray]`` section drives everything).
        bus: A pre-built session bus; built from :mod:`dbus` when None.
        client_factory: Builds the D-Bus client used by the worker
            thread (one per batch; default ``ManaradixClient``).
        icon: A :class:`StatusIcon` (default: constructed in ``run``).
        notifier: A :class:`Notifier`-like object (``notify``,
            ``close``, ``on_action``).
        menu_model: A :class:`MenuModel` (default: built in ``run``).
        tracker: A :class:`FindingTracker`.
        loop: An object with ``run()`` / ``quit()`` (default: a GLib
            mainloop).
        timeout: Schedules a callback with GLib semantics
            ``timeout(interval_seconds, callback)``; returns a handle.
            Default ``GLib.timeout_add_seconds``.
        timeout_ms: Schedules a callback with GLib semantics
            ``timeout(interval_ms, callback)`` (used for the 100 ms
            queue drain); default ``GLib.timeout_add``.
        source_remove: Cancels a scheduled callback by handle.
        open_gui: Opens the manaradix GUI.
        export_icon: Export the SNI/menu objects on the bus (disable in
            tests).
    """

    def __init__(
        self,
        store: ConfigStore,
        *,
        bus=None,
        client_factory: Optional[Callable[[], object]] = None,
        icon: Optional[StatusIcon] = None,
        notifier=None,
        menu_model: Optional[MenuModel] = None,
        tracker: Optional[FindingTracker] = None,
        loop=None,
        timeout=None,
        timeout_ms=None,
        source_remove=None,
        open_gui: Callable[[], None] = _default_open_gui,
        export_icon: bool = True,
    ) -> None:
        self._store = store
        self._tray = store.tray
        self._bus = bus
        self._client_factory = client_factory or ManaradixClient
        self._icon = icon
        self._notifier = notifier
        self._menu_model = menu_model
        self._tracker = tracker or FindingTracker()
        self._loop = loop
        self._timeout = timeout
        self._timeout_ms = timeout_ms
        self._source_remove = source_remove
        self._open_gui = open_gui
        self._export_icon = export_icon

        self._queue: queue.Queue = queue.Queue()          # jobs → worker
        self._results_queue: queue.Queue = queue.Queue()  # results → drain
        self._gens: dict[str, int] = {}
        self._results: dict[str, list] = {}
        self._timer_ids: list = []
        self._worker: Optional[threading.Thread] = None
        self._specs: list[CheckSpec] = []
        self._checks: dict[str, CheckSpec] = {}

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self) -> int:
        """Run the applet until shutdown; returns the exit code.

        Exits 0 without doing anything when the applet is disabled in
        the config, when the desktop has no StatusNotifierWatcher, or
        when another instance already owns the single-instance name.
        """
        if not self._tray.enabled:
            logger.info("Tray applet disabled in config; exiting")
            return 0
        if not self._setup_bus():
            return 0
        if not self._probe_watcher():
            logger.info("No StatusNotifierWatcher; the desktop has no "
                        "system tray; exiting")
            return 0
        if not self._claim_instance():
            return 0

        self._setup_components()
        self._specs = build_check_registry(self._store)
        self._checks = {spec.name: spec for spec in self._specs}

        self._worker = threading.Thread(
            target=self._worker_main, name="manaradix-tray-checks", daemon=True
        )
        self._worker.start()

        for spec in self._specs:
            self._enqueue(spec)  # immediate first run
        for spec in self._specs:
            if spec.enabled:
                self._timer_ids.append(
                    self._timeout(spec.interval_min * 60,
                                  self._interval_cb(spec))
                )
        self._timer_ids.append(self._timeout_ms(DRAIN_INTERVAL_MS, self._drain))

        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, self._shutdown)
        if hasattr(signal, "SIGINT"):
            signal.signal(signal.SIGINT, self._shutdown)

        logger.info("Tray applet running")
        try:
            self._loop.run()
        finally:
            self._cleanup()
        return 0

    # ------------------------------------------------------------------
    # Startup steps (each short-circuits run() with exit 0)
    # ------------------------------------------------------------------

    def _setup_bus(self) -> bool:
        if self._bus is not None:
            self._loop = self._loop or _FakeLoop()
            if self._timeout is None:
                self._timeout = lambda interval, cb: _NoopTimer(cb)
            if self._timeout_ms is None:
                self._timeout_ms = self._timeout
            self._source_remove = self._source_remove or _noop
            return True
        try:
            import dbus
            import dbus.mainloop.glib
            from dbus.mainloop.glib import DBusGMainLoop

            dbus.mainloop.glib.threads_init()
            DBusGMainLoop(set_as_default=True)
            self._bus = dbus.SessionBus()
            if self._loop is None:
                from gi.repository import GLib

                self._loop = GLib.MainLoop()
            if self._timeout is None:
                from gi.repository import GLib

                self._timeout = GLib.timeout_add_seconds
            if self._timeout_ms is None:
                from gi.repository import GLib

                self._timeout_ms = GLib.timeout_add
            if self._source_remove is None:
                from gi.repository import GLib

                self._source_remove = GLib.source_remove
            return True
        except Exception as exc:  # pragma: no cover - environment dependent
            logger.error("Could not set up the session bus: %s", exc)
            return False

    def _name_has_owner(self, name: str) -> bool:
        import dbus

        daemon = self._bus.get_object("org.freedesktop.DBus",
                                      "/org/freedesktop/DBus")
        return bool(daemon.NameHasOwner(name,
                                        dbus_interface="org.freedesktop.DBus"))

    def _probe_watcher(self) -> bool:
        try:
            return self._name_has_owner("org.kde.StatusNotifierWatcher")
        except Exception as exc:
            logger.warning("Watcher probe failed: %s", exc)
            return False

    def _claim_instance(self) -> bool:
        try:
            import dbus.bus

            reply = self._bus.request_name(
                INSTANCE_NAME, dbus.bus.NAME_FLAG_DO_NOT_QUEUE
            )
            return reply == dbus.bus.REQUEST_NAME_REPLY_PRIMARY_OWNER
        except Exception as exc:
            logger.warning("Could not claim the single-instance name: %s", exc)
            return False

    def _setup_components(self) -> None:
        if self._icon is None:
            # ItemIsMenu: a click on the icon pops the dbusmenu (Open
            # manaradix / Check now / About / Quit) instead of launching
            # the GUI straight away — the GUI is one menu item, not the
            # only thing the icon can do.  No Activate/SecondaryActivate
            # callbacks are wired for the same reason; a host that
            # ignores ItemIsMenu simply does nothing on a plain click
            # and still shows the menu on right-click.
            self._icon = StatusIcon(
                service_name=f"org.kde.StatusNotifierItem-{os.getpid()}-1",
                item_is_menu=True,
            )
        if self._menu_model is None:
            self._menu_model = MenuModel(self._on_menu_action)
        if self._notifier is None:
            self._notifier = Notifier(self._bus)
            self._notifier.on_action(self._on_notification_action)
        if self._export_icon:
            self._icon.export(self._bus)
            from manaradix.tray.dbus_menu import DbusMenu

            DbusMenu(self._bus, self._menu_model,
                     service_name=self._icon.service_name)

    # ------------------------------------------------------------------
    # Check scheduling: worker thread + generation counters + drain
    # ------------------------------------------------------------------

    def _enqueue(self, spec: CheckSpec) -> None:
        """Queue one check run (called from the main loop)."""
        if not spec.enabled:
            return
        gen = self._gens.get(spec.name, 0) + 1
        self._gens[spec.name] = gen
        self._queue.put((spec.name, gen, spec))

    def _interval_cb(self, spec: CheckSpec):
        def callback():
            self._enqueue(spec)
            return True  # keep the timer alive

        return callback

    def _worker_main(self) -> None:
        """Consumes check jobs; one fresh client per batch (thread-bound)."""
        while True:
            job = self._queue.get()
            if job is None:
                break
            name, gen, spec = job
            try:
                client = self._client_factory()
                findings = spec.run(TraySources(client))
                self._results_queue.put(("result", name, gen, findings))
            except Exception as exc:
                logger.warning("Check %s failed: %s", name, exc)
                self._results_queue.put(("result", name, gen, []))

    def _drain(self) -> bool:
        """Main loop: apply queued results, update icon + notifications."""
        while True:
            try:
                job = self._results_queue.get_nowait()
            except queue.Empty:
                break
            _kind, name, gen, findings = job
            if gen < self._gens.get(name, 0):
                continue  # superseded by a newer run
            self._handle_results(name, findings)
        return True  # keep the timer alive

    def _handle_results(self, name: str, findings: list) -> None:
        """Apply one check's findings: icon, tooltip, notifications."""
        if findings:
            self._results[name] = findings
        else:
            self._results.pop(name, None)
        merged = [finding for lst in self._results.values() for finding in lst]

        status, icon_name = icon_state(
            merged, hide_when_ok=self._tray.hide_when_ok)
        self._icon.set_status(status)
        self._icon.set_icon(icon_name)

        if merged:
            top = sorted(merged, key=lambda f: -_SEVERITY_RANK.get(f.severity, 0))[:3]
            self._icon.set_tooltip(
                _("Manaradix: %d issue(s)") % len(merged),
                "\n".join(f.summary for f in top),
            )
        else:
            self._icon.set_tooltip(_("Manaradix"), _("No issues found"))

        to_notify = self._tracker.update(merged)
        for finding in to_notify:
            if in_quiet_hours(self._tray.quiet_hours):
                continue
            self._notify(finding)

    def _notify(self, finding) -> None:
        body = finding.details
        if finding.action:
            body = f"{body}\n{finding.action}"
        self._notifier.notify(
            finding.summary, body,
            urgency="critical" if finding.severity == "critical" else "normal",
            actions=["open", _("Open manaradix")],
        )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _on_menu_action(self, action: str) -> None:
        if action == "open":
            self._open_gui()
        elif action == "check":
            for spec in self._specs:
                self._enqueue(spec)
        elif action == "about":
            self._show_about()
        elif action == "quit":
            self._shutdown()

    def _show_about(self) -> None:
        """Show an About notification: version, description, website."""
        try:
            self._notifier.notify(
                f"Manaradix {__version__}\n",
                _("Btrfs snapshot, subvolume, and filesystem "
                  "management utility for Mageia")
                + "\nhttps://bitbucket.org/_pmat_/manaradix",
                actions=["open", _("Open manaradix")],
            )
        except Exception as exc:
            logger.warning("Could not show the About notification: %s", exc)

    def _on_notification_action(self, action_key: str, notification_id: int) -> None:
        if action_key == "open":
            self._open_gui()
            self._notifier.close(notification_id)

    def run_checks_now(self) -> None:
        """Run every enabled check immediately (public, for tests/tools)."""
        for spec in self._specs:
            self._enqueue(spec)

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def _shutdown(self, *_args) -> None:
        logger.info("Shutting down the tray applet")
        self._loop.quit()

    def _cleanup(self) -> None:
        for timer_id in self._timer_ids:
            try:
                self._source_remove(timer_id)
            except Exception:
                pass
        if self._worker is not None:
            self._queue.put(None)
            self._worker.join(timeout=10)


class _FakeLoop:
    """Injected-loop fallback used when a bus is injected (tests)."""

    def run(self) -> None:  # pragma: no cover - trivial
        return None

    def quit(self) -> None:  # pragma: no cover - trivial
        return None


class _NoopTimer:
    """Timer handle for injected setups that never run a main loop."""

    def __init__(self, callback) -> None:
        self.callback = callback


def _noop(_timer_id) -> None:
    return None
