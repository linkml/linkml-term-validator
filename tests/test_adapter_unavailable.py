"""Tests for ``sqlite:obo:`` databases that cannot be built or loaded (#78).

For a prefix with no semantic-sql database, oaklib downloads the server's
error page as ``<slug>.db.gz`` (pystow saves an HTTP error body as the
download, cthoyt/pystow#184). Decompressing it raises ``BadGzipFile`` and
leaves an empty ``<slug>.db``, which every later run opens as a database with
no tables. Nothing in a prefix says whether it is an ontology with a
semantic-sql build (RO, ECO) or a generic namespace without one (dcterms,
schema), so a failure is classified by asking the server:

- No database (404), prefix not configured: expected for a generic namespace.
  The prefix gets no adapter and its terms are "not checked", not errors.
- No database, prefix configured in oak_config.yaml: a configuration error.
- The database exists but the cached copy is broken: the run stops with exit
  code 3 and names the file to delete.

No test touches the network or oaklib's real cache: ``get_adapter`` and the
server check are monkeypatched, and an "empty cached database" is a real
0-byte file opened through oaklib's ``sqlite:///`` adapter.
"""

import gzip
import sqlite3
from pathlib import Path

import pytest
import requests
from oaklib import get_adapter as oak_get_adapter
from typer.testing import CliRunner

from linkml_term_validator.cli import EXIT_ADAPTER_UNAVAILABLE, EXIT_SERVICE_UNAVAILABLE, app
from linkml_term_validator.utils import (
    OntologyAccess,
    OntologyAdapterUnavailableError,
    OntologyServiceUnavailableError,
    oak_utils,
)
from linkml_term_validator.utils.oak_utils import semsql_database_exists

TEST_ONTOLOGY = "simpleobo:tests/data/test_ontology.obo"

SCHEMA = """\
id: https://example.org/schema
name: schema
prefixes:
  linkml: https://w3id.org/linkml/
  TEST: http://example.org/TEST_
  dcterms: http://purl.org/dc/terms/
imports:
  - linkml:types
enums:
  Terms:
    permissible_values:
      root_term:
        title: root term
        meaning: TEST:0000001
      title:
        title: title
        meaning: dcterms:title
"""


def _http_error(status_code: int) -> requests.exceptions.HTTPError:
    response = requests.Response()
    response.status_code = status_code
    return requests.exceptions.HTTPError(f"{status_code} Error", response=response)


def _bad_gzip() -> gzip.BadGzipFile:
    return gzip.BadGzipFile("Not a gzipped file (b'<?')")


def _empty_db_adapter(tmp_path: Path) -> object:
    """A real oaklib adapter over a 0-byte file, as a failed download leaves."""
    db = tmp_path / "empty.db"
    db.touch()
    return oak_get_adapter(f"sqlite:///{db}")


def _semsql_db_adapter(tmp_path: Path) -> object:
    """A real oaklib adapter over a database that has the semantic-sql tables."""
    db = tmp_path / "good.db"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE statements (subject TEXT)")
    return oak_get_adapter(f"sqlite:///{db}")


class _Calls:
    """Records the adapter strings built and the slugs the server was asked about."""

    def __init__(self) -> None:
        self.built: list[str] = []
        self.probed: list[str] = []


@pytest.fixture
def calls():
    return _Calls()


@pytest.fixture
def fake_cache_path(monkeypatch, tmp_path):
    """Point the cache path named in messages at tmp_path, not oaklib's real cache."""
    monkeypatch.setattr(oak_utils, "semsql_cache_path", lambda slug: tmp_path / f"{slug}.db")
    return tmp_path


def _patch(monkeypatch, calls: _Calls, build: dict, server_has: dict) -> None:
    """Route adapter builds and server checks.

    Args:
        build: adapter string -> an exception to raise or an adapter to return.
            ``sqlite:obo:test`` defaults to the local test ontology.
        server_has: slug -> what ``semsql_database_exists`` answers.
    """

    def fake_get_adapter(adapter_string):
        calls.built.append(adapter_string)
        outcome = build.get(adapter_string)
        if outcome is None and adapter_string == "sqlite:obo:test":
            return oak_get_adapter(TEST_ONTOLOGY)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def fake_exists(slug, timeout=30):
        calls.probed.append(slug)
        answer = server_has[slug]
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(oak_utils, "get_adapter", fake_get_adapter)
    monkeypatch.setattr(oak_utils, "semsql_database_exists", fake_exists)


