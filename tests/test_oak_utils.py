"""Unit tests for the shared OntologyAccess service and helpers."""

import socket
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from linkml_term_validator.utils import (
    OntologyAccess,
    OntologyServiceUnavailableError,
    get_prefix,
    is_connectivity_error,
    normalize_string,
)
from linkml_term_validator.utils import oak_utils


def _dns_failure_connection_error() -> requests.exceptions.ConnectionError:
    """Build the real requests exception an OLS/EBI DNS outage produces.

    Mirrors the reported trace: a requests ConnectionError wrapping a urllib3
    MaxRetryError caused by a socket DNS resolution failure. The classifier must
    recognize this by type (isinstance), not by matching a class name.
    """
    inner = socket.gaierror(8, "nodename nor servname provided, or not known")
    exc = requests.exceptions.ConnectionError(
        "HTTPSConnectionPool(host='www.ebi.ac.uk', port=443): Max retries exceeded"
    )
    exc.__cause__ = inner
    return exc


def _http_error(status_code: int) -> requests.exceptions.HTTPError:
    """Build a requests HTTPError carrying a response with the given status."""
    response = requests.Response()
    response.status_code = status_code
    return requests.exceptions.HTTPError(f"{status_code} Error", response=response)

TEST_OAK_CONFIG = Path("tests/data/test_oak_config.yaml")


@pytest.mark.parametrize(
    "curie,expected",
    [
        ("GO:0008150", "GO"),
        ("CHEBI:12345", "CHEBI"),
        ("TEST:0000001", "TEST"),
        ("no_colon", None),
        ("", None),
    ],
)
def test_get_prefix(curie, expected):
    assert get_prefix(curie) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Hello, World!", "hello world"),
        ("T-Cell Receptor", "t cell receptor"),
        ("Multi  Spaces", "multi spaces"),
        ("  trim me  ", "trim me"),
    ],
)
def test_normalize_string(raw, expected):
    assert normalize_string(raw) == expected


def test_is_prefix_configured_reads_oak_config():
    access = OntologyAccess(cache_labels=False, oak_config_path=TEST_OAK_CONFIG)
    assert access.is_prefix_configured("TEST") is True
    assert access.is_prefix_configured("GO") is False


def test_is_prefix_configured_false_without_config():
    access = OntologyAccess(cache_labels=False)
    assert access.is_prefix_configured("GO") is False


def test_loaded_config_exposes_full_yaml():
    access = OntologyAccess(cache_labels=False, oak_config_path=TEST_OAK_CONFIG)
    assert "ontology_adapters" in access.loaded_config
    assert access.oak_config["TEST"] == "simpleobo:tests/data/test_ontology.obo"


def test_cache_round_trip(tmp_path):
    access = OntologyAccess(cache_labels=True, cache_dir=tmp_path)
    access.save_to_cache("GO", "GO:0008150", "biological process")

    assert access.load_cache("GO") == {"GO:0008150": "biological process"}
    cache_file = access.get_cache_file("GO")
    assert cache_file == tmp_path / "go" / "terms.csv"
    assert cache_file.exists()


def test_cache_entries_sorted_by_curie(tmp_path):
    access = OntologyAccess(cache_labels=True, cache_dir=tmp_path)
    access.save_to_cache("GO", "GO:0000003", "c")
    access.save_to_cache("GO", "GO:0000001", "a")
    access.save_to_cache("GO", "GO:0000002", "b")

    rows = access.load_cache_with_timestamps("GO")
    assert list(rows.keys()) == ["GO:0000001", "GO:0000002", "GO:0000003"]


def test_cache_preserves_timestamp_when_unchanged(tmp_path):
    access = OntologyAccess(cache_labels=True, cache_dir=tmp_path)
    access.save_to_cache("GO", "GO:0000001", "a")
    first = access.load_cache_with_timestamps("GO")["GO:0000001"]["retrieved_at"]

    # Re-saving the same label must not refresh the timestamp.
    access.save_to_cache("GO", "GO:0000001", "a")
    second = access.load_cache_with_timestamps("GO")["GO:0000001"]["retrieved_at"]
    assert first == second

    # Changing the label refreshes the timestamp.
    access.save_to_cache("GO", "GO:0000001", "different")
    third = access.load_cache_with_timestamps("GO")["GO:0000001"]["retrieved_at"]
    assert third != first


def test_cache_labels_false_is_noop(tmp_path):
    access = OntologyAccess(cache_labels=False, cache_dir=tmp_path)
    access.save_to_cache("GO", "GO:0000001", "a")
    # Nothing is persisted when caching is disabled.
    assert access.load_cache("GO") == {}


def test_get_label_no_prefix_returns_none():
    access = OntologyAccess(cache_labels=False)
    assert access.get_label("nocolon") is None


def test_unknown_prefix_tracked_when_config_loaded():
    access = OntologyAccess(cache_labels=False, oak_config_path=TEST_OAK_CONFIG)
    # BOGUS is not in the config, and a config is loaded, so no adapter is built.
    assert access.get_label("BOGUS:0000001") is None
    assert "BOGUS" in access.get_unknown_prefixes()


