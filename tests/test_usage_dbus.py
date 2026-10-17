"""Unit tests for the GetUsage D-Bus method."""

from unittest.mock import MagicMock, patch
import pytest

from manaradix.dbus.service import ManaradixHandler
from manaradix.dbus.client import ManaradixClient


class TestGetUsageHandler:
    """Test the handle_GetUsage method in ManaradixHandler."""

    def test_handle_get_usage_returns_dict(self, tmp_path):
        """GetUsage returns a dict with status, last_update, aggregates, snapshots."""
        handler = ManaradixHandler(
            MagicMock(),
            auth_check=lambda pid, action: None,
            pid_resolver=lambda connection, sender: 4242,
        )
        result = handler.dispatch("GetUsage", {}, ":1.7")

        assert isinstance(result, dict)
        assert "status" in result
        assert "last_update" in result
        assert "aggregates" in result
        assert "snapshots" in result

    def test_handle_get_usage_status_structure(self, tmp_path):
        """GetUsage status has the expected structure."""
        handler = ManaradixHandler(
            MagicMock(),
            auth_check=lambda pid, action: None,
            pid_resolver=lambda connection, sender: 4242,
        )
        result = handler.dispatch("GetUsage", {}, ":1.7")

        status = result["status"]
        assert "state" in status
        assert status["state"] in ["idle", "running", "ok", "failed"]

    def test_handle_get_usage_empty_when_no_data(self, tmp_path):
        """GetUsage returns empty lists when no data collected yet."""
        handler = ManaradixHandler(
            MagicMock(),
            auth_check=lambda pid, action: None,
            pid_resolver=lambda connection, sender: 4242,
        )
        result = handler.dispatch("GetUsage", {}, ":1.7")

        assert result["aggregates"] == []
        assert result["snapshots"] == []

    def test_handle_get_usage_with_data(self, tmp_path, monkeypatch):
        """GetUsage returns data when collection has run."""
        from manaradix.core import usage_store

        # Create test data
        store = usage_store.UsageStore(str(tmp_path / "test.sqlite"))
        store.start_collect()
        store.finish_collect(
            aggregates=[{
                "mount": "/",
                "snapshot_dir": "/.snapshots",
                "total_bytes": 1024000,
                "exclusive_bytes": 512000,
                "device_size_bytes": 1073741824,
            }],
            snapshots=[{
                "mount": "/",
                "snapshot_id": "2024-01-01_00-00-00",
                "snapshot_path": "/.snapshots/2024-01-01_00-00-00/root",
                "total_bytes": 512000,
                "exclusive_bytes": 256000,
            }]
        )

        # Patch DEFAULT_DB_PATH to use our test database
        monkeypatch.setattr(usage_store, "DEFAULT_DB_PATH", str(tmp_path / "test.sqlite"))

        handler = ManaradixHandler(
            MagicMock(),
            auth_check=lambda pid, action: None,
            pid_resolver=lambda connection, sender: 4242,
        )
        result = handler.dispatch("GetUsage", {}, ":1.7")

        assert len(result["aggregates"]) == 1
        assert result["aggregates"][0]["mount"] == "/"
        assert result["aggregates"][0]["total_bytes"] == 1024000

        assert len(result["snapshots"]) == 1
        assert result["snapshots"][0]["snapshot_id"] == "2024-01-01_00-00-00"


class TestGetUsageClient:
    """Test the get_usage method in ManaradixClient."""

    def test_get_usage_returns_dict(self):
        """Client get_usage returns the dict from D-Bus."""
        client = ManaradixClient(bus=MagicMock())
        mock_proxy = MagicMock()
        mock_proxy.GetUsage.return_value = {
            "status": {"state": "ok", "last_started": None, "last_finished": "2024-01-01T00:00:00", "last_error": None},
            "last_update": "2024-01-01T00:00:00",
            "aggregates": [],
            "snapshots": [],
        }
        client._proxy = MagicMock(return_value=mock_proxy)

        result = client.get_usage()

        assert isinstance(result, dict)
        assert "status" in result
        assert result["status"]["state"] == "ok"
        assert "last_update" in result
        assert "aggregates" in result
        assert "snapshots" in result

    def test_get_usage_calls_correct_method(self):
        """Client get_usage calls the GetUsage D-Bus method."""
        client = ManaradixClient(bus=MagicMock())
        mock_proxy = MagicMock()
        mock_proxy.GetUsage.return_value = {
            "status": {"state": "idle"},
            "last_update": None,
            "aggregates": [],
            "snapshots": [],
        }
        client._proxy = MagicMock(return_value=mock_proxy)

        client.get_usage()

        # Verify GetUsage was called on the proxy
        mock_proxy.GetUsage.assert_called_once()


