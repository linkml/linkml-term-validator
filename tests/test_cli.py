"""Tests for linkml-term-validator CLI commands using CliRunner."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from linkml_term_validator.cli import app


@pytest.fixture
def runner():
    """Create a CliRunner for testing."""
    return CliRunner()


@pytest.fixture
def examples_dir():
    """Get the examples directory."""
    return Path(__file__).parent.parent / "examples"


@pytest.fixture
def tests_data_dir():
    """Get the tests/data directory."""
    return Path(__file__).parent / "data"


def test_cli_help(runner):
    """Test that CLI help works."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "linkml-term-validator" in result.output
    assert "Validating external terms" in result.output


def test_validate_schema_help(runner):
    """Test validate-schema help."""
    result = runner.invoke(app, ["validate-schema", "--help"])
    assert result.exit_code == 0
    assert "Validate meaning fields" in result.output
    assert "--strict" in result.output
    assert "--cache-dir" in result.output


def test_validate_data_help(runner):
    """Test validate-data help."""
    result = runner.invoke(app, ["validate-data", "--help"])
    assert result.exit_code == 0
    assert "Validate data against dynamic enums" in result.output
    assert "--schema" in result.output
    assert "--labels" in result.output
    assert "--bindings" in result.output


def test_validate_schema_success(runner, examples_dir):
    """Test successful schema validation."""
    schema_path = examples_dir / "simple_schema.yaml"

    result = runner.invoke(app, ["validate-schema", str(schema_path), "--cache-dir", "cache"])

    # Should succeed - the simple schema has valid meanings
    assert result.exit_code == 0
    assert "✅" in result.output


def test_validate_schema_verbose(runner, examples_dir):
    """Test schema validation with verbose output."""
    schema_path = examples_dir / "simple_schema.yaml"

    result = runner.invoke(app, ["validate-schema", str(schema_path), "--verbose", "--cache-dir", "cache"])

    assert result.exit_code == 0
    assert "Enums checked:" in result.output
    assert "Values checked:" in result.output


def test_validate_schema_missing_file(runner):
    """Test schema validation with missing file."""
    result = runner.invoke(app, ["validate-schema", "nonexistent.yaml"])

    # Should fail with non-zero exit code
    assert result.exit_code != 0


def test_validate_schema_service_outage_reports_distinctly(
    runner, examples_dir, tmp_path, monkeypatch
):
    """A service outage exits with a distinct code and an "unable to validate"
    status rather than reporting every term as "not found"/invalid data."""
    import requests

    from linkml_term_validator.utils import oak_utils

    class DownAdapter:
        def label(self, curie):
            raise requests.exceptions.ConnectionError(
                "HTTPSConnectionPool(host='www.ebi.ac.uk', port=443): "
                "Max retries exceeded"
            )

    monkeypatch.setattr(oak_utils, "get_adapter", lambda s: DownAdapter())

    schema_path = examples_dir / "simple_schema.yaml"
    result = runner.invoke(
        app,
        [
            "validate-schema",
            str(schema_path),
            "--no-cache",
            "--cache-dir",
            str(tmp_path / "cache"),
        ],
    )

    # Distinct exit code (2), separate from validation-failure code 1.
    assert result.exit_code == 2, result.output
    assert "Unable to validate at this time" in result.output
    # Must NOT masquerade as a data error.
    assert "not found" not in result.output


def test_validate_data_service_outage_reports_distinctly(
    runner, tests_data_dir, tmp_path, monkeypatch
):
    """validate-data (the originally reported command) also fails fast with the
    distinct exit code 2, here exercising the HTTP 5xx outage path end-to-end."""
    import requests

    from linkml_term_validator.utils import oak_utils

    class ErroringAdapter:
        def label(self, curie):
            response = requests.Response()
            response.status_code = 503
            raise requests.exceptions.HTTPError(
                "503 Server Error: Service Unavailable", response=response
            )

    monkeypatch.setattr(oak_utils, "get_adapter", lambda s: ErroringAdapter())

    result = runner.invoke(
        app,
        [
            "validate-data",
            str(tests_data_dir / "dynamic_enum_valid_data.yaml"),
            "--schema",
            str(tests_data_dir / "dynamic_enum_schema.yaml"),
            "--target-class",
            "Sample",
            "--config",
            str(tests_data_dir / "test_oak_config.yaml"),
            "--no-bindings",
            "--no-cache",
            "--cache-dir",
            str(tmp_path / "cache"),
        ],
    )

    assert result.exit_code == 2, result.output
    assert "Unable to validate at this time" in result.output


