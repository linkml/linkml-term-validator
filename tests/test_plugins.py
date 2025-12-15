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


def test_binding_plugin_strict_mode_fails_on_nonexistent_term(plugin_cache_dir, tmp_path):
    """Test that strict mode (default) fails when term ID not found in configured ontology."""
    from linkml.validator.validation_context import ValidationContext  # type: ignore[import-untyped]
    from linkml_runtime import SchemaView
    from pathlib import Path

    # Use test oak_config that maps TEST prefix
    oak_config_path = Path(__file__).parent / "data" / "test_oak_config.yaml"

    schema_path = tmp_path / "strict_test_schema.yaml"
    schema_path.write_text("""
id: https://example.org/test
name: test_strict

prefixes:
  linkml: https://w3id.org/linkml/
  TEST: http://example.org/TEST_

default_prefix: test_strict
default_range: string

classes:
  Annotation:
    attributes:
      id:
        identifier: true
      term:
        range: Term
        inlined: true
        bindings:
          - binds_value_of: id
            range: TestEnum

  Term:
    attributes:
      id:
        description: CURIE
      label:
        description: Label

enums:
  TestEnum:
    reachable_from:
      source_ontology: simpleobo:tests/data/test_ontology.obo
      source_nodes:
        - TEST:0000001
      include_self: true
""")

    # Strict mode (default)
    plugin = BindingValidationPlugin(
        validate_labels=False,
        strict=True,
        cache_dir=plugin_cache_dir,
        oak_config_path=oak_config_path,
    )

    schema_view = SchemaView(str(schema_path))
    context = ValidationContext(
        schema=schema_view.schema,
        target_class="Annotation",
    )
    plugin.pre_process(context)

    # Test with NON-EXISTENT term ID (strict should fail)
    instance_nonexistent = {
        "id": "ann1",
        "term": {
            "id": "TEST:ZZZZZZZ",  # This ID does not exist in ontology
            "label": "Fake Term",
        }
    }
    results = list(plugin.process(instance_nonexistent, context))
    assert len(results) >= 1, f"Expected at least 1 error for non-existent term, got: {results}"
    assert any("not found in ontology" in r.message for r in results), f"Expected 'not found' error, got: {results}"
    assert any("TEST:ZZZZZZZ" in r.message for r in results)


def test_binding_plugin_lenient_mode_skips_nonexistent_term(plugin_cache_dir, tmp_path):
    """Test that lenient mode skips validation when term ID not found."""
    from linkml.validator.validation_context import ValidationContext  # type: ignore[import-untyped]
    from linkml_runtime import SchemaView
    from pathlib import Path

    oak_config_path = Path(__file__).parent / "data" / "test_oak_config.yaml"

    schema_path = tmp_path / "lenient_test_schema.yaml"
    schema_path.write_text("""
id: https://example.org/test
name: test_lenient

prefixes:
  linkml: https://w3id.org/linkml/
  TEST: http://example.org/TEST_

default_prefix: test_lenient
default_range: string

classes:
  Annotation:
    attributes:
      id:
        identifier: true
      term:
        range: Term
        inlined: true
        bindings:
          - binds_value_of: id
            range: TestEnum

  Term:
    attributes:
      id:
        description: CURIE
      label:
        description: Label

enums:
  TestEnum:
    reachable_from:
      source_ontology: simpleobo:tests/data/test_ontology.obo
      source_nodes:
        - TEST:0000001
      include_self: true
""")

    # Lenient mode (strict=False)
    plugin = BindingValidationPlugin(
        validate_labels=False,
        strict=False,
        cache_dir=plugin_cache_dir,
        oak_config_path=oak_config_path,
    )

    schema_view = SchemaView(str(schema_path))
    context = ValidationContext(
        schema=schema_view.schema,
        target_class="Annotation",
    )
    plugin.pre_process(context)

    # Test with NON-EXISTENT term ID (lenient should pass)
    instance_nonexistent = {
        "id": "ann1",
        "term": {
            "id": "TEST:ZZZZZZZ",  # Non-existent ID
            "label": "Fake Term",
        }
    }
    results = list(plugin.process(instance_nonexistent, context))
    # Should have no "term not found" errors in lenient mode
    term_not_found_errors = [r for r in results if "not found in ontology" in r.message]
    assert len(term_not_found_errors) == 0, f"Expected no 'term not found' errors in lenient mode, got: {term_not_found_errors}"


