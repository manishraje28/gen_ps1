"""Shared test setup: every test gets a fresh, temporary workspace database (never data/warrant.db)."""
import pytest


@pytest.fixture(autouse=True)
def _temp_store(tmp_path):
    from web import store
    store.connect(tmp_path / "warrant-test.db")
    yield
