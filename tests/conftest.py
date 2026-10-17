"""Shared test fixtures."""

import pytest

from manaradix.core import usage_store


@pytest.fixture(autouse=True)
def _isolate_usage_db(tmp_path, monkeypatch):
    """Never let a test read or write the host's real usage database.

    ``/var/lib/manaradix/usage.sqlite`` exists on a machine where the
    collector has run, which made "no data yet" tests depend on the host.
    Tests that need data patch ``DEFAULT_DB_PATH`` themselves; that
    overrides this default.
    """
    monkeypatch.setattr(
        usage_store, "DEFAULT_DB_PATH", str(tmp_path / "isolated-usage.sqlite")
    )