def test_binding_plugin_strict_skips_unconfigured_prefix(plugin_cache_dir, tmp_path):
    """Test that strict mode does NOT fail for unconfigured prefixes (only configured ones)."""
    from linkml.validator.validation_context import ValidationContext  # type: ignore[import-untyped]
    from linkml_runtime import SchemaView
    from pathlib import Path

    oak_config_path = Path(__file__).parent / "data" / "test_oak_config.yaml"

    schema_path = tmp_path / "unconfigured_prefix_schema.yaml"
    schema_path.write_text("""
id: https://example.org/test
name: test_unconfigured

prefixes:
  linkml: https://w3id.org/linkml/
  UNKNOWN: http://example.org/UNKNOWN_

default_prefix: test_unconfigured
default_range: string

classes:
  Annotation:
    attributes:
      id:
        identifier: true
      term:
        range: Term
        inlined: true
        bindings:
          - binds_value_of: id
            range: UnknownEnum

  Term:
    attributes:
      id:
        description: CURIE
      label:
        description: Label

enums:
  UnknownEnum:
    permissible_values:
      ITEM_A:
        meaning: UNKNOWN:001
""")

    # Strict mode
    plugin = BindingValidationPlugin(
        validate_labels=False,
        strict=True,
        cache_dir=plugin_cache_dir,
        oak_config_path=oak_config_path,
    )

    schema_view = SchemaView(str(schema_path))
    context = ValidationContext(
        schema=schema_view.schema,
        target_class="Annotation",
    )
    plugin.pre_process(context)

    # Test with an UNKNOWN prefix term (should NOT fail - prefix not configured)
    instance = {
        "id": "ann1",
        "term": {
            "id": "UNKNOWN:001",  # Prefix not in oak_config
            "label": "Unknown Term",
        }
    }
    results = list(plugin.process(instance, context))
    # Should have no "term not found" errors for unconfigured prefix
    term_not_found_errors = [r for r in results if "not found in ontology" in r.message]
    assert len(term_not_found_errors) == 0, f"Expected no 'term not found' for unconfigured prefix, got: {term_not_found_errors}"


def test_binding_plugin_strict_passes_existing_term(plugin_cache_dir, tmp_path):
    """Test that strict mode passes for terms that actually exist."""
    from linkml.validator.validation_context import ValidationContext  # type: ignore[import-untyped]
    from linkml_runtime import SchemaView
    from pathlib import Path

    oak_config_path = Path(__file__).parent / "data" / "test_oak_config.yaml"

    schema_path = tmp_path / "existing_term_schema.yaml"
    schema_path.write_text("""
id: https://example.org/test
name: test_existing

prefixes:
  linkml: https://w3id.org/linkml/
  TEST: http://example.org/TEST_

default_prefix: test_existing
default_range: string

classes:
  Annotation:
    attributes:
      id:
        identifier: true
      term:
        range: Term
        inlined: true
        bindings:
          - binds_value_of: id
            range: TestEnum

  Term:
    attributes:
      id:
        description: CURIE
      label:
        description: Label

enums:
  TestEnum:
    reachable_from:
      source_ontology: simpleobo:tests/data/test_ontology.obo
      source_nodes:
        - TEST:0000001
      include_self: true
""")

    plugin = BindingValidationPlugin(
        validate_labels=False,
        strict=True,
        cache_dir=plugin_cache_dir,
        oak_config_path=oak_config_path,
    )

    schema_view = SchemaView(str(schema_path))
    context = ValidationContext(
        schema=schema_view.schema,
        target_class="Annotation",
    )
    plugin.pre_process(context)

    # Test with EXISTING term ID (should pass)
    instance_existing = {
        "id": "ann1",
        "term": {
            "id": "TEST:0000002",  # This exists in test_ontology.obo
            "label": "child term one",
        }
    }
    results = list(plugin.process(instance_existing, context))
    term_not_found_errors = [r for r in results if "not found in ontology" in r.message]
    assert len(term_not_found_errors) == 0, f"Expected no 'term not found' errors for existing term, got: {term_not_found_errors}"


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