def test_validate_data_inconsistent_reachability_reports_distinctly(
    runner, tests_data_dir, tmp_path, monkeypatch
):
    """validate-data fails with the distinct exit code 3 when an adapter's
    ancestor/descendant directions disagree by CURIE (the OLS4 MONDO defect
    shape), separate from invalid data (1) and a transient outage (2)."""
    from linkml_term_validator.utils import oak_utils

    class InconsistentAdapter:
        """descendants(root)=[child] but ancestors(child) omits the root by CURIE."""

        def label(self, curie):
            return f"term {curie}" if str(curie).startswith("TEST:") else None

        def descendants(self, curies, predicates=None):
            if "TEST:0000001" in list(curies):
                return iter(["TEST:0000002"])
            return iter(())

        def ancestors(self, curies, predicates=None):
            # Non-empty (reverse direction works) but omits the root — inconsistent.
            if "TEST:0000002" in list(curies):
                return iter(["OTHER:0000001"])
            return iter(())

    monkeypatch.setattr(oak_utils, "get_adapter", lambda s: InconsistentAdapter())

    # RootDescendantsEnum in this schema is reachable_from TEST:0000001; the data
    # cites TEST:0000002 (a real descendant the corrupted graph can't match by CURIE).
    data_path = tmp_path / "data.yaml"
    data_path.write_text("- id: s1\n  term: TEST:0000002\n")

    result = runner.invoke(
        app,
        [
            "validate-data",
            str(data_path),
            "--schema",
            str(tests_data_dir / "dynamic_enum_schema.yaml"),
            "--target-class",
            "Sample",
            "--config",
            str(tests_data_dir / "test_oak_config.yaml"),
            "--no-bindings",
            "--no-cache",
            "--cache-dir",
            str(tmp_path / "cache"),
        ],
    )

    assert result.exit_code == 3, result.output
    assert "reachability is unreliable" in result.output
    # A configuration/ontology-graph problem, not invalid data.
    assert "not invalid" in result.output


def test_offline_flag_in_help(runner):
    """The --offline flag is documented on the validation commands."""
    for command in ["validate-schema", "validate-data", "validate", "validate-text-file"]:
        result = runner.invoke(app, [command, "--help"])
        assert result.exit_code == 0
        assert "--offline" in result.output


def test_validate_schema_offline_uncached_is_error(runner, tests_data_dir, tmp_path):
    """Offline schema validation fails on uncached terms (no silent green pass)."""
    schema_path = tests_data_dir / "test_schema.yaml"
    cache_dir = tmp_path / "cache"  # empty → every term is a cache miss

    result = runner.invoke(
        app,
        ["validate-schema", str(schema_path), "--offline", "--cache-dir", str(cache_dir)],
    )

    assert result.exit_code == 1
    assert "offline cache" in result.output
    # The misleading "validation skipped" note must not appear in offline mode.
    assert "validation skipped" not in result.output


@pytest.mark.parametrize("strategy", ["progressive", "greedy"])
def test_validate_data_offline_empty_cache_is_error(runner, tests_data_dir, tmp_path, strategy):
    """Offline data validation fails (non-zero) with an empty cache, and the
    dynamic-enum diagnostic points at the unmaterialized closure, not the data.

    Covers both cache strategies: the greedy path must not silently pass or
    poison the cache when the closure was never materialized.
    """
    schema_path = tests_data_dir / "dynamic_enum_schema.yaml"
    data_path = tests_data_dir / "dynamic_enum_valid_data.yaml"
    config_path = tests_data_dir / "test_oak_config.yaml"
    cache_dir = tmp_path / "cache"  # empty → nothing is materialized

    result = runner.invoke(
        app,
        [
            "validate-data",
            str(data_path),
            "--schema",
            str(schema_path),
            "--target-class",
            "Sample",
            "--config",
            str(config_path),
            "--offline",
            "--no-bindings",
            "--cache-strategy",
            strategy,
            "--cache-dir",
            str(cache_dir),
        ],
    )

    assert result.exit_code == 1
    assert "not materialized" in result.output
    # The failed offline run must not have poisoned the cache: neither a bogus
    # ".complete" marker (the authoritative flag) nor a stray enum data file.
    assert not list(cache_dir.glob("enums/*.complete"))
    assert not list(cache_dir.glob("enums/*.csv"))


