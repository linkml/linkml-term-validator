"""Tests for meanings that do not resolve to a label (#77).

``validate-schema`` used to treat every unresolved ``meaning`` under an
unconfigured prefix as INFO ("Unconfigured prefix, could not validate"), even
when an auto-created ``sqlite:obo:<prefix>`` adapter had been built and
searched. INFO was never printed, the term was counted as validated, and the
run closed with "No issues found". A CURIE that does not exist passed.

Two cases are now told apart:

- An adapter was searched and has no such term: an ERROR, configured prefix
  or not, naming the adapter that was searched.
- There was no adapter to ask (oak_config.yaml is loaded but does not list the
  prefix): still INFO, but counted as "not checked", listed under --verbose,
  and the run does not claim a clean pass.

Everything runs offline against ``tests/data/test_ontology.obo``. Passing it
as ``--adapter`` without a config stands in for the auto-created
``sqlite:obo:<prefix>`` adapter: the prefix is unconfigured, yet an adapter is
built and searched.
"""

from pathlib import Path

import pytest
import requests
from typer.testing import CliRunner

from linkml_term_validator.cli import app
from linkml_term_validator.models import SeverityLevel, ValidationConfig
from linkml_term_validator.utils import oak_utils
from linkml_term_validator.validator import EnumValidator

TEST_ONTOLOGY = "simpleobo:tests/data/test_ontology.obo"
OAK_CONFIG = Path("tests/data/test_oak_config.yaml")

SCHEMA_TEMPLATE = """\
id: https://example.org/schema
name: schema
prefixes:
  linkml: https://w3id.org/linkml/
  TEST: http://example.org/TEST_
  OTHER: http://example.org/OTHER_
imports:
  - linkml:types
enums:
  Terms:
    permissible_values:
      root_term:
        title: root term
        meaning: TEST:0000001
      extra:
        title: extra
        meaning: {meaning}
"""

FAKE_TERM = "TEST:0000999"  # TEST prefix, absent from the test ontology
NO_ADAPTER_TERM = "OTHER:0000001"  # not listed in test_oak_config.yaml


@pytest.fixture
def runner():
    return CliRunner()


def _schema(tmp_path: Path, meaning: str) -> Path:
    path = tmp_path / "schema.yaml"
    path.write_text(SCHEMA_TEMPLATE.format(meaning=meaning))
    return path


def _validator(**config) -> EnumValidator:
    return EnumValidator(ValidationConfig(cache_labels=False, **config))


def _http_error(status_code: int) -> requests.exceptions.HTTPError:
    response = requests.Response()
    response.status_code = status_code
    return requests.exceptions.HTTPError(f"{status_code} Error", response=response)


def _failing_adapter(monkeypatch, exc: Exception) -> None:
    """Make every adapter's label lookup raise ``exc`` (not an outage)."""

    class FailingAdapter:
        def label(self, curie):
            raise exc

    monkeypatch.setattr(oak_utils, "get_adapter", lambda s: FailingAdapter())


# ---------------------------------------------------------------------------
# EnumValidator
# ---------------------------------------------------------------------------


def test_missing_term_in_auto_adapter_is_error(tmp_path):
    """An adapter was searched, so a missing term fails without --strict."""
    validator = _validator(oak_adapter_string=TEST_ONTOLOGY)
    result = validator.validate_schema(_schema(tmp_path, FAKE_TERM))

    errors = [i for i in result.issues if i.is_error()]
    assert [e.meaning for e in errors] == [FAKE_TERM]
    message = errors[0].message
    assert f"{FAKE_TERM} not found in {TEST_ONTOLOGY}" in message
    # The prefix has no oak_config entry, so suggest mapping one (BFO -> RO).
    assert "map TEST to that ontology's adapter in oak_config.yaml" in message
    assert result.total_meanings_unchecked == 0


