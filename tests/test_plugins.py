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


def test_binding_plugin_finds_label_slots_with_implements(plugin_cache_dir, tmp_path):
    """Test that BindingValidationPlugin detects label slots via implements."""
    from linkml.validator.validation_context import ValidationContext  # type: ignore[import-untyped]
    from linkml_runtime import SchemaView

    # Create schema with implements: [rdfs:label] on name slot
    schema_path = tmp_path / "implements_schema.yaml"
    schema_path.write_text("""
id: https://example.org/test
name: test

prefixes:
  rdfs: http://www.w3.org/2000/01/rdf-schema#
  linkml: https://w3id.org/linkml/
  TEST: http://example.org/TEST_

default_prefix: test
default_range: string

classes:
  OntologyTerm:
    attributes:
      id:
        identifier: true
      name:
        implements:
          - rdfs:label
      title:
        description: Display title (not a label)
""")

    plugin = BindingValidationPlugin(
        validate_labels=True,
        cache_dir=plugin_cache_dir,
    )

    # Load schema and create validation context
    schema_view = SchemaView(str(schema_path))
    context = ValidationContext(
        schema=schema_view.schema,
        target_class="OntologyTerm",
    )

    # Run pre_process to collect implements info
    plugin.pre_process(context)

    # Check that 'name' is found as a label slot (via implements)
    label_slots = plugin._find_label_slots("OntologyTerm")
    assert "name" in label_slots
    # 'title' should NOT be in label slots (no implements)
    assert "title" not in label_slots


def test_binding_plugin_falls_back_to_label_convention(plugin_cache_dir, tmp_path):
    """Test that BindingValidationPlugin falls back to 'label' field if no implements."""
    from linkml.validator.validation_context import ValidationContext  # type: ignore[import-untyped]
    from linkml_runtime import SchemaView

    # Create schema WITHOUT implements
    schema_path = tmp_path / "no_implements_schema.yaml"
    schema_path.write_text("""
id: https://example.org/test
name: test

prefixes:
  linkml: https://w3id.org/linkml/

default_prefix: test
default_range: string

classes:
  SimpleTerm:
    attributes:
      id:
        identifier: true
      label:
        description: Term label
""")

    plugin = BindingValidationPlugin(
        validate_labels=True,
        cache_dir=plugin_cache_dir,
    )

    schema_view = SchemaView(str(schema_path))
    context = ValidationContext(
        schema=schema_view.schema,
        target_class="SimpleTerm",
    )

    plugin.pre_process(context)

    # Should fall back to convention: 'label'
    label_slots = plugin._find_label_slots("SimpleTerm")
    assert label_slots == ["label"]


def test_binding_plugin_finds_label_slots_with_slot_uri(plugin_cache_dir, tmp_path):
    """Test that BindingValidationPlugin detects label slots via slot_uri."""
    from linkml.validator.validation_context import ValidationContext  # type: ignore[import-untyped]
    from linkml_runtime import SchemaView

    # Create schema with slot_uri: rdfs:label on name slot
    schema_path = tmp_path / "slot_uri_schema.yaml"
    schema_path.write_text("""
id: https://example.org/test
name: test

prefixes:
  rdfs: http://www.w3.org/2000/01/rdf-schema#
  linkml: https://w3id.org/linkml/
  TEST: http://example.org/TEST_

default_prefix: test
default_range: string

classes:
  OntologyTerm:
    attributes:
      id:
        identifier: true
      name:
        slot_uri: rdfs:label
      title:
        description: Display title (not a label)
""")

    plugin = BindingValidationPlugin(
        validate_labels=True,
        cache_dir=plugin_cache_dir,
    )

    # Load schema and create validation context
    schema_view = SchemaView(str(schema_path))
    context = ValidationContext(
        schema=schema_view.schema,
        target_class="OntologyTerm",
    )

    # Run pre_process to collect slot properties
    plugin.pre_process(context)

    # Check that 'name' is found as a label slot (via slot_uri)
    label_slots = plugin._find_label_slots("OntologyTerm")
    assert "name" in label_slots
    # 'title' should NOT be in label slots (no slot_uri or implements)
    assert "title" not in label_slots


