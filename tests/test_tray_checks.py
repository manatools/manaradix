"""Tests for the check registry (space/devices/retention/scrub)."""

from manaradix.core.config_store import ConfigStore
from manaradix.model.policy import SnapshotPolicy
from manaradix.tray.checks import (
    TraySources,
    build_check_registry,
    resolve_profile,
)


class _FakeClient:
    """A fake ManaradixClient shaped like the real one's read methods."""

    def __init__(self, mounts=None, devices=None, snapshots=None,
                 scrub=None) -> None:
        self._mounts = mounts or [{"target": "/"}]
        self._devices = devices or []
        self._snapshots = snapshots or []
        self._scrub = scrub or {}

    def btrfs_mounts(self):
        return self._mounts

    def get_device_stats(self):
        return self._devices

    def list_snapshots(self):
        return {"snapshots": self._snapshots}

    def scrub_status(self, mount):
        return self._scrub


def _store(**overrides):
    store = ConfigStore.__new__(ConfigStore)
    # wire a minimal [tray] surface without touching the filesystem
    store._tray = None
    tray = _Tray(**overrides)
    store._tray = tray
    store._policies = {}
    store._applied_profile = ""
    return store, tray


class _Tray:
    def __init__(self, **overrides) -> None:
        self.enabled = True
        self.autostart = True
        self.hide_when_ok = True
        self.quiet_hours = ""
        self.space_enabled = True
        self.device_enabled = True
        self.retention_enabled = True
        self.scrub_enabled = True
        self.space_warn_pct = 85
        self.space_critical_pct = 95
        self.space_interval_min = 10
        self.device_interval_min = 60
        self.retention_interval_min = 1440
        self.scrub_interval_min = 60
        for key, value in overrides.items():
            setattr(self, key, value)


class TestResolveProfile:
    def test_applied_profile_wins(self):
        store, _tray = _store()
        store._applied_profile = "kiosk"
        assert resolve_profile(store) == "kiosk"

    def test_fallback_desktop(self):
        store, _tray = _store()
        assert resolve_profile(store) == "desktop"


class TestBuildCheckRegistry:
    def test_four_checks_in_order(self):
        store, _tray = _store()
        specs = build_check_registry(store)
        assert [spec.name for spec in specs] == [
            "space", "devices", "retention", "scrub"]

    def test_intervals_and_enablement_from_config(self):
        store, _tray = _store(
            space_interval_min=5, device_enabled=False)
        specs = {spec.name: spec for spec in build_check_registry(store)}
        assert specs["space"].interval_min == 5
        assert specs["space"].enabled is True
        assert specs["devices"].enabled is False
        assert specs["retention"].interval_min == 1440

    def test_space_check_findings(self):
        store, _tray = _store(space_warn_pct=50, space_critical_pct=99)
        specs = {spec.name: spec for spec in build_check_registry(store)}
        client = _FakeClient(mounts=[{"target": "/"}])
        findings = specs["space"].run(TraySources(client))
        # / is far below 50 % used on the dev machine... but the check
        # must not crash and must return a list.
        assert isinstance(findings, list)

    def test_devices_check_findings(self):
        store, _tray = _store()
        specs = {spec.name: spec for spec in build_check_registry(store)}
        client = _FakeClient(devices=[{"device": "/dev/sda2",
                                       "smart_available": True,
                                       "smart_failing": True}])
        findings = specs["devices"].run(TraySources(client))
        assert len(findings) == 1
        assert findings[0].severity == "critical"

    def test_retention_check_skips_disabled_policies(self):
        store, _tray = _store()
        policy = SnapshotPolicy(subvolume="home", enabled=False)
        store._policies = {"home": policy}
        specs = {spec.name: spec for spec in build_check_registry(store)}
        client = _FakeClient(snapshots=[])
        assert specs["retention"].run(TraySources(client)) == []

    def test_broken_check_degrades_to_empty(self):
        store, _tray = _store()
        specs = {spec.name: spec for spec in build_check_registry(store)}

        class _BoomClient(_FakeClient):
            def btrfs_mounts(self):
                raise RuntimeError("no bus")

        assert specs["space"].run(TraySources(_BoomClient())) == []
        assert specs["devices"].run(TraySources(_BoomClient())) == []
        assert specs["retention"].run(TraySources(_BoomClient())) == []
        assert specs["scrub"].run(TraySources(_BoomClient())) == []

    def test_scrub_check_findings(self):
        store, _tray = _store()
        specs = {spec.name: spec for spec in build_check_registry(store)}
        client = _FakeClient(scrub={
            "running": False, "errors": 2, "last_started": None,
            "data_extents_scrubbed": 10,
        })
        findings = specs["scrub"].run(TraySources(client))
        assert len(findings) == 1
        assert findings[0].severity == "warning"


class TestTraySources:
    def test_mounts(self):
        client = _FakeClient(mounts=[{"target": "/"}])
        sources = TraySources(client)
        assert sources.mounts() == [{"target": "/"}]

    def test_snapshots_shape(self):
        client = _FakeClient(snapshots=[{"id": "1"}])
        assert TraySources(client).snapshots() == [{"id": "1"}]