def test_validate_data_offline_uses_cache(runner, tests_data_dir, tmp_path):
    """Offline data validation passes when the enum cache is pre-populated."""
    schema_path = tests_data_dir / "dynamic_enum_schema.yaml"
    data_path = tests_data_dir / "dynamic_enum_valid_data.yaml"
    config_path = tests_data_dir / "test_oak_config.yaml"
    cache_dir = tmp_path / "cache"

    common = [
        str(data_path),
        "--schema",
        str(schema_path),
        "--target-class",
        "Sample",
        "--config",
        str(config_path),
        "--cache-dir",
        str(cache_dir),
        "--no-bindings",
    ]

    # 1. Populate a complete enum cache online.
    online = runner.invoke(app, ["validate-data", *common, "--saturate-enum-caches"])
    assert online.exit_code == 0, online.output

    # 2. Re-run offline against the populated cache: should still pass.
    offline = runner.invoke(app, ["validate-data", *common, "--offline"])
    assert offline.exit_code == 0, offline.output
    assert "✅" in offline.output


def test_validate_data_missing_schema(runner, examples_dir):
    """Test data validation without --schema flag."""
    data_path = examples_dir / "simple_data.yaml"

    result = runner.invoke(app, ["validate-data", str(data_path)])

    # Should fail - schema is required
    assert result.exit_code != 0
    # Typer will show the required option error
    assert "--schema" in result.output or "required" in result.output.lower()


def test_validate_data_with_schema(runner, examples_dir):
    """Test data validation with schema."""
    schema_path = examples_dir / "simple_schema.yaml"
    data_path = examples_dir / "simple_data.yaml"

    result = runner.invoke(
        app,
        ["validate-data", str(data_path), "--schema", str(schema_path), "--cache-dir", "cache"],
    )

    # Note: simple_schema.yaml has static enums (not dynamic), so DynamicEnumPlugin
    # won't catch INVALID_VALUE. This test shows successful plugin execution.
    # For actual enum validation, would need JsonschemaValidationPlugin.
    assert result.exit_code == 0
    assert "✅ Validation passed" in result.output


def test_validate_command_schema_mode(runner, examples_dir):
    """Test the 'validate' command in schema mode."""
    schema_path = examples_dir / "simple_schema.yaml"

    result = runner.invoke(app, ["validate", str(schema_path), "--cache-dir", "cache"])

    # Should succeed - validates schema
    assert result.exit_code == 0


def test_validate_command_data_mode(runner, examples_dir):
    """Test the 'validate' command in data mode."""
    schema_path = examples_dir / "simple_schema.yaml"
    data_path = examples_dir / "simple_data.yaml"

    result = runner.invoke(
        app,
        ["validate", str(data_path), "--schema", str(schema_path), "--cache-dir", "cache"],
    )

    # Passes because simple_schema.yaml has static enums (not dynamic)
    assert result.exit_code == 0


def test_validate_data_no_bindings(runner, examples_dir):
    """Test data validation with bindings disabled."""
    schema_path = examples_dir / "simple_schema.yaml"
    data_path = examples_dir / "simple_data.yaml"

    result = runner.invoke(
        app,
        [
            "validate-data",
            str(data_path),
            "--schema",
            str(schema_path),
            "--no-bindings",
            "--cache-dir",
            "cache",
        ],
    )

    # Passes because dynamic enums are enabled and there are no bindings to check
    assert result.exit_code == 0


def test_validate_data_no_dynamic_enums(runner, examples_dir):
    """Test data validation with dynamic enums disabled."""
    schema_path = examples_dir / "simple_schema.yaml"
    data_path = examples_dir / "simple_data.yaml"

    result = runner.invoke(
        app,
        [
            "validate-data",
            str(data_path),
            "--schema",
            str(schema_path),
            "--no-dynamic-enums",
            "--cache-dir",
            "cache",
        ],
    )

    # Passes because only bindings are checked (simple_schema has no bindings)
    assert result.exit_code == 0


