"""Tests for riding out a transient ontology-service failure.

A remote ontology service stalls or drops the occasional request without being
down. Before retries, one such request aborted the whole run with "unable to
validate", which is enough to fail a CI build whose data is fine - and on
whichever term happened to be in flight, so the failure said nothing about the
diff (monarch-initiative/dismech#10396). These tests pin the retry behavior and,
just as importantly, pin what is *not* retried: a definitive answer about a term.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from typer.testing import CliRunner

from linkml_term_validator.cli import app
from linkml_term_validator.plugins import DynamicEnumPlugin
from linkml_term_validator.utils import (
    DEFAULT_SERVICE_RETRIES,
    MAX_SERVICE_RETRY_DELAY,
    OntologyAccess,
    OntologyServiceUnavailableError,
    RetryPolicy,
    oak_utils,
    parse_retry_config,
    resolve_retry_policy,
    retry_on_service_unavailable,
)

GO_LABELS = {"GO:0008150": "biological_process", "GO:0007049": "cell cycle"}


def _read_timeout() -> requests.exceptions.ReadTimeout:
    """The exact shape an EBI/OLS stall produces."""
    return requests.exceptions.ReadTimeout(
        "HTTPSConnectionPool(host='www.ebi.ac.uk', port=443): "
        "Read timed out. (read timeout=5)"
    )


def _not_found() -> requests.exceptions.HTTPError:
    """A definitive "this term does not exist" from a remote service."""
    response = requests.Response()
    response.status_code = 404
    return requests.exceptions.HTTPError("404 Client Error", response=response)


class FlakyAdapter:
    """Times out for the first ``failures`` calls, then answers normally."""

    def __init__(self, failures: int, labels: dict[str, str] | None = None):
        self.failures = failures
        self.labels = labels if labels is not None else GO_LABELS
        self.calls = 0

    def label(self, curie: str):
        self.calls += 1
        if self.calls <= self.failures:
            raise _read_timeout()
        return self.labels.get(curie)


@pytest.fixture
def access_factory(monkeypatch):
    """Build an OntologyAccess whose single adapter is a given fake."""

    def build(adapter, **kwargs) -> OntologyAccess:
        monkeypatch.setattr(oak_utils, "get_adapter", lambda s: adapter)
        return OntologyAccess(cache_labels=False, **kwargs)

    return build


# =============================================================================
# The policy itself
# =============================================================================


def test_default_policy_makes_three_attempts():
    assert RetryPolicy().retries == DEFAULT_SERVICE_RETRIES
    assert RetryPolicy().attempts == 3


@pytest.mark.parametrize("bad", [{"retries": -1}, {"backoff": -0.5}])
def test_policy_rejects_negative_settings(bad):
    with pytest.raises(ValueError):
        RetryPolicy(**bad)


def test_retry_helper_only_retries_a_service_outage():
    """A definitive failure is a result, not a hiccup: it is raised at once."""
    attempts = []

    def definitive():
        attempts.append(1)
        raise ValueError("this term is simply wrong")

    with pytest.raises(ValueError):
        retry_on_service_unavailable(definitive, sleep=lambda seconds: None)
    assert attempts == [1]


def test_retry_helper_reports_the_outage_once_attempts_are_spent():
    def always_down():
        raise OntologyServiceUnavailableError("GO:0008150")

    with pytest.raises(OntologyServiceUnavailableError):
        retry_on_service_unavailable(
            always_down, policy=RetryPolicy(retries=1), sleep=lambda seconds: None
        )


def test_backoff_doubles_between_attempts():
    delays: list[float] = []

    def always_down():
        raise OntologyServiceUnavailableError("GO:0008150")

    with pytest.raises(OntologyServiceUnavailableError):
        retry_on_service_unavailable(
            always_down,
            policy=RetryPolicy(retries=3, backoff=0.5),
            sleep=delays.append,
        )
    assert delays == [0.5, 1.0, 2.0]


# =============================================================================
# Where the settings come from
# =============================================================================


def test_explicit_settings_win_over_the_config_file():
    config = {"service_retries": 7, "service_retry_backoff": 9.0}
    assert resolve_retry_policy(config=config) == RetryPolicy(retries=7, backoff=9.0)
    assert resolve_retry_policy(retries=0, config=config) == RetryPolicy(retries=0, backoff=9.0)
    assert resolve_retry_policy(backoff=0.25, config=config) == RetryPolicy(
        retries=7, backoff=0.25
    )


@pytest.mark.parametrize(
    "config",
    [
        {"service_retries": "lots"},
        {"service_retries": -1},
        {"service_retries": True},
        {"service_retry_backoff": "soon"},
        {"service_retry_backoff": -1},
    ],
)
def test_bad_config_values_are_rejected_not_ignored(config):
    with pytest.raises(ValueError):
        parse_retry_config(config)


def test_ontology_access_reads_the_policy_from_oak_config(tmp_path):
    config_path = tmp_path / "oak_config.yaml"
    config_path.write_text("service_retries: 4\nservice_retry_backoff: 0.25\n")

    access = OntologyAccess(cache_labels=False, oak_config_path=config_path)
    assert access.retry_policy == RetryPolicy(retries=4, backoff=0.25)

    explicit = OntologyAccess(cache_labels=False, oak_config_path=config_path, service_retries=0)
    assert explicit.retry_policy == RetryPolicy(retries=0, backoff=0.25)


# =============================================================================
# Lookups
# =============================================================================


def test_get_label_survives_a_transient_timeout(access_factory):
    adapter = FlakyAdapter(failures=2)
    access = access_factory(adapter)

    assert access.get_label("GO:0008150") == "biological_process"
    assert adapter.calls == 3


def test_get_label_gives_up_once_the_retries_are_spent(access_factory):
    adapter = FlakyAdapter(failures=99)
    access = access_factory(adapter)

    with pytest.raises(OntologyServiceUnavailableError):
        access.get_label("GO:0008150")
    assert adapter.calls == RetryPolicy().attempts
    # A spent outage must not poison the cache with a bogus "no label".
    assert "GO:0008150" not in access._label_cache


def test_retries_zero_restores_fail_fast(access_factory):
    adapter = FlakyAdapter(failures=1)
    access = access_factory(adapter, service_retries=0)

    with pytest.raises(OntologyServiceUnavailableError):
        access.get_label("GO:0008150")
    assert adapter.calls == 1


def test_a_missing_term_is_not_retried(access_factory):
    """404 is an answer about the term, so it costs one call and no backoff."""

    class MissingTermAdapter:
        def __init__(self):
            self.calls = 0

        def label(self, curie):
            self.calls += 1
            raise _not_found()

    adapter = MissingTermAdapter()
    access = access_factory(adapter)

    assert access.get_label("GO:0000000") is None
    assert adapter.calls == 1


def test_backoff_is_applied_between_lookup_attempts(access_factory):
    adapter = FlakyAdapter(failures=2)
    access = access_factory(adapter, service_retry_backoff=0.5)
    delays: list[float] = []
    access._sleep = delays.append

    assert access.get_label("GO:0008150") == "biological_process"
    assert delays == [0.5, 1.0]


def test_alias_lookup_survives_a_transient_timeout(access_factory):
    """The Not4Curation check reads aliases, so it needs the same protection."""

    class FlakyAliasAdapter:
        def __init__(self, failures):
            self.failures = failures
            self.calls = 0

        def entity_aliases(self, curie):
            self.calls += 1
            if self.calls <= self.failures:
                raise _read_timeout()
            return ["cell cycle", "Not4Curation"]

    adapter = FlakyAliasAdapter(failures=2)
    access = access_factory(adapter)

    assert access.entity_aliases("GO:0007049") == ["cell cycle", "Not4Curation"]
    assert adapter.calls == 3


def test_obsolescence_lookup_survives_a_transient_timeout(access_factory):
    class FlakyObsoletesAdapter:
        def __init__(self, failures):
            self.failures = failures
            self.calls = 0

        def obsoletes(self):
            self.calls += 1
            if self.calls <= self.failures:
                raise _read_timeout()
            return ["GO:0000005"]

    adapter = FlakyObsoletesAdapter(failures=2)
    access = access_factory(adapter)

    assert access.is_obsolete("GO:0000005") is True
    assert adapter.calls == 3


# =============================================================================
# Adapter construction
# =============================================================================


def test_adapter_construction_survives_a_transient_timeout(monkeypatch):
    """Building an adapter can download a database, so it fails the same way."""
    attempts = []

    def flaky_get_adapter(adapter_string):
        attempts.append(adapter_string)
        if len(attempts) < 3:
            raise _read_timeout()
        return FlakyAdapter(failures=0)

    monkeypatch.setattr(oak_utils, "get_adapter", flaky_get_adapter)
    access = OntologyAccess(cache_labels=False)

    assert access.get_label("GO:0008150") == "biological_process"
    assert len(attempts) == 3


def test_a_bad_adapter_string_is_not_an_outage(monkeypatch):
    """A configuration error is definitive: raised as itself, and only once."""
    attempts = []

    def bad_get_adapter(adapter_string):
        attempts.append(adapter_string)
        raise ValueError(f"unknown adapter scheme: {adapter_string}")

    monkeypatch.setattr(oak_utils, "get_adapter", bad_get_adapter)
    access = OntologyAccess(cache_labels=False)

    with pytest.raises(ValueError):
        access.get_label("GO:0008150")
    assert len(attempts) == 1


# =============================================================================
# Graph traversal (dynamic enums)
# =============================================================================


def test_graph_traversal_survives_a_transient_timeout(monkeypatch):
    class FlakyGraphAdapter:
        def __init__(self, failures):
            self.failures = failures
            self.calls = 0

        def ancestors(self, curies, predicates=None, reflexive=False):
            self.calls += 1
            if self.calls <= self.failures:
                raise _read_timeout()
            return {"GO:0008150", "GO:0007049"}

    adapter = FlakyGraphAdapter(failures=2)
    plugin = DynamicEnumPlugin(cache_labels=False, cache_enum_expansions=False)

    values = plugin._traverse(
        adapter=adapter,
        method_name="ancestors",
        start_curie="GO:0007049",
        predicates=["rdfs:subClassOf"],
        reflexive=True,
    )

    assert "GO:0008150" in values
    assert adapter.calls == 3


def test_graph_traversal_retries_a_lazily_raised_timeout(monkeypatch):
    """OAK traversals often answer with a generator.

    The requests then run while the result is being materialized, not when the
    method is called, so a timeout raised there must still be classified and
    retried - otherwise the traversal half of the retry never fires.
    """

    class LazyGraphAdapter:
        def __init__(self, failures):
            self.failures = failures
            self.calls = 0

        def ancestors(self, curies, predicates=None, reflexive=False):
            self.calls += 1
            fails = self.calls <= self.failures

            def walk():
                yield "GO:0007049"
                if fails:
                    raise _read_timeout()
                yield "GO:0008150"

            return walk()

    adapter = LazyGraphAdapter(failures=2)
    plugin = DynamicEnumPlugin(cache_labels=False, cache_enum_expansions=False)

    values = plugin._traverse(
        adapter=adapter,
        method_name="ancestors",
        start_curie="GO:0007049",
        predicates=["rdfs:subClassOf"],
        reflexive=True,
    )

    assert values == {"GO:0007049", "GO:0008150"}
    assert adapter.calls == 3


# =============================================================================
# OLS term payloads
# =============================================================================


class FlakyOlsAdapter:
    """An OLS-shaped adapter whose term payload times out the first calls."""

    def __init__(self, failures: int):
        self.failures = failures
        self.calls = 0
        self.resource = SimpleNamespace(scheme="ols", slug="go")
        self.focus_ontology = "go"
        self.client = self

    def curie_to_uri(self, curie):
        return f"http://purl.obolibrary.org/obo/{curie.replace(':', '_')}"

    def get_term(self, ontology, iri):
        self.calls += 1
        if self.calls <= self.failures:
            raise _read_timeout()
        return {"label": "cell cycle", "is_obsolete": True, "synonyms": ["Not4Curation"]}


def test_ols_obsolescence_check_survives_a_transient_timeout(access_factory):
    adapter = FlakyOlsAdapter(failures=2)
    access = access_factory(adapter)

    assert access.is_obsolete("GO:0007049") is True
    assert adapter.calls == 3


def test_ols_alias_read_survives_a_transient_timeout(access_factory):
    adapter = FlakyOlsAdapter(failures=2)
    access = access_factory(adapter)

    assert access.entity_aliases("GO:0007049") == ["cell cycle", "Not4Curation"]
    assert adapter.calls == 3


# =============================================================================
# Retry-After
# =============================================================================


def _rate_limited(retry_after: str) -> OntologyServiceUnavailableError:
    """A 429 carrying a Retry-After header, wrapped as the lookups wrap it."""
    response = requests.Response()
    response.status_code = 429
    response.headers["Retry-After"] = retry_after
    original = requests.exceptions.HTTPError("429 Too Many Requests", response=response)
    try:
        raise OntologyServiceUnavailableError("GO:0008150", original) from original
    except OntologyServiceUnavailableError as exc:
        return exc


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        # The service asks for longer than the backoff: it wins.
        ("5", 5.0),
        # It asks for less: the backoff stands.
        ("0", 0.5),
        # An outlandish value is clamped rather than hanging the run.
        ("99999", MAX_SERVICE_RETRY_DELAY),
        # The HTTP-date form is not guessed at; the backoff stands.
        ("Wed, 21 Oct 2026 07:28:00 GMT", 0.5),
    ],
)
def test_retry_after_steers_the_wait(header, expected):
    delays: list[float] = []
    rate_limited = _rate_limited(header)

    def always_rate_limited():
        raise rate_limited

    with pytest.raises(OntologyServiceUnavailableError):
        retry_on_service_unavailable(
            always_rate_limited,
            policy=RetryPolicy(retries=1, backoff=0.5),
            sleep=delays.append,
        )
    assert delays == [expected]


def test_the_spent_attempts_are_recorded_on_the_error():
    def always_down():
        raise OntologyServiceUnavailableError("GO:0008150")

    with pytest.raises(OntologyServiceUnavailableError) as exc_info:
        retry_on_service_unavailable(
            always_down, policy=RetryPolicy(retries=2), sleep=lambda seconds: None
        )
    assert exc_info.value.attempts == 3


def test_ols_descendant_fallback_retries_a_lazily_raised_timeout(monkeypatch):
    """The fallback OLS paging is the path OLS-backed enums actually take.

    ``client.get_paged`` is a generator too, so the requests run while the
    result set is built - it needs the same classification and retry as
    ``_call_graph_traversal``.
    """

    class FlakyPagingClient:
        def __init__(self, failures):
            self.failures = failures
            self.calls = 0

        def get_paged(self, path, key):
            self.calls += 1
            fails = self.calls <= self.failures

            def pages():
                yield {"obo_id": "GO:0009987"}
                if fails:
                    raise _read_timeout()
                yield {"obo_id": "GO:0000278"}

            return pages()

    class EmptyDescendantsOlsAdapter:
        focus_ontology = "go"

        def __init__(self, failures):
            self.client = FlakyPagingClient(failures)

        def curie_to_uri(self, curie):
            return f"http://purl.obolibrary.org/obo/{curie.replace(':', '_')}"

        def descendants(self, curies, predicates=None, reflexive=False):
            return set()

    adapter = EmptyDescendantsOlsAdapter(failures=2)
    monkeypatch.setattr(oak_utils, "get_adapter", lambda s: adapter)
    plugin = DynamicEnumPlugin(cache_labels=False, cache_enum_expansions=False)

    query = SimpleNamespace(
        source_nodes=["GO:0007049"],
        relationship_types=["rdfs:subClassOf"],
        traverse_up=False,
        include_self=False,
    )
    values = plugin._expand_reachable_from(query)

    assert values == {"GO:0009987", "GO:0000278"}
    assert adapter.client.calls == 3


def test_ols_descendant_fallback_classifies_a_persistent_timeout(monkeypatch):
    """Once the retries are spent it is "unable to validate", not a traceback."""

    class DownPagingClient:
        def get_paged(self, path, key):
            def pages():
                yield {"obo_id": "GO:0009987"}
                raise _read_timeout()

            return pages()

    class DownOlsAdapter:
        focus_ontology = "go"
        client = DownPagingClient()

        def curie_to_uri(self, curie):
            return f"http://purl.obolibrary.org/obo/{curie.replace(':', '_')}"

        def descendants(self, curies, predicates=None, reflexive=False):
            return set()

    monkeypatch.setattr(oak_utils, "get_adapter", lambda s: DownOlsAdapter())
    plugin = DynamicEnumPlugin(cache_labels=False, cache_enum_expansions=False)

    query = SimpleNamespace(
        source_nodes=["GO:0007049"],
        relationship_types=["rdfs:subClassOf"],
        traverse_up=False,
        include_self=False,
    )
    with pytest.raises(OntologyServiceUnavailableError):
        plugin._expand_reachable_from(query)


# =============================================================================
# End to end through the CLI
# =============================================================================


@pytest.fixture
def runner():
    return CliRunner()


def _validate_schema(runner, tmp_path, extra_args=()):
    schema_path = Path(__file__).parent.parent / "examples" / "simple_schema.yaml"
    return runner.invoke(
        app,
        [
            "validate-schema",
            str(schema_path),
            "--no-cache",
            "--cache-dir",
            str(tmp_path / "cache"),
            *extra_args,
        ],
    )


def test_validate_schema_rides_out_a_stalled_request(runner, tmp_path, monkeypatch):
    """The build that used to die on one read timeout now passes."""
    adapter = FlakyAdapter(failures=2)
    monkeypatch.setattr(oak_utils, "get_adapter", lambda s: adapter)

    result = _validate_schema(runner, tmp_path)

    assert result.exit_code == 0, result.output
    assert "Unable to validate at this time" not in result.output


def test_validate_schema_retries_zero_fails_fast(runner, tmp_path, monkeypatch):
    """--retries 0 is the way back to the old behavior."""
    adapter = FlakyAdapter(failures=1)
    monkeypatch.setattr(oak_utils, "get_adapter", lambda s: adapter)

    result = _validate_schema(runner, tmp_path, ["--retries", "0"])

    assert result.exit_code == 2, result.output
    assert "Unable to validate at this time" in result.output
    assert adapter.calls == 1


def test_validate_schema_help_documents_the_retry_flags(runner):
    result = runner.invoke(app, ["validate-schema", "--help"])
    assert result.exit_code == 0
    assert "--retries" in result.output
    assert "--retry-wait" in result.output


def test_the_outage_message_reports_the_attempts_spent(runner, tmp_path, monkeypatch):
    adapter = FlakyAdapter(failures=99)
    monkeypatch.setattr(oak_utils, "get_adapter", lambda s: adapter)

    result = _validate_schema(runner, tmp_path)

    assert result.exit_code == 2, result.output
    assert "after 3 attempts" in result.output


def test_the_outage_message_claims_no_retry_when_there_was_none(runner, tmp_path, monkeypatch):
    adapter = FlakyAdapter(failures=99)
    monkeypatch.setattr(oak_utils, "get_adapter", lambda s: adapter)

    result = _validate_schema(runner, tmp_path, ["--retries", "0"])

    assert result.exit_code == 2, result.output
    assert "after 1 attempt." in result.output
    assert "attempts" not in result.output