def test_get_label_resolves_local_ontology():
    """Offline label resolution via the local simpleobo test ontology."""
    access = OntologyAccess(cache_labels=False, oak_config_path=TEST_OAK_CONFIG)
    assert access.get_label("TEST:0000001") == "root term"


def test_non_sqlite_default_adapter_string_is_used(monkeypatch):
    """Default adapter strings other than sqlite:obo: should not be ignored."""
    calls = []

    class DummyAdapter:
        pass

    def fake_get_adapter(adapter_string):
        calls.append(adapter_string)
        return DummyAdapter()

    monkeypatch.setattr(oak_utils, "get_adapter", fake_get_adapter)

    access = OntologyAccess(oak_adapter_string="ubergraph:", cache_labels=False)

    assert isinstance(access.get_adapter("GO"), DummyAdapter)
    assert calls == ["ubergraph:"]


def test_ols_default_adapter_string_is_parameterized_by_prefix(monkeypatch):
    """The ols: shorthand should become ols:<prefix> for each prefix."""
    calls = []

    class DummyOlsAdapter:
        focus_ontology = None
        resource = SimpleNamespace(scheme="ols", slug="go")

    def fake_get_adapter(adapter_string):
        calls.append(adapter_string)
        return DummyOlsAdapter()

    monkeypatch.setattr(oak_utils, "get_adapter", fake_get_adapter)

    access = OntologyAccess(oak_adapter_string="ols:", cache_labels=False)
    adapter = access.get_adapter("GO")

    assert calls == ["ols:go"]
    assert adapter.focus_ontology == "go"


def test_ols_configured_adapter_string_is_parameterized_by_prefix(monkeypatch, tmp_path):
    """Per-prefix config can use ols: shorthand without losing focus ontology."""
    config = tmp_path / "oak_config.yaml"
    config.write_text('ontology_adapters:\n  GO: "ols:"\n')
    calls = []

    class DummyOlsAdapter:
        focus_ontology = None
        resource = SimpleNamespace(scheme="ols", slug="go")

    def fake_get_adapter(adapter_string):
        calls.append(adapter_string)
        return DummyOlsAdapter()

    monkeypatch.setattr(oak_utils, "get_adapter", fake_get_adapter)

    access = OntologyAccess(cache_labels=False, oak_config_path=config)
    adapter = access.get_adapter("GO")

    assert calls == ["ols:go"]
    assert adapter.focus_ontology == "go"


def test_get_label_extracts_ols4_embedded_label():
    """Older OAK OLS adapters return OLS4 term search payloads from get_term."""

    class DummyClient:
        def get_term(self, ontology, iri):
            assert ontology == "go"
            assert iri == "http://purl.obolibrary.org/obo/GO_0008150"
            return {"_embedded": {"terms": [{"label": "biological_process"}]}}

    class DummyOlsAdapter:
        focus_ontology = "go"
        client = DummyClient()

        def label(self, curie):
            return None

        def curie_to_uri(self, curie):
            return "http://purl.obolibrary.org/obo/GO_0008150"

    access = OntologyAccess(cache_labels=False)

    assert access._get_adapter_label(DummyOlsAdapter(), "GO:0008150") == "biological_process"


def test_get_label_extracts_ols4_direct_label():
    """The OLS label fallback also accepts already-flat term payloads."""

    class DummyClient:
        def get_term(self, ontology, iri):
            assert ontology == "go"
            assert iri == "http://purl.obolibrary.org/obo/GO_0008150"
            return {"label": "biological_process"}

    class DummyOlsAdapter:
        focus_ontology = "go"
        client = DummyClient()

        def label(self, curie):
            return None

        def curie_to_uri(self, curie):
            return "http://purl.obolibrary.org/obo/GO_0008150"

    access = OntologyAccess(cache_labels=False)

    assert access._get_adapter_label(DummyOlsAdapter(), "GO:0008150") == "biological_process"


# =============================================================================
# Offline mode (see issue #51): guarantee no external access, cache-only
# =============================================================================


def test_offline_never_builds_adapter():
    """In offline mode get_adapter always returns None (no ontology access)."""
    access = OntologyAccess(offline=True, oak_config_path=TEST_OAK_CONFIG)
    # TEST is configured, but offline mode still refuses to build an adapter.
    assert access.get_adapter("TEST") is None
    assert access.get_adapter("GO") is None


def test_offline_resolves_labels_from_cache(tmp_path):
    """Offline mode resolves labels from the file cache without any adapter."""
    # Populate the cache first (online-style write).
    writer = OntologyAccess(cache_labels=True, cache_dir=tmp_path)
    writer.save_to_cache("GO", "GO:0008150", "biological process")

    # Offline reader points at a default adapter string that would require a
    # download if it were ever built - but offline mode never builds it.
    access = OntologyAccess(
        oak_adapter_string="sqlite:obo:",
        cache_labels=False,
        cache_dir=tmp_path,
        offline=True,
    )
    assert access.get_label("GO:0008150") == "biological process"