def test_validate_data_help_shows_lenient(runner):
    """Test validate-data help shows --lenient option."""
    result = runner.invoke(app, ["validate-data", "--help"])
    assert result.exit_code == 0
    assert "--lenient" in result.output
    assert "lenient mode" in result.output.lower()
    assert "term ids are not" in result.output.lower()


def test_migrate_cache_refresh_labels(runner, tmp_path):
    """Test migrate-cache --refresh-labels doesn't crash (issue #17).

    Previously this crashed because BaseOntologyPlugin (abstract) was
    instantiated directly. The fix uses DynamicEnumPlugin instead.
    """
    # Create a fake cache directory with a terms.csv
    prefix_dir = tmp_path / "test"
    prefix_dir.mkdir()
    terms_file = prefix_dir / "terms.csv"
    terms_file.write_text(
        "curie,label,retrieved_at\n"
        "TEST:0000001,old label,2024-01-01T00:00:00\n"
        "TEST:0000002,child term one,2024-01-01T00:00:00\n"
    )

    config_path = Path(__file__).parent / "data" / "test_oak_config.yaml"

    result = runner.invoke(
        app,
        [
            "migrate-cache",
            "--cache-dir",
            str(tmp_path),
            "--refresh-labels",
            "--config",
            str(config_path),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "Migration preview" in result.output


# ---------------------------------------------------------------------------
# validate-text-file tests
# ---------------------------------------------------------------------------


def test_validate_text_file_help(runner):
    """Test that validate-text-file help text is correct."""
    result = runner.invoke(app, ["validate-text-file", "--help"])
    assert result.exit_code == 0
    assert "--regex" in result.output
    assert "--curie-group" in result.output
    assert "--label-group" in result.output
    assert "--strict" in result.output
    assert "--config" in result.output


def test_validate_text_file_valid_terms(runner, tmp_path, tests_data_dir):
    """Test validate-text-file with valid CURIEs and matching labels."""
    text_file = tmp_path / "doc.md"
    text_file.write_text(
        '- @term TEST:0000001 "root term"\n'
        '- @term TEST:0000002 "child term one"\n'
        '- @term TEST:0000003 "child term two"\n'
    )

    result = runner.invoke(
        app,
        [
            "validate-text-file",
            str(text_file),
            "--config",
            str(tests_data_dir / "test_oak_config.yaml"),
            "--no-cache",
        ],
    )

    assert result.exit_code == 0
    assert "✅" in result.output
    assert "3 CURIE(s)" in result.output


def test_validate_text_file_label_mismatch(runner, tmp_path, tests_data_dir):
    """Test validate-text-file reports error on label mismatch."""
    text_file = tmp_path / "doc.md"
    text_file.write_text('- @term TEST:0000001 "wrong label"\n')

    result = runner.invoke(
        app,
        [
            "validate-text-file",
            str(text_file),
            "--config",
            str(tests_data_dir / "test_oak_config.yaml"),
            "--no-cache",
        ],
    )

    assert result.exit_code == 1
    assert "mismatch" in result.output.lower()
    assert "TEST:0000001" in result.output


def test_validate_text_file_unresolvable_configured_prefix(runner, tmp_path, tests_data_dir):
    """Test that an unresolvable CURIE with a configured prefix is always an error."""
    text_file = tmp_path / "doc.md"
    # TEST:9999999 does not exist in the test ontology, but TEST is configured
    text_file.write_text('- @term TEST:9999999 "nonexistent term"\n')

    result = runner.invoke(
        app,
        [
            "validate-text-file",
            str(text_file),
            "--config",
            str(tests_data_dir / "test_oak_config.yaml"),
            "--no-cache",
        ],
    )

    assert result.exit_code == 1
    assert "TEST:9999999" in result.output


def test_validate_text_file_unresolvable_unconfigured_no_strict(runner, tmp_path, tests_data_dir):
    """Test that an unresolvable CURIE with an unconfigured prefix passes without --strict."""
    text_file = tmp_path / "doc.md"
    # UNKNOWN prefix is not in test_oak_config.yaml
    text_file.write_text('- @term UNKNOWN:9999999 "something"\n')

    result = runner.invoke(
        app,
        [
            "validate-text-file",
            str(text_file),
            "--config",
            str(tests_data_dir / "test_oak_config.yaml"),
            "--no-cache",
        ],
    )

    # Without --strict, unconfigured prefix is silently skipped
    assert result.exit_code == 0


def test_validate_text_file_unresolvable_unconfigured_strict(runner, tmp_path, tests_data_dir):
    """Test that --strict turns unresolvable unconfigured CURIEs into errors."""
    text_file = tmp_path / "doc.md"
    text_file.write_text('- @term UNKNOWN:9999999 "something"\n')

    result = runner.invoke(
        app,
        [
            "validate-text-file",
            str(text_file),
            "--config",
            str(tests_data_dir / "test_oak_config.yaml"),
            "--strict",
            "--no-cache",
        ],
    )

    assert result.exit_code == 1
    assert "UNKNOWN:9999999" in result.output


def test_validate_text_file_custom_regex(runner, tmp_path, tests_data_dir):
    """Test validate-text-file with a custom regex and group indices."""
    text_file = tmp_path / "doc.md"
    # Custom format: {label}={CURIE}
    text_file.write_text('root term=TEST:0000001\nchild term one=TEST:0000002\n')

    result = runner.invoke(
        app,
        [
            "validate-text-file",
            str(text_file),
            "--regex",
            r"([^=]+)=(TEST:\d+)",
            "--curie-group",
            "2",
            "--label-group",
            "1",
            "--config",
            str(tests_data_dir / "test_oak_config.yaml"),
            "--no-cache",
        ],
    )

    assert result.exit_code == 0
    assert "✅" in result.output


def test_validate_text_file_no_matches(runner, tmp_path, tests_data_dir):
    """Test validate-text-file warns when the regex finds no matches."""
    text_file = tmp_path / "doc.md"
    text_file.write_text("# A document with no term annotations\n")

    result = runner.invoke(
        app,
        [
            "validate-text-file",
            str(text_file),
            "--config",
            str(tests_data_dir / "test_oak_config.yaml"),
            "--no-cache",
        ],
    )

    # No matches → exit 0 with a warning message
    assert result.exit_code == 0
    assert "No matches" in result.output


def test_validate_text_file_invalid_regex(runner, tmp_path):
    """Test validate-text-file exits with error on invalid regex."""
    text_file = tmp_path / "doc.md"
    text_file.write_text("some content\n")

    result = runner.invoke(
        app,
        [
            "validate-text-file",
            str(text_file),
            "--regex",
            r"([invalid",
        ],
    )

    assert result.exit_code == 1
    assert "Invalid regex" in result.output or "invalid regex" in result.output.lower()


def test_validate_text_file_verbose(runner, tmp_path, tests_data_dir):
    """Test validate-text-file verbose output shows each CURIE."""
    text_file = tmp_path / "doc.md"
    text_file.write_text(
        '- @term TEST:0000001 "root term"\n'
        '- @term TEST:0000002 "child term one"\n'
    )

    result = runner.invoke(
        app,
        [
            "validate-text-file",
            str(text_file),
            "--config",
            str(tests_data_dir / "test_oak_config.yaml"),
            "--no-cache",
            "--verbose",
        ],
    )

    assert result.exit_code == 0
    assert "TEST:0000001" in result.output
    assert "TEST:0000002" in result.output


# ---------------------------------------------------------------------------
# --strict fix tests for validate-schema
# ---------------------------------------------------------------------------


def test_validate_schema_strict_unresolvable_unconfigured(runner, tmp_path, tests_data_dir):
    """Test that --strict turns unconfigured-prefix unresolvable CURIEs into schema errors."""
    # Build a minimal schema with a CURIE whose prefix is NOT in oak_config
    schema_yaml = tmp_path / "schema.yaml"
    schema_yaml.write_text(
        "id: https://example.org/test\n"
        "name: test\n"
        "prefixes:\n"
        "  linkml: https://w3id.org/linkml/\n"
        "  UNKNOWN: http://example.org/UNKNOWN/\n"
        "imports:\n"
        "  - linkml:types\n"
        "enums:\n"
        "  TestEnum:\n"
        "    permissible_values:\n"
        "      some_value:\n"
        "        meaning: UNKNOWN:9999999\n"
    )

    result = runner.invoke(
        app,
        [
            "validate-schema",
            str(schema_yaml),
            "--config",
            str(tests_data_dir / "test_oak_config.yaml"),
            "--strict",
            "--no-cache",
        ],
    )

    # With --strict, unresolvable unconfigured CURIE → exit 1
    assert result.exit_code == 1
