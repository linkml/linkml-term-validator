"""Pytest configuration and shared fixtures."""

from pathlib import Path

import pytest
import requests_cache

from linkml_term_validator.utils import oak_utils


@pytest.fixture(scope="session", autouse=True)
def patch_requests_cache(pytestconfig):
    """Cache network requests - only do unique requests once per session.

    This significantly speeds up integration tests by caching HTTP requests
    to ontology services (OLS, BioPortal, etc.).
    """
    cache_file = Path(__file__).parent / "output" / "requests-cache.sqlite"
    cache_file.parent.mkdir(exist_ok=True)
    requests_cache.install_cache(str(cache_file), backend="sqlite")
    yield
    requests_cache.uninstall_cache()


@pytest.fixture(autouse=True)
def instant_retry_backoff(monkeypatch):
    """Make the service-retry backoff free for the whole suite.

    A lookup that reports the ontology service unavailable is retried with an
    exponential backoff (see ``RetryPolicy``), so the tests that simulate an
    outage would otherwise spend the real 1s + 2s waiting it out. The retries
    themselves still happen - only the sleeping is skipped - and a test that
    cares about the delays sets its own policy.
    """
    monkeypatch.setattr(oak_utils, "DEFAULT_SERVICE_RETRY_BACKOFF", 0.0)


@pytest.fixture
def test_schema_path():
    """Path to test schema with example ontology terms."""
    return Path(__file__).parent / "data" / "test_schema.yaml"


@pytest.fixture
def output_dir(tmp_path):
    """Temporary directory for test outputs."""
    return tmp_path / "output"


@pytest.fixture
def cache_dir(tmp_path):
    """Temporary cache directory for tests."""
    return tmp_path / "cache"