# =============================================================================
# Tests for Binding + Dynamic Enum Closure Validation
# =============================================================================


def test_binding_with_dynamic_enum_validates_closure(plugin_cache_dir, tmp_path):
    """Test that bindings with dynamic enums properly validate ontology closure.

    This tests the fix for the gap where dynamic enum closure validation
    was not being applied to bindings (only to direct slot ranges).

    Uses the local test ontology with structure:
    - TEST:0000005 (biological_process)
      └── TEST:0000006 (cell_cycle)
    - TEST:0000001 (root)
      ├── TEST:0000002 (child term one)
      │   └── TEST:0000004 (grandchild)
      └── TEST:0000003 (child term two)
    """
    from linkml.validator.validation_context import ValidationContext  # type: ignore[import-untyped]
    from linkml_runtime import SchemaView
    from pathlib import Path

    oak_config_path = Path(__file__).parent / "data" / "test_oak_config.yaml"

    # Schema with binding to a dynamic enum (reachable_from biological_process)
    schema_path = tmp_path / "binding_dynamic_enum_schema.yaml"
    schema_path.write_text("""
id: https://example.org/test
name: test_binding_dynamic

prefixes:
  linkml: https://w3id.org/linkml/
  TEST: http://example.org/TEST_

default_prefix: test_binding_dynamic
default_range: string

classes:
  Annotation:
    attributes:
      id:
        identifier: true
      process:
        range: ProcessTerm
        inlined: true
        bindings:
          - binds_value_of: id
            range: BioProcessEnum

  ProcessTerm:
    attributes:
      id:
        description: CURIE for the process
      label:
        description: Human-readable label

enums:
  BioProcessEnum:
    description: Biological processes (descendants of TEST:0000005)
    reachable_from:
      source_ontology: simpleobo:tests/data/test_ontology.obo
      source_nodes:
        - TEST:0000005  # biological_process
      relationship_types:
        - rdfs:subClassOf
      include_self: true
""")

    # Use greedy cache strategy to verify upfront expansion
    from linkml_term_validator.models import CacheStrategy

    plugin = BindingValidationPlugin(
        validate_labels=False,
        strict=True,
        cache_dir=plugin_cache_dir,
        oak_config_path=oak_config_path,
        cache_strategy=CacheStrategy.GREEDY,  # Use greedy to test upfront expansion
    )

    schema_view = SchemaView(str(schema_path))
    context = ValidationContext(
        schema=schema_view.schema,
        target_class="Annotation",
    )
    plugin.pre_process(context)

    # Verify enum was expanded (only happens in greedy mode)
    assert "BioProcessEnum" in plugin.expanded_enums
    allowed = plugin.expanded_enums["BioProcessEnum"]
    assert "TEST:0000005" in allowed  # biological_process (include_self=true)
    assert "TEST:0000006" in allowed  # cell_cycle (child)

    # Test 1: Valid term IN closure (cell_cycle is under biological_process)
    valid_instance = {
        "id": "ann1",
        "process": {
            "id": "TEST:0000006",  # cell_cycle - IN closure
            "label": "cell_cycle",
        }
    }
    results = list(plugin.process(valid_instance, context))
    closure_errors = [r for r in results if "not in dynamic enum" in r.message]
    assert len(closure_errors) == 0, f"Expected no closure errors for valid term, got: {closure_errors}"

    # Test 2: Term EXISTS but NOT in closure (child term one is under root, not bio_process)
    invalid_closure = {
        "id": "ann2",
        "process": {
            "id": "TEST:0000002",  # child term one - NOT in biological_process closure
            "label": "child term one",
        }
    }
    results = list(plugin.process(invalid_closure, context))
    closure_errors = [r for r in results if "not in dynamic enum" in r.message]
    assert len(closure_errors) == 1, f"Expected 1 closure error, got: {closure_errors}"
    assert "TEST:0000002" in closure_errors[0].message
    assert "BioProcessEnum" in closure_errors[0].message

    # Test 3: Fabricated term (doesn't exist at all)
    fabricated = {
        "id": "ann3",
        "process": {
            "id": "TEST:ZZZZZZZ",  # Doesn't exist
            "label": "Fake",
        }
    }
    results = list(plugin.process(fabricated, context))
    # Should get both closure error AND term not found error (strict mode)
    closure_errors = [r for r in results if "not in dynamic enum" in r.message]
    existence_errors = [r for r in results if "not found in ontology" in r.message]
    assert len(closure_errors) >= 1, "Expected closure error for fabricated term"
    assert len(existence_errors) >= 1, "Expected existence error for fabricated term"