def test_missing_term_in_configured_adapter_is_error(tmp_path):
    """A configured prefix names the adapter but needs no mapping hint."""
    validator = _validator(oak_config_path=OAK_CONFIG)
    result = validator.validate_schema(_schema(tmp_path, FAKE_TERM))

    errors = [i for i in result.issues if i.is_error()]
    assert len(errors) == 1
    assert errors[0].message == f"Term {FAKE_TERM} not found in {TEST_ONTOLOGY}"


@pytest.mark.parametrize(
    "exc", [_http_error(401), _http_error(403), ValueError("malformed response")]
)
def test_failed_lookup_is_error_but_not_called_missing(monkeypatch, tmp_path, exc):
    """A lookup that raised (a wrong API key, a parse error) fails closed, but
    must not tell the user to fix a CURIE that may well exist."""
    _failing_adapter(monkeypatch, exc)
    validator = _validator(oak_adapter_string=TEST_ONTOLOGY)
    result = validator.validate_schema(_schema(tmp_path, FAKE_TERM))

    errors = [i for i in result.issues if i.is_error()]
    # Both meanings were looked up, and both lookups failed.
    assert {e.meaning for e in errors} == {"TEST:0000001", FAKE_TERM}
    for error in errors:
        assert error.message == (
            f"Could not retrieve label for {error.meaning} from {TEST_ONTOLOGY}: "
            "the lookup failed with an error (see the logged warning)"
        )
        assert "not found" not in error.message
    assert result.total_meanings_unchecked == 0


@pytest.mark.parametrize("status", [404, 410])
def test_http_not_found_is_reported_missing(monkeypatch, tmp_path, status):
    _failing_adapter(monkeypatch, _http_error(status))
    validator = _validator(oak_adapter_string=TEST_ONTOLOGY)
    result = validator.validate_schema(_schema(tmp_path, FAKE_TERM))

    messages = [i.message for i in result.issues if i.is_error()]
    assert len(messages) == 2
    assert all(m.startswith("Term TEST:") and " not found in " in m for m in messages)


def test_term_without_adapter_is_unchecked_not_error(tmp_path):
    """No adapter to ask: INFO, counted as not checked rather than validated."""
    validator = _validator(oak_config_path=OAK_CONFIG)
    result = validator.validate_schema(_schema(tmp_path, NO_ADAPTER_TERM))

    assert not result.has_errors()
    assert [i.severity for i in result.issues] == [SeverityLevel.INFO]
    assert result.total_meanings_checked == 2
    assert result.total_meanings_unchecked == 1
    assert validator.get_unchecked_terms() == {NO_ADAPTER_TERM}
    assert result.clean_run_message() == (
        "⚠️  No errors, but 1 meaning(s) could not be checked"
    )


def test_term_without_adapter_is_error_in_strict_mode(tmp_path):
    validator = _validator(oak_config_path=OAK_CONFIG, strict_mode=True)
    result = validator.validate_schema(_schema(tmp_path, NO_ADAPTER_TERM))

    assert result.error_count() == 1
    assert result.total_meanings_unchecked == 0


def test_curie_label_pair_missing_from_auto_adapter_is_error():
    validator = _validator(oak_adapter_string=TEST_ONTOLOGY)
    issues = validator.validate_curie_label_pairs([(FAKE_TERM, "x", "line:1")])

    assert [i.severity for i in issues] == [SeverityLevel.ERROR]
    assert "not found in" in issues[0].message
    assert validator.get_unchecked_terms() == set()


def test_curie_label_pair_without_adapter_is_unchecked():
    validator = _validator(oak_config_path=OAK_CONFIG)
    issues = validator.validate_curie_label_pairs([(NO_ADAPTER_TERM, "x", "line:1")])

    assert issues == []
    assert validator.get_unchecked_terms() == {NO_ADAPTER_TERM}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("verbose", [False, True])
def test_cli_missing_term_fails_without_strict(runner, tmp_path, verbose):
    args = [
        "validate-schema",
        str(_schema(tmp_path, FAKE_TERM)),
        "--adapter",
        TEST_ONTOLOGY,
        "--no-cache",
    ]
    if verbose:
        args.append("--verbose")
    result = runner.invoke(app, args)

    assert result.exit_code == 1, result.output
    assert f"{FAKE_TERM} not found in" in result.output
    assert "No issues found" not in result.output


