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