def test_binding_with_dynamic_enum_multiple_source_nodes(plugin_cache_dir, tmp_path):
    """Test dynamic enum bindings with multiple source nodes."""
    from linkml.validator.validation_context import ValidationContext  # type: ignore[import-untyped]
    from linkml_runtime import SchemaView
    from pathlib import Path
    from linkml_term_validator.models import CacheStrategy

    oak_config_path = Path(__file__).parent / "data" / "test_oak_config.yaml"

    # Schema with multiple source nodes (union of descendants)
    schema_path = tmp_path / "multi_source_binding_schema.yaml"
    schema_path.write_text("""
id: https://example.org/test
name: test_multi_source

prefixes:
  linkml: https://w3id.org/linkml/
  TEST: http://example.org/TEST_

default_prefix: test_multi_source
default_range: string

classes:
  Annotation:
    attributes:
      id:
        identifier: true
      term:
        range: Term
        inlined: true
        bindings:
          - binds_value_of: id
            range: MultiSourceEnum

  Term:
    attributes:
      id:
      label:

enums:
  MultiSourceEnum:
    description: Terms under child_one OR child_two
    reachable_from:
      source_ontology: simpleobo:tests/data/test_ontology.obo
      source_nodes:
        - TEST:0000002  # child term one
        - TEST:0000003  # child term two
      relationship_types:
        - rdfs:subClassOf
""")

    plugin = BindingValidationPlugin(
        validate_labels=False,
        cache_dir=plugin_cache_dir,
        oak_config_path=oak_config_path,
        cache_strategy=CacheStrategy.GREEDY,  # Use greedy for expanded_enums test
    )

    schema_view = SchemaView(str(schema_path))
    context = ValidationContext(
        schema=schema_view.schema,
        target_class="Annotation",
    )
    plugin.pre_process(context)

    # Verify expanded enum includes descendants of both sources
    assert "MultiSourceEnum" in plugin.expanded_enums
    allowed = plugin.expanded_enums["MultiSourceEnum"]
    # Grandchild is descendant of child_one
    assert "TEST:0000004" in allowed

    # Valid: grandchild term (under child_one)
    valid_instance = {
        "id": "ann1",
        "term": {"id": "TEST:0000004", "label": "grandchild term"}
    }
    results = list(plugin.process(valid_instance, context))
    closure_errors = [r for r in results if "not in dynamic enum" in r.message]
    assert len(closure_errors) == 0

    # Invalid: biological_process (not under either source)
    invalid_instance = {
        "id": "ann2",
        "term": {"id": "TEST:0000005", "label": "biological_process"}
    }
    results = list(plugin.process(invalid_instance, context))
    closure_errors = [r for r in results if "not in dynamic enum" in r.message]
    assert len(closure_errors) == 1