def test_cli_unchecked_term_is_not_a_clean_pass(runner, tmp_path):
    result = runner.invoke(
        app,
        [
            "validate-schema",
            str(_schema(tmp_path, NO_ADAPTER_TERM)),
            "--config",
            str(OAK_CONFIG),
            "--no-cache",
        ],
    )

    # Nothing was found wrong, so the exit code stays 0; but no bare ✅.
    assert result.exit_code == 0, result.output
    assert result.output.strip().splitlines()[-1] == (
        "⚠️  No errors, but 1 meaning(s) could not be checked"
    )
    assert "Unknown prefixes encountered" in result.output


def test_cli_verbose_lists_unchecked_meanings(runner, tmp_path):
    result = runner.invoke(
        app,
        [
            "validate-schema",
            str(_schema(tmp_path, NO_ADAPTER_TERM)),
            "--config",
            str(OAK_CONFIG),
            "--no-cache",
            "--verbose",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Meanings validated: 1\n" in result.output
    assert "Meanings not checked (no adapter for prefix): 1" in result.output
    assert "INFO (1):" in result.output
    assert f"Terms.extra: Unconfigured prefix, could not validate {NO_ADAPTER_TERM}" in (
        result.output
    )
    assert "No issues found" not in result.output
    assert "No errors, but 1 meaning(s) could not be checked" in result.output


@pytest.mark.parametrize("verbose", [False, True])
def test_cli_fully_checked_schema_still_reports_clean(runner, tmp_path, verbose):
    schema = tmp_path / "schema.yaml"
    schema.write_text(
        SCHEMA_TEMPLATE.format(meaning="TEST:0000002").replace(
            "title: extra", "title: child term one"
        )
    )
    args = ["validate-schema", str(schema), "--config", str(OAK_CONFIG), "--no-cache"]
    if verbose:
        args.append("--verbose")
    result = runner.invoke(app, args)

    assert result.exit_code == 0, result.output
    assert "Meanings not checked" not in result.output
    expected = "✅ No issues found!" if verbose else "✅"
    assert result.output.strip().splitlines()[-1] == expected


def test_cli_text_file_unchecked_is_not_all_validated(runner, tmp_path):
    text_file = tmp_path / "doc.md"
    text_file.write_text(
        '- @term TEST:0000001 "root term"\n'
        f'- @term {NO_ADAPTER_TERM} "something"\n'
    )

    result = runner.invoke(
        app,
        [
            "validate-text-file",
            str(text_file),
            "--config",
            str(OAK_CONFIG),
            "--no-cache",
            "--verbose",
        ],
    )

    assert result.exit_code == 0, result.output
    assert f'{NO_ADAPTER_TERM} "something" (not checked: no adapter)' in result.output
    assert "All 2 CURIE(s) validated successfully" not in result.output
    assert "No errors, but 1 of 2 CURIE(s) could not be checked" in result.output


def test_cli_failed_lookup_fails_with_lookup_message(runner, monkeypatch, tmp_path):
    _failing_adapter(monkeypatch, _http_error(401))
    result = runner.invoke(
        app,
        [
            "validate-schema",
            str(_schema(tmp_path, FAKE_TERM)),
            "--adapter",
            TEST_ONTOLOGY,
            "--no-cache",
        ],
    )

    assert result.exit_code == 1, result.output
    assert f"Could not retrieve label for {FAKE_TERM}" in result.output
    assert "the lookup failed with an error" in result.output
    assert "not found in" not in result.output


def test_cli_text_file_missing_term_in_auto_adapter_fails(runner, tmp_path):
    text_file = tmp_path / "doc.md"
    text_file.write_text(f'- @term {FAKE_TERM} "nonexistent"\n')

    result = runner.invoke(
        app,
        ["validate-text-file", str(text_file), "--adapter", TEST_ONTOLOGY, "--no-cache"],
    )

    assert result.exit_code == 1, result.output
    assert f"{FAKE_TERM} not found in" in result.output