# ---------------------------------------------------------------------------
# OntologyAccess: classifying a broken sqlite:obo: adapter
# ---------------------------------------------------------------------------


def test_failed_download_without_database_is_not_checked(monkeypatch, calls, fake_cache_path):
    """First run for a generic namespace: no error, just no adapter."""
    _patch(monkeypatch, calls, {"sqlite:obo:dcterms": _bad_gzip()}, {"dcterms": False})
    access = OntologyAccess(cache_labels=False)

    assert access.get_adapter("dcterms") is None
    assert access.get_no_semsql_prefixes() == {"dcterms"}
    assert access.get_label("dcterms:title") is None
    assert access.get_unknown_prefixes() == {"dcterms"}
    # Nothing was searched, so it is "not checked", never "not found".
    assert access.not_found_in("dcterms:title") is None
    assert access.lookup_failed_in("dcterms:title") is None


def test_empty_cached_database_without_database_is_not_checked(
    monkeypatch, calls, fake_cache_path, tmp_path
):
    """Later runs: oaklib builds an adapter over the leftover 0-byte file."""
    _patch(
        monkeypatch,
        calls,
        {"sqlite:obo:dcterms": _empty_db_adapter(tmp_path)},
        {"dcterms": False},
    )
    access = OntologyAccess(cache_labels=False)

    assert access.get_adapter("dcterms") is None
    assert access.get_no_semsql_prefixes() == {"dcterms"}


@pytest.mark.parametrize("broken", ["bad_gzip", "empty_db"])
def test_broken_copy_of_existing_database_raises(
    monkeypatch, calls, fake_cache_path, tmp_path, broken
):
    outcome = _bad_gzip() if broken == "bad_gzip" else _empty_db_adapter(tmp_path)
    _patch(monkeypatch, calls, {"sqlite:obo:ro": outcome}, {"ro": True})
    access = OntologyAccess(cache_labels=False)

    with pytest.raises(OntologyAdapterUnavailableError) as exc_info:
        access.get_adapter("RO")
    error = exc_info.value
    assert error.prefix == "RO"
    assert error.adapter_string == "sqlite:obo:ro"
    message = str(error)
    assert "The semantic-sql server has a database for ro" in message
    assert f"Delete {fake_cache_path / 'ro.db'}" in message
    assert "map RO to another adapter in oak_config.yaml" in message


def test_adapter_error_is_kept_not_rebuilt_per_term(monkeypatch, calls, fake_cache_path):
    """The download is not retried, and later terms re-raise the same error."""
    _patch(monkeypatch, calls, {"sqlite:obo:ro": _bad_gzip()}, {"ro": True})
    access = OntologyAccess(cache_labels=False)

    for curie in ("RO:0002327", "RO:0000050"):
        with pytest.raises(OntologyAdapterUnavailableError):
            access.get_label(curie)
    assert calls.built == ["sqlite:obo:ro"]
    assert calls.probed == ["ro"]


def test_no_database_is_classified_once_per_prefix(monkeypatch, calls, fake_cache_path):
    _patch(monkeypatch, calls, {"sqlite:obo:dcterms": _bad_gzip()}, {"dcterms": False})
    access = OntologyAccess(cache_labels=False)

    for curie in ("dcterms:title", "dcterms:creator"):
        assert access.get_label(curie) is None
    assert calls.built == ["sqlite:obo:dcterms"]
    assert calls.probed == ["dcterms"]


def test_usable_database_is_not_checked_with_server(monkeypatch, calls, tmp_path):
    adapter = _semsql_db_adapter(tmp_path)
    _patch(monkeypatch, calls, {"sqlite:obo:ro": adapter}, {})
    access = OntologyAccess(cache_labels=False)

    assert access.get_adapter("RO") is adapter
    assert calls.probed == []


def test_download_error_with_404_needs_no_server_check(monkeypatch, calls, fake_cache_path):
    """Once pystow reports the HTTP error itself, its status is enough."""
    try:
        try:
            raise _http_error(404)
        except requests.exceptions.HTTPError as http_error:
            raise OSError("download failed") from http_error
    except OSError as e:
        download_error = e
    _patch(monkeypatch, calls, {"sqlite:obo:dcterms": download_error}, {})
    access = OntologyAccess(cache_labels=False)

    assert access.get_adapter("dcterms") is None
    assert access.get_no_semsql_prefixes() == {"dcterms"}
    assert calls.probed == []