def test_binding_with_static_enum_still_works(plugin_cache_dir, tmp_path):
    """Test that static enums (no reachable_from) still work with bindings."""
    from linkml.validator.validation_context import ValidationContext  # type: ignore[import-untyped]
    from linkml_runtime import SchemaView

    schema_path = tmp_path / "static_enum_binding_schema.yaml"
    schema_path.write_text("""
id: https://example.org/test
name: test_static

prefixes:
  linkml: https://w3id.org/linkml/
  TEST: http://example.org/TEST_

default_prefix: test_static
default_range: string

classes:
  Annotation:
    attributes:
      id:
        identifier: true
      term:
        range: Term
        inlined: true
        bindings:
          - binds_value_of: id
            range: StaticEnum

  Term:
    attributes:
      id:
      label:

enums:
  StaticEnum:
    permissible_values:
      ITEM_A:
        meaning: TEST:0001
      ITEM_B:
        meaning: TEST:0002
""")

    plugin = BindingValidationPlugin(
        validate_labels=False,
        cache_dir=plugin_cache_dir,
    )

    schema_view = SchemaView(str(schema_path))
    context = ValidationContext(
        schema=schema_view.schema,
        target_class="Annotation",
    )
    plugin.pre_process(context)

    # Static enum should NOT be in expanded_enums
    assert "StaticEnum" not in plugin.expanded_enums

    # Valid: TEST:0001 is a meaning in the static enum
    valid_instance = {
        "id": "ann1",
        "term": {"id": "TEST:0001", "label": "Item A"}
    }
    results = list(plugin.process(valid_instance, context))
    enum_errors = [r for r in results if "not in enum" in r.message]
    assert len(enum_errors) == 0

    # Invalid: TEST:9999 is not in the static enum
    invalid_instance = {
        "id": "ann2",
        "term": {"id": "TEST:9999", "label": "Invalid"}
    }
    results = list(plugin.process(invalid_instance, context))
    enum_errors = [r for r in results if "not in enum" in r.message]
    assert len(enum_errors) == 1
    assert "TEST:9999" in enum_errors[0].message


def test_base_plugin_is_dynamic_enum():
    """Test is_dynamic_enum method on base plugin."""
    from linkml_runtime.linkml_model import EnumDefinition
    from linkml_runtime.linkml_model.meta import ReachabilityQuery

    plugin = BindingValidationPlugin()

    # Static enum (only permissible values)
    static = EnumDefinition(name="Static")
    assert plugin.is_dynamic_enum(static) is False

    # Dynamic enum with reachable_from
    dynamic_rf = EnumDefinition(name="DynamicRF")
    dynamic_rf.reachable_from = ReachabilityQuery(source_nodes=["GO:0008150"])
    assert plugin.is_dynamic_enum(dynamic_rf) is True

    # Dynamic enum with concepts
    dynamic_concepts = EnumDefinition(name="DynamicConcepts", concepts=["A", "B"])
    assert plugin.is_dynamic_enum(dynamic_concepts) is True


def test_base_plugin_expand_enum_with_permissible_values():
    """Test expand_enum with static permissible values."""
    from linkml_runtime.linkml_model import EnumDefinition, PermissibleValue

    plugin = BindingValidationPlugin()

    # Enum with permissible values
    enum_def = EnumDefinition(
        name="TestEnum",
        permissible_values={
            "A": PermissibleValue(text="A", meaning="TEST:001"),
            "B": PermissibleValue(text="B", meaning="TEST:002"),
            "C": PermissibleValue(text="C"),  # No meaning
        }
    )

    expanded = plugin.expand_enum(enum_def)
    assert "A" in expanded
    assert "B" in expanded
    assert "C" in expanded
    assert "TEST:001" in expanded
    assert "TEST:002" in expanded


# =============================================================================
# Tests for Enum Caching
# =============================================================================