class TestGetUsageWire:
    """Test the GetUsage D-Bus wire method."""

    def test_wire_method_exists(self):
        """GetUsage is registered as a D-Bus method."""
        from manaradix.dbus.wire import ManaradixService
        assert hasattr(ManaradixService, "GetUsage")

    def test_wire_method_has_correct_signature(self):
        """GetUsage has the correct D-Bus signature."""
        from manaradix.dbus.wire import ManaradixService
        method = getattr(ManaradixService, "GetUsage")

        # Check that it's a dbus method
        assert hasattr(method, "_dbus_in_signature")
        assert hasattr(method, "_dbus_out_signature")


class TestRefreshUsage:
    """handle_RefreshUsage: non-blocking systemd start, tier and outcomes."""

    @staticmethod
    def _handler():
        return ManaradixHandler(
            MagicMock(),
            auth_check=lambda pid, action: None,
            pid_resolver=lambda connection, sender: 4242,
        )

    def test_requires_manage_filesystem_tier(self):
        from manaradix.dbus.constants import ACTION_MANAGE_FILESYSTEM, METHOD_ACTIONS
        assert METHOD_ACTIONS["RefreshUsage"] == ACTION_MANAGE_FILESYSTEM

    def test_get_usage_is_read_tier(self):
        from manaradix.dbus.constants import ACTION_READ, METHOD_ACTIONS
        assert METHOD_ACTIONS["GetUsage"] == ACTION_READ

    def test_starts_unit_without_blocking(self, tmp_path, monkeypatch):
        from manaradix.core import usage_store
        monkeypatch.setattr(usage_store, "DEFAULT_DB_PATH", str(tmp_path / "none.sqlite"))
        with patch("subprocess.run") as run:
            result = self._handler().dispatch("RefreshUsage", {}, ":1.7")
        assert result == {"state": "started", "error": ""}
        assert run.call_args[0][0] == [
            "systemctl", "start", "--no-block", "manaradix-usage.service"
        ]

    def test_already_running_skips_start(self, tmp_path, monkeypatch):
        from manaradix.core import usage_store
        db = str(tmp_path / "u.sqlite")
        usage_store.UsageStore(db).start_collect()
        monkeypatch.setattr(usage_store, "DEFAULT_DB_PATH", db)
        with patch("subprocess.run") as run:
            result = self._handler().dispatch("RefreshUsage", {}, ":1.7")
        assert result["state"] == "already_running"
        run.assert_not_called()

    def test_systemctl_failure_reported(self, tmp_path, monkeypatch):
        import subprocess
        from manaradix.core import usage_store
        monkeypatch.setattr(usage_store, "DEFAULT_DB_PATH", str(tmp_path / "none.sqlite"))
        err = subprocess.CalledProcessError(1, "systemctl", stderr="Unit not found\n")
        with patch("subprocess.run", side_effect=err):
            result = self._handler().dispatch("RefreshUsage", {}, ":1.7")
        assert result == {"state": "failed", "error": "Unit not found"}

    def test_missing_systemctl_reported(self, tmp_path, monkeypatch):
        from manaradix.core import usage_store
        monkeypatch.setattr(usage_store, "DEFAULT_DB_PATH", str(tmp_path / "none.sqlite"))
        with patch("subprocess.run", side_effect=FileNotFoundError("systemctl")):
            result = self._handler().dispatch("RefreshUsage", {}, ":1.7")
        assert result["state"] == "failed"


def test_client_refresh_usage_calls_method():
    client = ManaradixClient(bus=MagicMock())
    proxy = MagicMock()
    proxy.RefreshUsage.return_value = {"state": "started", "error": ""}
    client._proxy = MagicMock(return_value=proxy)
    assert client.refresh_usage()["state"] == "started"
    proxy.RefreshUsage.assert_called_once()


def test_wire_refresh_usage_registered():
    from manaradix.dbus.wire import ManaradixService
    assert hasattr(ManaradixService, "RefreshUsage")