def test_configured_prefix_without_database_raises(monkeypatch, calls, tmp_path):
    config = tmp_path / "oak_config.yaml"
    config.write_text("ontology_adapters:\n  dcterms: sqlite:obo:dcterms\n")
    _patch(monkeypatch, calls, {"sqlite:obo:dcterms": _bad_gzip()}, {"dcterms": False})
    access = OntologyAccess(cache_labels=False, oak_config_path=config)

    with pytest.raises(OntologyAdapterUnavailableError) as exc_info:
        access.get_adapter("dcterms")
    message = str(exc_info.value)
    assert "oak_config.yaml maps dcterms to sqlite:obo:dcterms" in message
    assert "has no database for dcterms" in message
    assert access.get_no_semsql_prefixes() == set()


def test_unknown_server_answer_raises_without_claiming_either_way(
    monkeypatch, calls, fake_cache_path
):
    _patch(monkeypatch, calls, {"sqlite:obo:ro": _bad_gzip()}, {"ro": None})
    access = OntologyAccess(cache_labels=False)

    with pytest.raises(OntologyAdapterUnavailableError) as exc_info:
        access.get_adapter("RO")
    assert "It is not known whether the server has a database for ro" in str(exc_info.value)


def test_server_outage_during_check_is_an_outage(monkeypatch, calls, fake_cache_path):
    """The check is retried like any lookup, then reported as an outage."""
    outage = OntologyServiceUnavailableError("ro.db", requests.exceptions.ConnectionError("down"))
    _patch(monkeypatch, calls, {"sqlite:obo:ro": _bad_gzip()}, {"ro": outage})
    access = OntologyAccess(cache_labels=False, service_retries=2)

    with pytest.raises(OntologyServiceUnavailableError):
        access.get_adapter("RO")
    assert calls.probed == ["ro", "ro", "ro"]
    assert calls.built == ["sqlite:obo:ro"]


def test_other_adapters_are_not_classified(monkeypatch, calls):
    """Only sqlite:obo: databases come from the semantic-sql server."""
    _patch(monkeypatch, calls, {"pronto:broken.obo.gz": _bad_gzip()}, {})
    access = OntologyAccess(cache_labels=False, oak_adapter_string="pronto:broken.obo.gz")

    with pytest.raises(gzip.BadGzipFile):
        access.get_adapter("X")
    assert calls.probed == []


# ---------------------------------------------------------------------------
# semsql_database_exists: the HEAD request
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            response = requests.Response()
            response.status_code = self.status_code
            raise requests.exceptions.HTTPError(f"{self.status_code}", response=response)


@pytest.mark.parametrize("status,expected", [(200, True), (404, False), (410, False), (403, None)])
def test_semsql_database_exists_reads_status(monkeypatch, status, expected):
    seen = {}

    def fake_head(url, **kwargs):
        seen["url"] = url
        seen["headers"] = kwargs.get("headers")
        return _FakeResponse(status)

    monkeypatch.setattr(requests, "head", fake_head)

    assert semsql_database_exists("dcterms") is expected
    assert seen["url"].endswith("/dcterms.db.gz")
    # The semantic-sql CDN rejects requests without oaklib's User-Agent.
    assert seen["headers"] == {"User-Agent": "oaklib"}


@pytest.mark.parametrize("failure", ["503", "connection"])
def test_semsql_database_exists_raises_outage(monkeypatch, failure):
    def fake_head(url, **kwargs):
        if failure == "connection":
            raise requests.exceptions.ConnectionError("no route")
        return _FakeResponse(503)

    monkeypatch.setattr(requests, "head", fake_head)

    with pytest.raises(OntologyServiceUnavailableError):
        semsql_database_exists("ro")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def schema(tmp_path):
    path = tmp_path / "schema.yaml"
    path.write_text(SCHEMA)
    return path


def _validate_schema(runner, schema, *extra):
    return runner.invoke(app, ["validate-schema", str(schema), "--no-cache", *extra])


