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
