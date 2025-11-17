"""Tests for dynamic enum validation using local OBO files with simpleobo adapter."""

from pathlib import Path

import pytest
from linkml.validator import Validator  # type: ignore[import-untyped]
from linkml.validator.loaders import YamlLoader  # type: ignore[import-untyped]

from linkml_term_validator.plugins import DynamicEnumPlugin


@pytest.fixture
def test_data_dir():
    """Get the test data directory."""
    return Path(__file__).parent / "data"


@pytest.fixture
def oak_config(test_data_dir):
    """Path to OAK config for tests."""
    return test_data_dir / "test_oak_config.yaml"


@pytest.fixture
def dynamic_enum_schema(test_data_dir):
    """Path to schema with dynamic enums."""
    return test_data_dir / "dynamic_enum_schema.yaml"


@pytest.fixture
def valid_data(test_data_dir):
    """Path to valid test data."""
    return test_data_dir / "dynamic_enum_valid_data.yaml"


@pytest.fixture
def invalid_data(test_data_dir):
    """Path to invalid test data."""
    return test_data_dir / "dynamic_enum_invalid_data.yaml"


def test_dynamic_enum_plugin_with_simpleobo(dynamic_enum_schema, valid_data, oak_config):
    """Test DynamicEnumPlugin with simpleobo adapter and valid data."""
    # Create plugin with oak_config to use local test ontology
    plugin = DynamicEnumPlugin(oak_config_path=oak_config)

    # Create validator
    validator = Validator(
        schema=str(dynamic_enum_schema),
        validation_plugins=[plugin],
    )

    # Load and validate
    loader = YamlLoader(valid_data)
    report = validator.validate_source(loader, target_class="Sample")

    # Should pass - all terms are valid
    assert len(report.results) == 0, f"Expected valid data to pass, got: {report.results}"


def test_dynamic_enum_plugin_detects_invalid_terms(dynamic_enum_schema, invalid_data, oak_config):
    """Test DynamicEnumPlugin detects invalid terms in dynamic enums."""
    plugin = DynamicEnumPlugin(oak_config_path=oak_config)

    validator = Validator(
        schema=str(dynamic_enum_schema),
        validation_plugins=[plugin],
    )

    loader = YamlLoader(invalid_data)
    report = validator.validate_source(loader, target_class="Sample")

    # Should fail - has invalid terms
    assert len(report.results) > 0, "Expected invalid data to fail validation"

    # Check that we got error messages about invalid terms
    messages = [r.message for r in report.results]
    assert any("not in dynamic enum" in msg or "not found" in msg.lower() for msg in messages)


def test_dynamic_enum_reachable_from_validation(dynamic_enum_schema, test_data_dir, oak_config):
    """Test that reachable_from constraints are properly enforced."""
    plugin = DynamicEnumPlugin(oak_config_path=oak_config)

    validator = Validator(
        schema=str(dynamic_enum_schema),
        validation_plugins=[plugin],
    )

    # Create data with term outside the reachable_from constraint
    test_data = Path(__file__).parent / "data" / "test_reachable_from.yaml"
    test_data.write_text("""
- id: test1
  process_type: TEST:0000002  # child term one - NOT under biological_process
""")

    try:
        loader = YamlLoader(test_data)
        report = validator.validate_source(loader, target_class="Sample")

        # Should fail because TEST:0000002 is not reachable from TEST:0000005
        assert len(report.results) > 0
    finally:
        # Clean up
        if test_data.exists():
            test_data.unlink()


def test_simpleobo_adapter_directly():
    """Test that simpleobo adapter works correctly with test ontology."""
    from oaklib import get_adapter

    obo_path = Path(__file__).parent / "data" / "test_ontology.obo"
    adapter = get_adapter(f"simpleobo:{obo_path}")

    # Test basic label retrieval
    assert adapter.label("TEST:0000001") == "root term"
    assert adapter.label("TEST:0000005") == "biological_process"
    assert adapter.label("TEST:0000006") == "cell_cycle"

    # Test relationship traversal (get descendants)
    descendants = list(adapter.descendants("TEST:0000005"))
    assert "TEST:0000006" in descendants  # cell_cycle is descendant of biological_process


def test_dynamic_enum_with_multiple_source_nodes(test_data_dir, oak_config):
    """Test dynamic enum with multiple source nodes in reachable_from."""
    # Create schema with multiple source nodes
    schema_path = test_data_dir / "test_multi_source.yaml"
    schema_path.write_text("""
id: https://example.org/test-multi-source
name: test-multi-source

prefixes:
  TEST: http://example.org/TEST_
  linkml: https://w3id.org/linkml/

default_prefix: test-multi-source
default_range: string

classes:
  Sample:
    attributes:
      id:
        identifier: true
      term:
        range: MultiSourceEnum

enums:
  MultiSourceEnum:
    description: Terms reachable from multiple sources
    reachable_from:
      source_ontology: simpleobo:tests/data/test_ontology.obo
      source_nodes:
        - TEST:0000002  # child term one
        - TEST:0000003  # child term two
      relationship_types:
        - rdfs:subClassOf
""")

    data_path = test_data_dir / "test_multi_source_data.yaml"
    data_path.write_text("""
# Only use descendants of the source nodes, not the source nodes themselves
- id: s1
  term: TEST:0000004  # grandchild term (descendant of TEST:0000002)
""")

    try:
        plugin = DynamicEnumPlugin(oak_config_path=oak_config)
        validator = Validator(
            schema=str(schema_path),
            validation_plugins=[plugin],
        )

        loader = YamlLoader(data_path)
        report = validator.validate_source(loader, target_class="Sample")

        # Should pass - all terms are reachable from one of the source nodes
        assert len(report.results) == 0, f"Multi-source validation failed: {report.results}"
    finally:
        # Clean up
        for f in [schema_path, data_path]:
            if f.exists():
                f.unlink()


def test_dynamic_enum_plugin_with_cache(dynamic_enum_schema, valid_data, oak_config, tmp_path):
    """Test that DynamicEnumPlugin works repeatedly (tests label caching)."""
    cache_dir = tmp_path / "test_cache"

    # First validation
    plugin1 = DynamicEnumPlugin(cache_dir=cache_dir, oak_config_path=oak_config)
    validator1 = Validator(
        schema=str(dynamic_enum_schema),
        validation_plugins=[plugin1],
    )

    loader1 = YamlLoader(valid_data)
    report1 = validator1.validate_source(loader1, target_class="Sample")
    assert len(report1.results) == 0

    # Second validation with a new plugin instance
    plugin2 = DynamicEnumPlugin(cache_dir=cache_dir, oak_config_path=oak_config)
    validator2 = Validator(
        schema=str(dynamic_enum_schema),
        validation_plugins=[plugin2],
    )

    loader2 = YamlLoader(valid_data)
    report2 = validator2.validate_source(loader2, target_class="Sample")
    assert len(report2.results) == 0, "Second validation should also pass"