def test_cli_generic_namespace_is_not_checked_and_passes(
    runner, monkeypatch, calls, fake_cache_path, schema
):
    _patch(monkeypatch, calls, {"sqlite:obo:dcterms": _bad_gzip()}, {"dcterms": False})

    result = _validate_schema(runner, schema)

    assert result.exit_code == 0, result.output
    assert "  - dcterms (no semantic-sql database)" in result.output
    assert result.output.strip().splitlines()[-1] == (
        "⚠️  No errors, but 1 meaning(s) could not be checked"
    )
    assert "Traceback" not in result.output


def test_cli_verbose_says_why_a_meaning_was_not_checked(
    runner, monkeypatch, calls, fake_cache_path, schema
):
    _patch(monkeypatch, calls, {"sqlite:obo:dcterms": _bad_gzip()}, {"dcterms": False})

    result = _validate_schema(runner, schema, "--verbose")

    assert result.exit_code == 0, result.output
    assert (
        "Terms.title: No semantic-sql database for dcterms, could not validate dcterms:title"
        in result.output
    )


def test_cli_generic_namespace_fails_in_strict_mode(
    runner, monkeypatch, calls, fake_cache_path, schema
):
    _patch(monkeypatch, calls, {"sqlite:obo:dcterms": _bad_gzip()}, {"dcterms": False})

    result = _validate_schema(runner, schema, "--strict")

    assert result.exit_code == 1, result.output
    assert (
        "Could not validate dcterms:title: the semantic-sql server has no database "
        "for dcterms (map dcterms to another adapter in oak_config.yaml)"
    ) in result.output


def test_cli_broken_copy_exits_3_without_traceback(
    runner, monkeypatch, calls, fake_cache_path, schema
):
    _patch(monkeypatch, calls, {"sqlite:obo:dcterms": _bad_gzip()}, {"dcterms": True})

    result = _validate_schema(runner, schema)

    assert result.exit_code == EXIT_ADAPTER_UNAVAILABLE == 3, result.output
    assert "Unable to validate dcterms terms: no usable adapter." in result.output
    assert f"Delete {fake_cache_path / 'dcterms.db'}" in result.output
    assert "Traceback" not in result.output


def test_cli_configured_prefix_without_database_exits_3(
    runner, monkeypatch, calls, schema, tmp_path
):
    config = tmp_path / "oak_config.yaml"
    config.write_text(
        f"ontology_adapters:\n  TEST: {TEST_ONTOLOGY}\n  dcterms: sqlite:obo:dcterms\n"
    )
    _patch(monkeypatch, calls, {"sqlite:obo:dcterms": _bad_gzip()}, {"dcterms": False})

    result = _validate_schema(runner, schema, "--config", str(config))

    assert result.exit_code == EXIT_ADAPTER_UNAVAILABLE, result.output
    assert "oak_config.yaml maps dcterms to sqlite:obo:dcterms" in result.output


def test_cli_server_outage_during_check_exits_2(
    runner, monkeypatch, calls, fake_cache_path, schema
):
    outage = OntologyServiceUnavailableError(
        "dcterms.db", requests.exceptions.ConnectionError("down")
    )
    _patch(monkeypatch, calls, {"sqlite:obo:dcterms": _bad_gzip()}, {"dcterms": outage})

    result = _validate_schema(runner, schema)

    assert result.exit_code == EXIT_SERVICE_UNAVAILABLE, result.output
    assert "Unable to validate at this time" in result.output


def test_cli_validate_data_broken_copy_exits_3(
    runner, monkeypatch, calls, fake_cache_path, tmp_path
):
    """The plugins reach the same adapter code, so validate-data stops too."""
    config = tmp_path / "oak_config.yaml"
    config.write_text("ontology_adapters:\n  TEST: sqlite:obo:test\n")
    _patch(monkeypatch, calls, {"sqlite:obo:test": _bad_gzip()}, {"test": True})

    result = runner.invoke(
        app,
        [
            "validate-data",
            "tests/data/dynamic_enum_valid_data.yaml",
            "--schema",
            "tests/data/dynamic_enum_schema.yaml",
            "--target-class",
            "Sample",
            "--config",
            str(config),
            "--no-bindings",
            "--no-cache",
        ],
    )

    assert result.exit_code == EXIT_ADAPTER_UNAVAILABLE, result.output
    assert "Unable to validate TEST terms" in result.output
