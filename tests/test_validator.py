"""Tests for the EnumValidator class."""

from pathlib import Path

import pytest

from linkml_term_validator.models import SeverityLevel, ValidationConfig
from linkml_term_validator.validator import EnumValidator


@pytest.fixture
def validator():
    """Create a validator with caching disabled for tests."""
    config = ValidationConfig(cache_labels=False)
    return EnumValidator(config)


def test_normalize_string():
    """Test string normalization."""
    assert EnumValidator.normalize_string("Hello, World!") == "hello world"
    assert EnumValidator.normalize_string("T-Cell Receptor") == "t cell receptor"
    assert EnumValidator.normalize_string("GO:0008150") == "go 0008150"
    assert EnumValidator.normalize_string("Multi  Spaces") == "multi spaces"


def test_get_prefix():
    """Test CURIE prefix extraction."""
    validator = EnumValidator(ValidationConfig())
    assert validator._get_prefix("GO:0008150") == "GO"
    assert validator._get_prefix("CHEBI:12345") == "CHEBI"
    assert validator._get_prefix("invalid") is None
    assert validator._get_prefix("") is None


def test_validation_config_defaults():
    """Test default configuration values."""
    config = ValidationConfig()
    assert config.oak_adapter_string == "sqlite:obo:"
    assert config.strict_mode is False
    assert config.cache_labels is True
    assert config.cache_dir == Path("cache")
    assert config.oak_config_path is None


def test_validation_config_custom():
    """Test custom configuration values."""
    config = ValidationConfig(
        oak_adapter_string="ols:",
        strict_mode=True,
        cache_labels=False,
        cache_dir=Path("custom_cache"),
    )
    assert config.oak_adapter_string == "ols:"
    assert config.strict_mode is True
    assert config.cache_labels is False
    assert config.cache_dir == Path("custom_cache")


def test_validator_initialization():
    """Test validator initialization."""
    config = ValidationConfig(cache_labels=False)
    validator = EnumValidator(config)
    assert validator.config == config
    assert len(validator._label_cache) == 0
    assert len(validator._adapter_cache) == 0


def test_extract_aliases():
    """Test alias extraction from permissible values."""
    from linkml_runtime.linkml_model import PermissibleValue

    validator = EnumValidator(ValidationConfig())

    pv = PermissibleValue(
        text="EXAMPLE", title="Example Term", description="An example"
    )
    aliases = validator.extract_aliases(pv, "EXAMPLE")

    assert "example" in aliases
    assert "example term" in aliases
    assert "an example" in aliases


def test_validate_schema_structure(test_schema_path, validator):
    """Test that validation runs on a schema."""
    result = validator.validate_schema(test_schema_path)

    assert result.schema_path == test_schema_path
    assert result.total_enums_checked == 3
    assert result.total_values_checked == 6
    assert result.total_meanings_checked == 4
    assert isinstance(result.issues, list)


def test_validation_result_methods():
    """Test ValidationResult helper methods."""
    from linkml_term_validator.models import ValidationIssue, ValidationResult

    result = ValidationResult(schema_path=Path("test.yaml"))

    assert not result.has_errors()
    assert not result.has_warnings()
    assert result.error_count() == 0
    assert result.warning_count() == 0

    result.issues.append(
        ValidationIssue(
            enum_name="TestEnum",
            value_name="VALUE",
            severity=SeverityLevel.ERROR,
            message="Test error",
        )
    )

    assert result.has_errors()
    assert result.error_count() == 1

    result.issues.append(
        ValidationIssue(
            enum_name="TestEnum",
            value_name="VALUE2",
            severity=SeverityLevel.WARNING,
            message="Test warning",
        )
    )

    assert result.has_warnings()
    assert result.warning_count() == 1


def test_validation_issue_methods():
    """Test ValidationIssue helper methods."""
    from linkml_term_validator.models import ValidationIssue

    error = ValidationIssue(
        enum_name="Test",
        value_name="VAL",
        severity=SeverityLevel.ERROR,
        message="test",
    )
    assert error.is_error()
    assert not error.is_warning()

    warning = ValidationIssue(
        enum_name="Test",
        value_name="VAL",
        severity=SeverityLevel.WARNING,
        message="test",
    )
    assert warning.is_warning()
    assert not warning.is_error()


def test_cache_file_path():
    """Test cache file path generation."""
    validator = EnumValidator(ValidationConfig(cache_dir=Path("test_cache")))
    cache_file = validator._get_cache_file("GO")
    assert cache_file == Path("test_cache/go/terms.csv")


def test_unknown_prefixes_tracking():
    """Test tracking of unknown prefixes."""
    validator = EnumValidator(ValidationConfig(cache_labels=False))
    assert len(validator.get_unknown_prefixes()) == 0
