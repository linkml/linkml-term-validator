"""Tests for validation plugins."""


import pytest
from linkml.validator import Validator  # type: ignore[import-untyped]

from linkml_term_validator.plugins import (
    BindingValidationPlugin,
    DynamicEnumPlugin,
    PermissibleValueMeaningPlugin,
)


@pytest.fixture
def plugin_cache_dir(tmp_path):
    """Create a temporary cache directory for plugins."""
    cache_dir = tmp_path / "plugin_cache"
    cache_dir.mkdir()
    return cache_dir


def test_permissible_value_plugin_init(plugin_cache_dir):
    """Test that PermissibleValueMeaningPlugin can be instantiated."""
    plugin = PermissibleValueMeaningPlugin(
        oak_adapter_string="sqlite:obo:",
        cache_labels=True,
        cache_dir=plugin_cache_dir,
    )
    assert plugin is not None
    assert plugin.config.oak_adapter_string == "sqlite:obo:"
    assert plugin.config.cache_labels is True


def test_dynamic_enum_plugin_init(plugin_cache_dir):
    """Test that DynamicEnumPlugin can be instantiated."""
    plugin = DynamicEnumPlugin(
        oak_adapter_string="sqlite:obo:",
        cache_labels=True,
        cache_dir=plugin_cache_dir,
    )
    assert plugin is not None
    assert plugin.expanded_enums == {}


def test_binding_plugin_init(plugin_cache_dir):
    """Test that BindingValidationPlugin can be instantiated."""
    plugin = BindingValidationPlugin(
        oak_adapter_string="sqlite:obo:",
        validate_labels=True,
        cache_labels=True,
        cache_dir=plugin_cache_dir,
    )
    assert plugin is not None
    assert plugin.validate_labels is True


@pytest.mark.integration
def test_permissible_value_plugin_with_linkml_validator(test_schema_path, plugin_cache_dir):
    """Test PermissibleValueMeaningPlugin integrated with LinkML Validator.

    This integration test verifies that the plugin works with LinkML's validator framework.

    WILL FAIL if OBO databases (GO, CHEBI) are not installed.
    """
    # Create plugin
    plugin = PermissibleValueMeaningPlugin(
        oak_adapter_string="sqlite:obo:",
        cache_labels=True,
        cache_dir=plugin_cache_dir,
    )

    # Create LinkML validator with our plugin
    validator = Validator(
        schema=str(test_schema_path),
        validation_plugins=[plugin],
    )

    # Validate the schema (the schema file itself is the data being validated)
    # For schema validation, we pass the schema path as data
    report = validator.validate(test_schema_path)

    # The test schema should validate successfully
    # (it has correct meanings and labels)
    assert report is not None


def test_plugin_base_functionality(plugin_cache_dir):
    """Test base plugin functionality (OAK adapter, caching)."""
    plugin = PermissibleValueMeaningPlugin(
        oak_adapter_string="sqlite:obo:",
        cache_labels=False,  # Disable caching for this test
        cache_dir=plugin_cache_dir,
    )

    # Test prefix extraction
    assert plugin._get_prefix("GO:0008150") == "GO"
    assert plugin._get_prefix("CHEBI:15377") == "CHEBI"
    assert plugin._get_prefix("invalid") is None

    # Test string normalization
    assert plugin.normalize_string("Hello, World!") == "hello world"
    assert plugin.normalize_string("T-Cell Receptor") == "t cell receptor"


def test_plugin_unknown_prefix_tracking(plugin_cache_dir, tmp_path):
    """Test that plugins track unknown prefixes."""
    # Create an oak_config that explicitly lists known ontologies
    # This prevents the default sqlite:obo: from trying to download unknown prefixes
    oak_config = tmp_path / "oak_config.yaml"
    oak_config.write_text("""ontology_adapters:
  GO: sqlite:obo:go
  CHEBI: sqlite:obo:chebi
""")

    plugin = PermissibleValueMeaningPlugin(
        oak_config_path=oak_config,
        cache_labels=False,
        cache_dir=plugin_cache_dir,
    )

    # Try to get a label for a prefix not in oak_config
    # This should track it as unknown
    _ = plugin.get_ontology_label("NOTCONFIGURED:12345")

    # Should be tracked as unknown
    unknown = plugin.get_unknown_prefixes()
    assert "NOTCONFIGURED" in unknown