def test_enum_caching_saves_to_file(plugin_cache_dir, tmp_path):
    """Test that expanded dynamic enums are cached to disk (greedy mode)."""
    from linkml.validator.validation_context import ValidationContext  # type: ignore[import-untyped]
    from linkml_runtime import SchemaView
    from pathlib import Path
    import csv
    from linkml_term_validator.models import CacheStrategy

    oak_config_path = Path(__file__).parent / "data" / "test_oak_config.yaml"

    schema_path = tmp_path / "cache_test_schema.yaml"
    schema_path.write_text("""
id: https://example.org/test
name: test_cache

prefixes:
  linkml: https://w3id.org/linkml/
  TEST: http://example.org/TEST_

default_prefix: test_cache
default_range: string

classes:
  Annotation:
    attributes:
      id:
        identifier: true
      term:
        range: Term
        inlined: true
        bindings:
          - binds_value_of: id
            range: CachedEnum

  Term:
    attributes:
      id:
      label:

enums:
  CachedEnum:
    description: Test enum for caching
    reachable_from:
      source_ontology: simpleobo:tests/data/test_ontology.obo
      source_nodes:
        - TEST:0000001
      relationship_types:
        - rdfs:subClassOf
      include_self: true
""")

    plugin = BindingValidationPlugin(
        cache_labels=True,  # Enable caching
        cache_dir=plugin_cache_dir,
        oak_config_path=oak_config_path,
        cache_strategy=CacheStrategy.GREEDY,  # Use greedy to test upfront expansion
    )

    schema_view = SchemaView(str(schema_path))
    context = ValidationContext(
        schema=schema_view.schema,
        target_class="Annotation",
    )
    plugin.pre_process(context)

    # Verify enum was expanded (greedy mode)
    assert "CachedEnum" in plugin.expanded_enums
    original_values = plugin.expanded_enums["CachedEnum"]
    assert len(original_values) > 0

    # Check that cache file was created (CSV format)
    enum_cache_dir = plugin_cache_dir / "enums"
    assert enum_cache_dir.exists()
    cache_files = list(enum_cache_dir.glob("cachedenum_*.csv"))
    assert len(cache_files) == 1, f"Expected 1 CSV cache file, found: {cache_files}"

    # Verify cache file contents (CSV format)
    cached_values = set()
    with open(cache_files[0]) as f:
        reader = csv.DictReader(f)
        for row in reader:
            cached_values.add(row["curie"])
    assert cached_values == original_values


def test_enum_caching_loads_from_file(plugin_cache_dir, tmp_path):
    """Test that enum values are loaded from cache on subsequent runs (greedy mode)."""
    from linkml.validator.validation_context import ValidationContext  # type: ignore[import-untyped]
    from linkml_runtime import SchemaView
    from pathlib import Path
    from linkml_term_validator.models import CacheStrategy

    oak_config_path = Path(__file__).parent / "data" / "test_oak_config.yaml"

    schema_path = tmp_path / "cache_load_schema.yaml"
    schema_path.write_text("""
id: https://example.org/test
name: test_cache_load

prefixes:
  linkml: https://w3id.org/linkml/
  TEST: http://example.org/TEST_

default_prefix: test_cache_load
default_range: string

classes:
  Annotation:
    attributes:
      id:
        identifier: true
      term:
        range: Term
        inlined: true
        bindings:
          - binds_value_of: id
            range: LoadTestEnum

  Term:
    attributes:
      id:
      label:

enums:
  LoadTestEnum:
    description: Test enum for cache loading
    reachable_from:
      source_ontology: simpleobo:tests/data/test_ontology.obo
      source_nodes:
        - TEST:0000001
      relationship_types:
        - rdfs:subClassOf
      include_self: true
""")

    # First run - expand and cache (greedy mode)
    plugin1 = BindingValidationPlugin(
        cache_labels=True,
        cache_dir=plugin_cache_dir,
        oak_config_path=oak_config_path,
        cache_strategy=CacheStrategy.GREEDY,
    )
    schema_view = SchemaView(str(schema_path))
    context = ValidationContext(schema=schema_view.schema, target_class="Annotation")
    plugin1.pre_process(context)
    original_values = plugin1.expanded_enums["LoadTestEnum"]

    # Verify cache file exists (CSV format)
    enum_cache_dir = plugin_cache_dir / "enums"
    cache_files = list(enum_cache_dir.glob("loadtestenum_*.csv"))
    assert len(cache_files) == 1

    # Second run - should load from cache (greedy mode)
    plugin2 = BindingValidationPlugin(
        cache_labels=True,
        cache_dir=plugin_cache_dir,
        oak_config_path=oak_config_path,
        cache_strategy=CacheStrategy.GREEDY,
    )
    schema_view2 = SchemaView(str(schema_path))
    context2 = ValidationContext(schema=schema_view2.schema, target_class="Annotation")
    plugin2.pre_process(context2)

    # Should have same values
    assert plugin2.expanded_enums["LoadTestEnum"] == original_values