def test_offline_cache_miss_returns_none(tmp_path):
    """A cache miss in offline mode returns None instead of reaching out."""
    access = OntologyAccess(cache_dir=tmp_path, offline=True)
    assert access.get_label("GO:9999999") is None


def test_offline_reads_cache_even_when_cache_labels_disabled(tmp_path):
    """Offline mode consults the cache even when label writing is disabled."""
    writer = OntologyAccess(cache_labels=True, cache_dir=tmp_path)
    writer.save_to_cache("GO", "GO:0000001", "a")

    access = OntologyAccess(cache_labels=False, cache_dir=tmp_path, offline=True)
    assert access.get_label("GO:0000001") == "a"


def test_offline_resolves_local_ontology_from_cache_not_adapter(tmp_path):
    """Offline mode does not fall back to the configured local ontology adapter."""
    # TEST is configured to a local simpleobo file, which would resolve online.
    access = OntologyAccess(
        cache_labels=False,
        cache_dir=tmp_path,
        oak_config_path=TEST_OAK_CONFIG,
        offline=True,
    )
    # Nothing cached yet, and offline refuses the adapter, so it is unresolved.
    assert access.get_label("TEST:0000001") is None


# =============================================================================
# Service outages: distinguish "unable to validate" from "term not found"
# =============================================================================


def test_is_connectivity_error_detects_wrapped_dns_failure():
    """The real requests ConnectionError wrapping a DNS gaierror is recognized."""
    assert is_connectivity_error(_dns_failure_connection_error()) is True


def test_is_connectivity_error_detects_requests_types_by_isinstance():
    """requests connection/timeout exceptions are matched by type, not name."""
    assert is_connectivity_error(requests.exceptions.ConnectionError("boom")) is True
    assert is_connectivity_error(requests.exceptions.ConnectTimeout("slow")) is True
    assert is_connectivity_error(requests.exceptions.ReadTimeout("slow")) is True


def test_is_connectivity_error_detects_builtin_and_socket_errors():
    assert is_connectivity_error(TimeoutError("slow")) is True
    assert is_connectivity_error(socket.gaierror("dns")) is True


def test_is_connectivity_error_ignores_non_connection_errors():
    """A plain error, or an HTTP 404, is not a connection-level outage."""
    assert is_connectivity_error(RuntimeError("404 Not Found")) is False
    assert is_connectivity_error(KeyError("_embedded")) is False
    # An HTTPError is a definitive HTTP answer, not a connection failure.
    assert is_connectivity_error(_http_error(404)) is False


def test_is_connectivity_error_respects_suppressed_context():
    """A suppressed context (raise ... from None) is not walked into.

    An unrelated in-flight connection error must not turn a deliberately
    context-suppressed error into a false "outage".
    """
    try:
        try:
            raise requests.exceptions.ConnectionError("in-flight outage")
        except requests.exceptions.ConnectionError:
            raise ValueError("definitive answer") from None
    except ValueError as e:
        assert e.__suppress_context__ is True
        assert is_connectivity_error(e) is False


def test_get_label_raises_service_unavailable_on_outage(monkeypatch):
    """A network outage during lookup fails fast, not "term not found"."""

    class DownAdapter:
        def label(self, curie):
            raise _dns_failure_connection_error()

    monkeypatch.setattr(oak_utils, "get_adapter", lambda s: DownAdapter())
    access = OntologyAccess(cache_labels=False)

    with pytest.raises(OntologyServiceUnavailableError) as exc_info:
        access.get_label("GO:0008150")
    assert exc_info.value.curie == "GO:0008150"
    # The failed lookup must not poison the in-memory cache with a bogus None.
    assert "GO:0008150" not in access._label_cache


def test_get_label_raises_service_unavailable_on_http_5xx(monkeypatch):
    """An HTTP 5xx means the service is reached but erroring: unable to validate."""

    class ErroringAdapter:
        def label(self, curie):
            raise _http_error(503)

    monkeypatch.setattr(oak_utils, "get_adapter", lambda s: ErroringAdapter())
    access = OntologyAccess(cache_labels=False)

    with pytest.raises(OntologyServiceUnavailableError):
        access.get_label("GO:0008150")


def test_get_label_treats_non_connectivity_error_as_not_found(monkeypatch):
    """An unexpected (non-network) adapter error is still treated as not found."""

    class BadAdapter:
        def label(self, curie):
            raise ValueError("malformed response")

    monkeypatch.setattr(oak_utils, "get_adapter", lambda s: BadAdapter())
    access = OntologyAccess(cache_labels=False)

    assert access.get_label("GO:0008150") is None


def test_get_label_treats_404_as_not_found(monkeypatch):
    """A definitive HTTP 404 remains a normal "term not found" (returns None)."""

    class NotFoundAdapter:
        def label(self, curie):
            raise _http_error(404)

    monkeypatch.setattr(oak_utils, "get_adapter", lambda s: NotFoundAdapter())
    access = OntologyAccess(cache_labels=False)

    assert access.get_label("GO:9999999") is None