def test_binding_plugin_validates_nested_bindings(plugin_cache_dir, tmp_path):
    """Test that BindingValidationPlugin recurses into nested structures.

    This tests the fix for the bug where bindings on nested/inlined objects
    were not being validated.
    """
    from linkml.validator.validation_context import ValidationContext  # type: ignore[import-untyped]
    from linkml_runtime import SchemaView

    # Create schema with nested bindings (simplified from bug report)
    schema_path = tmp_path / "nested_bindings_schema.yaml"
    schema_path.write_text("""
id: https://example.org/test
name: test_nested

prefixes:
  linkml: https://w3id.org/linkml/
  TEST: http://example.org/TEST_

default_prefix: test_nested
default_range: string

classes:
  Disease:
    attributes:
      name:
        identifier: true
      disease_term:
        range: DiseaseDescriptor
        inlined: true

  DiseaseDescriptor:
    attributes:
      preferred_term:
        required: true
      term:
        range: Term
        inlined: true
        bindings:
          - binds_value_of: id
            range: DiseaseTermEnum

  Term:
    attributes:
      id:
        description: CURIE for the term
      label:
        description: Human-readable label

enums:
  DiseaseTermEnum:
    permissible_values:
      DISEASE_A:
        meaning: TEST:0001
      DISEASE_B:
        meaning: TEST:0002
""")

    plugin = BindingValidationPlugin(
        validate_labels=False,
        cache_dir=plugin_cache_dir,
    )

    schema_view = SchemaView(str(schema_path))
    context = ValidationContext(
        schema=schema_view.schema,
        target_class="Disease",
    )

    plugin.pre_process(context)

    # Test with valid nested term
    valid_instance = {
        "name": "Test Disease",
        "disease_term": {
            "preferred_term": "Disease A",
            "term": {
                "id": "TEST:0001",
                "label": "Disease A Label",
            }
        }
    }
    results = list(plugin.process(valid_instance, context))
    assert len(results) == 0, f"Expected no errors, got: {results}"

    # Test with INVALID nested term (id not in enum)
    invalid_instance = {
        "name": "Test Disease",
        "disease_term": {
            "preferred_term": "Invalid Disease",
            "term": {
                "id": "TEST:9999",  # Not in enum!
                "label": "Invalid",
            }
        }
    }
    results = list(plugin.process(invalid_instance, context))
    assert len(results) == 1, f"Expected 1 error, got: {results}"
    assert "TEST:9999" in results[0].message
    assert "DiseaseTermEnum" in results[0].message
    # Check that path is included
    assert any("disease_term.term" in ctx for ctx in results[0].context)


def test_binding_plugin_validates_deeply_nested_bindings(plugin_cache_dir, tmp_path):
    """Test that BindingValidationPlugin handles deeply nested structures."""
    from linkml.validator.validation_context import ValidationContext  # type: ignore[import-untyped]
    from linkml_runtime import SchemaView

    # Create schema with deeply nested bindings
    schema_path = tmp_path / "deep_nested_schema.yaml"
    schema_path.write_text("""
id: https://example.org/test
name: test_deep

prefixes:
  linkml: https://w3id.org/linkml/
  TEST: http://example.org/TEST_

default_prefix: test_deep
default_range: string

classes:
  Document:
    attributes:
      title:
        identifier: true
      sections:
        range: Section
        multivalued: true
        inlined_as_list: true

  Section:
    attributes:
      heading:
        required: true
      annotations:
        range: Annotation
        multivalued: true
        inlined_as_list: true

  Annotation:
    attributes:
      text:
        required: true
      term:
        range: Term
        inlined: true
        bindings:
          - binds_value_of: id
            range: AnnotationTermEnum

  Term:
    attributes:
      id:
        description: CURIE
      label:
        description: Label

enums:
  AnnotationTermEnum:
    permissible_values:
      TERM_A:
        meaning: TEST:A001
      TERM_B:
        meaning: TEST:B002
""")

    plugin = BindingValidationPlugin(
        validate_labels=False,
        cache_dir=plugin_cache_dir,
    )

    schema_view = SchemaView(str(schema_path))
    context = ValidationContext(
        schema=schema_view.schema,
        target_class="Document",
    )

    plugin.pre_process(context)

    # Test with valid deeply nested term
    valid_instance = {
        "title": "Test Document",
        "sections": [
            {
                "heading": "Section 1",
                "annotations": [
                    {
                        "text": "Annotation text",
                        "term": {
                            "id": "TEST:A001",
                            "label": "Term A",
                        }
                    }
                ]
            }
        ]
    }
    results = list(plugin.process(valid_instance, context))
    assert len(results) == 0, f"Expected no errors, got: {results}"

    # Test with INVALID deeply nested term
    invalid_instance = {
        "title": "Test Document",
        "sections": [
            {
                "heading": "Section 1",
                "annotations": [
                    {
                        "text": "Valid annotation",
                        "term": {"id": "TEST:A001", "label": "Term A"}
                    },
                    {
                        "text": "Invalid annotation",
                        "term": {"id": "TEST:INVALID", "label": "Bad"}  # Invalid!
                    }
                ]
            },
            {
                "heading": "Section 2",
                "annotations": [
                    {
                        "text": "Another invalid",
                        "term": {"id": "TEST:ALSO_INVALID", "label": "Also Bad"}
                    }
                ]
            }
        ]
    }
    results = list(plugin.process(invalid_instance, context))
    assert len(results) == 2, f"Expected 2 errors, got: {results}"

    # Check paths are correct
    paths = [ctx for r in results for ctx in r.context if ctx.startswith("path:")]
    assert "path: sections[0].annotations[1].term" in paths
    assert "path: sections[1].annotations[0].term" in paths
