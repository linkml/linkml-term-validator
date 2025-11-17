"""Tests for integration with linkml-validate CLI."""

import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def examples_dir():
    """Get the examples directory."""
    return Path(__file__).parent.parent / "examples"


def test_linkml_validate_basic(examples_dir):
    """Test that linkml-validate works with basic config (no ontology plugins)."""
    config_path = examples_dir / "simple_config.yaml"

    # Run linkml-validate
    result = subprocess.run(
        ["uv", "run", "linkml-validate", "--config", str(config_path)],
        capture_output=True,
        text=True,
    )

    # Should fail because INVALID_VALUE is not in the enum
    assert result.returncode == 1
    assert "INVALID_VALUE" in result.stdout
    assert "is not one of" in result.stdout


def test_linkml_validate_with_simple_schema(examples_dir):
    """Test linkml-validate with simple schema directly."""
    schema_path = examples_dir / "simple_schema.yaml"
    data_path = examples_dir / "simple_data.yaml"

    # Run linkml-validate
    result = subprocess.run(
        [
            "uv",
            "run",
            "linkml-validate",
            "-s",
            str(schema_path),
            "-C",
            "Person",
            str(data_path),
        ],
        capture_output=True,
        text=True,
    )

    # Should fail because INVALID_VALUE is not in the enum
    assert result.returncode == 1
    assert "INVALID_VALUE" in result.stdout


def test_linkml_validate_help():
    """Test that linkml-validate is available."""
    result = subprocess.run(
        ["uv", "run", "linkml-validate", "--help"], capture_output=True, text=True
    )

    assert result.returncode == 0
    assert "Validate data according to a LinkML Schema" in result.stdout
    assert "--config" in result.stdout
