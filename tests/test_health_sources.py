"""Tests for the injectable health data sources."""

from __future__ import annotations

import os

from manaradix.core import health_sources


class TestStatvfsUsages:
    def test_real_directory(self, tmp_path) -> None:
        usages = health_sources.statvfs_usages([str(tmp_path)])
        assert len(usages) == 1
        usage = usages[0]
        assert usage["mount"] == str(tmp_path)
        assert usage["total_bytes"] > 0
        assert usage["free_bytes"] > 0
        assert 0.0 <= usage["percent_used"] <= 100.0

    def test_missing_mount_skipped(self) -> None:
        assert health_sources.statvfs_usages(
            ["/nonexistent-manaradix-test-mount"]
        ) == []

    def test_statvfs_error_skipped(self, tmp_path, monkeypatch) -> None:
        def boom(_path: str) -> None:
            raise OSError("nope")

        monkeypatch.setattr(os, "statvfs", boom)
        assert health_sources.statvfs_usages([str(tmp_path)]) == []


class TestClientHelpers:
    def test_device_stats_via_client(self) -> None:
        class FakeClient:
            def get_device_stats(self) -> list:
                return [{"device": "/dev/sda"}, {"device": "/dev/sdb"}]

        assert health_sources.device_stats_via_client(FakeClient()) == [
            {"device": "/dev/sda"}, {"device": "/dev/sdb"}
        ]

    def test_snapshots_via_client(self) -> None:
        class FakeClient:
            def list_snapshots(self) -> list:
                return [{"id": "1"}, {"id": "2"}]

        assert [s["id"] for s in health_sources.snapshots_via_client(FakeClient())] == ["1", "2"]

    def test_scrub_status_via_client(self) -> None:
        class FakeClient:
            def scrub_status(self, mount: str) -> dict:
                assert mount == "/"
                return {"errors": 0, "running": False, "last_started": None}

        assert health_sources.scrub_status_via_client(FakeClient()) == {
            "errors": 0, "running": False, "last_started": None
        }

    def test_device_stats_empty(self) -> None:
        class FakeClient:
            def get_device_stats(self) -> list:
                return []

        assert health_sources.device_stats_via_client(FakeClient()) == []