def test_enum_cache_key_changes_with_definition(plugin_cache_dir):
    """Test that cache key changes when enum definition changes."""
    from linkml_runtime.linkml_model import EnumDefinition
    from linkml_runtime.linkml_model.meta import ReachabilityQuery

    plugin = BindingValidationPlugin(cache_dir=plugin_cache_dir)

    # Enum with one source node
    enum1 = EnumDefinition(name="TestEnum")
    enum1.reachable_from = ReachabilityQuery(source_nodes=["GO:0008150"])
    key1 = plugin._get_enum_cache_key(enum1)

    # Same name but different source node
    enum2 = EnumDefinition(name="TestEnum")
    enum2.reachable_from = ReachabilityQuery(source_nodes=["GO:0005575"])
    key2 = plugin._get_enum_cache_key(enum2)

    # Keys should be different
    assert key1 != key2

    # Same definition should give same key
    enum3 = EnumDefinition(name="TestEnum")
    enum3.reachable_from = ReachabilityQuery(source_nodes=["GO:0008150"])
    key3 = plugin._get_enum_cache_key(enum3)
    assert key1 == key3


def test_enum_caching_disabled(plugin_cache_dir, tmp_path):
    """Test that caching can be disabled."""
    from linkml.validator.validation_context import ValidationContext  # type: ignore[import-untyped]
    from linkml_runtime import SchemaView
    from pathlib import Path

    oak_config_path = Path(__file__).parent / "data" / "test_oak_config.yaml"

    schema_path = tmp_path / "no_cache_schema.yaml"
    schema_path.write_text("""
id: https://example.org/test
name: test_no_cache

prefixes:
  linkml: https://w3id.org/linkml/
  TEST: http://example.org/TEST_

default_prefix: test_no_cache
default_range: string

classes:
  Annotation:
    attributes:
      id:
        identifier: true
      term:
        range: Term
        inlined: true
        bindings:
          - binds_value_of: id
            range: NoCacheEnum

  Term:
    attributes:
      id:
      label:

enums:
  NoCacheEnum:
    reachable_from:
      source_ontology: simpleobo:tests/data/test_ontology.obo
      source_nodes:
        - TEST:0000001
      include_self: true
""")

    # Disable caching but use greedy mode to test that expansion still works
    from linkml_term_validator.models import CacheStrategy

    plugin = BindingValidationPlugin(
        cache_labels=False,  # Disable caching
        cache_dir=plugin_cache_dir,
        oak_config_path=oak_config_path,
        cache_strategy=CacheStrategy.GREEDY,  # Still need greedy to populate expanded_enums
    )

    schema_view = SchemaView(str(schema_path))
    context = ValidationContext(schema=schema_view.schema, target_class="Annotation")
    plugin.pre_process(context)

    # Enum should still be expanded (greedy mode)
    assert "NoCacheEnum" in plugin.expanded_enums

    # But no cache file should be created (CSV format)
    enum_cache_dir = plugin_cache_dir / "enums"
    if enum_cache_dir.exists():
        cache_files = list(enum_cache_dir.glob("nocacheenum_*.csv"))
        assert len(cache_files) == 0, f"Expected no cache files when caching disabled, got: {cache_files}"
