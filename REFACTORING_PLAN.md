# LinkML Term Validator Refactoring Plan

## Overview

Transform `linkml-term-validator` into a collection of **LinkML ValidationPlugin implementations** that validate ontology term references in three distinct contexts:

1. **Schema Validation**: Validate `meaning` fields in enum permissible values
2. **Dynamic Enum Validation**: Validate data against dynamically-defined enums
3. **Binding Validation**: Validate nested object fields against binding constraints

## Architecture

### Current State
- Single `EnumValidator` class that validates schema PVs with meanings
- Standalone CLI tool
- Multi-level caching (in-memory + file-based CSV)
- OAK adapter integration

### Target State
- Three `ValidationPlugin` implementations for LinkML validator framework
- Backward-compatible standalone CLI
- Integration with standard `linkml-validate` command
- Shared caching and OAK adapter logic
- Composable plugins that can be used individually or together

## Three ValidationPlugin Implementations

### 1. PermissibleValueMeaningPlugin (Schema Validation)

**Purpose**: Validate that `meaning` fields in schema enum `permissible_values` reference valid ontology terms with correct labels.

**Use Case Example**:
```yaml
# Schema
enums:
  BiologicalProcessEnum:
    permissible_values:
      CELL_CYCLE:
        title: cell cycle
        meaning: GO:0007049  # ← Validate this exists and label matches
```

**Implementation**:
```python
from linkml.validator.plugins import ValidationPlugin

class PermissibleValueMeaningPlugin(ValidationPlugin):
    """Validates meaning fields in enum permissible_values"""

    def __init__(self, oak_adapter_string="sqlite:obo:",
                 cache_labels=True, cache_dir=Path("cache"),
                 oak_config_path=None, strict_mode=False):
        self.config = ValidationConfig(...)
        self._label_cache = {}
        self._adapter_cache = {}
        self._oak_config = {}

    def pre_process(self, context: ValidationContext) -> None:
        """Load schema and identify enums with meanings"""
        self.schema_view = context.schema_view
        # Load oak_config if provided
        # Initialize OAK adapters

    def process(self, instance: dict, context: ValidationContext) -> Iterator[ValidationResult]:
        """Validate enum permissible_values in schema"""
        # This plugin validates the SCHEMA itself
        # So instance should be the schema or we extract enums from context
        # For each enum with permissible_values:
        #   For each PV with a meaning:
        #     Validate meaning exists and label matches
        if meaning_invalid:
            yield ValidationResult(
                type="permissible_value_meaning",
                severity=Severity.ERROR,
                message=f"Invalid meaning {curie}: {reason}",
                instance=instance,
                context=[f"enum: {enum_name}", f"value: {pv_name}"]
            )
```

### 2. DynamicEnumPlugin (Data Validation)

**Purpose**: Validate data values against dynamically-defined enums that use `reachable_from`, `matches`, `concepts`, etc.

**Use Case Example**:
```yaml
# Schema
enums:
  NeuronTypeEnum:
    reachable_from:
      source_ontology: obo:cl
      source_nodes:
        - CL:0000540  # neuron
      relationship_types:
        - rdfs:subClassOf

classes:
  Observation:
    slots:
      - cell_type
    slot_usage:
      cell_type:
        range: NeuronTypeEnum

# Data
cell_type: CL:0000100  # ← Validate this is reachable from CL:0000540
```

**Implementation**:
```python
class DynamicEnumPlugin(ValidationPlugin):
    """Validates data against dynamic enum definitions"""

    def __init__(self, oak_adapter_string="sqlite:obo:",
                 cache_dir=Path("cache")):
        self.config = ValidationConfig(...)
        self.expanded_enums = {}

    def pre_process(self, context: ValidationContext) -> None:
        """Materialize all dynamic enums in schema"""
        self.schema_view = context.schema_view

        for enum in self.schema_view.all_enums().values():
            if self._is_dynamic_enum(enum):
                # Use OAK to expand the enum
                self.expanded_enums[enum.name] = self._expand_enum(enum)

    def process(self, instance: dict, context: ValidationContext) -> Iterator[ValidationResult]:
        """Validate instance slot values against expanded dynamic enums"""
        # Get target class schema
        target_class = context.target_class

        # For each slot in instance:
        for slot_name, value in instance.items():
            slot = self.schema_view.induced_slot(slot_name, target_class)

            # If slot range is a dynamic enum:
            if slot.range in self.expanded_enums:
                allowed_values = self.expanded_enums[slot.range]

                # Handle multivalued
                values = value if isinstance(value, list) else [value]

                for val in values:
                    if val not in allowed_values:
                        yield ValidationResult(
                            type="dynamic_enum_validation",
                            severity=Severity.ERROR,
                            message=f"Value '{val}' not in dynamic enum {slot.range}",
                            instance=instance,
                            context=[f"slot: {slot_name}"]
                        )

    def _is_dynamic_enum(self, enum_def) -> bool:
        """Check if enum uses dynamic definition"""
        return (enum_def.reachable_from or enum_def.matches or
                enum_def.concepts or enum_def.include or enum_def.inherits)

    def _expand_enum(self, enum_def) -> set[str]:
        """Use OAK to materialize dynamic enum values"""
        values = set()

        # Handle reachable_from
        if enum_def.reachable_from:
            values.update(self._expand_reachable_from(enum_def.reachable_from))

        # Handle matches
        if enum_def.matches:
            values.update(self._expand_matches(enum_def.matches))

        # Handle concepts
        if enum_def.concepts:
            values.update(enum_def.concepts)

        # Handle include (union)
        if enum_def.include:
            for include_expr in enum_def.include:
                values.update(self._expand_enum_expression(include_expr))

        # Handle minus (set difference)
        if enum_def.minus:
            for minus_expr in enum_def.minus:
                values -= self._expand_enum_expression(minus_expr)

        # Handle inherits
        if enum_def.inherits:
            for parent_enum_name in enum_def.inherits:
                parent_enum = self.schema_view.get_enum(parent_enum_name)
                values.update(self._expand_enum(parent_enum))

        return values

    def _expand_reachable_from(self, query) -> set[str]:
        """Expand reachable_from query using OAK"""
        # Get adapter for source ontology
        adapter = self._get_adapter_for_ontology(query.source_ontology)

        # Use OAK relationships methods
        values = set()
        for source_node in query.source_nodes:
            if query.traverse_up:
                # Get ancestors
                ancestors = adapter.ancestors(source_node,
                                             predicates=query.relationship_types,
                                             reflexive=query.include_self)
                values.update(ancestors)
            else:
                # Get descendants
                descendants = adapter.descendants(source_node,
                                                 predicates=query.relationship_types,
                                                 reflexive=query.include_self)
                values.update(descendants)

        return values
```

### 3. BindingValidationPlugin (Data Validation)

**Purpose**: Validate nested object fields against binding constraints that restrict specific fields to enum values.

**Use Case Example**:
```yaml
# Schema
classes:
  Annotation:
    slots:
      - term
    slot_usage:
      term:
        range: Term  # Complex object: {id: ..., label: ...}
        bindings:
          - binds_value_of: id
            range: GOTermEnum
            obligation_level: REQUIRED

# Data
term:
  id: GO:0008150          # ← Validate against GOTermEnum
  label: biological process  # ← Optionally validate label matches ontology
```

**Implementation**:
```python
class BindingValidationPlugin(ValidationPlugin):
    """Validates bindings in nested objects"""

    def __init__(self, oak_adapter_string="sqlite:obo:",
                 validate_labels=False,
                 cache_dir=Path("cache")):
        self.config = ValidationConfig(...)
        self.validate_labels = validate_labels
        self.bindings_map = {}  # (class_name, slot_name) -> [EnumBinding]

    def pre_process(self, context: ValidationContext) -> None:
        """Extract all bindings from schema"""
        self.schema_view = context.schema_view

        # Walk schema and collect all bindings
        for cls in self.schema_view.all_classes().values():
            for slot in self.schema_view.class_induced_slots(cls.name):
                if slot.bindings:
                    key = (cls.name, slot.name)
                    self.bindings_map[key] = slot.bindings

    def process(self, instance: dict, context: ValidationContext) -> Iterator[ValidationResult]:
        """Validate binding constraints on nested fields"""
        target_class = context.target_class

        # For each slot in instance:
        for slot_name, value in instance.items():
            key = (target_class, slot_name)

            if key not in self.bindings_map:
                continue

            # Handle multivalued
            values = value if isinstance(value, list) else [value]

            for binding in self.bindings_map[key]:
                # For each binding, validate the constraint
                for val in values:
                    yield from self._validate_binding(
                        val, binding, slot_name, instance
                    )

    def _validate_binding(self, value, binding, slot_name, instance):
        """Validate a single binding constraint"""
        # Extract the field specified by binds_value_of
        field_path = binding.binds_value_of

        # Navigate to the field (simple case: direct field access)
        if isinstance(value, dict) and field_path in value:
            field_value = value[field_path]
        else:
            # Handle complex paths (e.g., "extensions.0.value")
            field_value = self._extract_field(value, field_path)

        if field_value is None:
            if binding.obligation_level == "REQUIRED":
                yield ValidationResult(
                    type="binding_validation",
                    severity=Severity.ERROR,
                    message=f"Required binding field '{field_path}' not found",
                    instance=instance,
                    context=[f"slot: {slot_name}"]
                )
            return

        # Validate against the enum range
        enum_def = self.schema_view.get_enum(binding.range)

        if enum_def:
            # Get permissible values
            pv_texts = set(enum_def.permissible_values.keys()) if enum_def.permissible_values else set()

            # Also check meanings
            valid_meanings = set()
            if enum_def.permissible_values:
                for pv in enum_def.permissible_values.values():
                    if pv.meaning:
                        valid_meanings.add(pv.meaning)

            if field_value not in pv_texts and field_value not in valid_meanings:
                yield ValidationResult(
                    type="binding_validation",
                    severity=Severity.ERROR,
                    message=f"Value '{field_value}' not in enum {binding.range}",
                    instance=instance,
                    context=[f"slot: {slot_name}", f"field: {field_path}"]
                )

        # Optionally validate label matches ontology
        if self.validate_labels and isinstance(value, dict):
            if 'label' in value and field_value:
                ontology_label = self.get_ontology_label(field_value)
                if ontology_label:
                    normalized_provided = self.normalize_string(value['label'])
                    normalized_ontology = self.normalize_string(ontology_label)

                    if normalized_provided != normalized_ontology:
                        yield ValidationResult(
                            type="binding_label_mismatch",
                            severity=Severity.WARNING,
                            message=f"Label mismatch for {field_value}: expected '{ontology_label}', got '{value['label']}'",
                            instance=instance,
                            context=[f"slot: {slot_name}"]
                        )
```

## Project Structure

```
src/linkml_term_validator/
├── __init__.py
├── plugins/
│   ├── __init__.py
│   ├── base.py                          # BaseOntologyPlugin with shared OAK/caching
│   ├── permissible_value_plugin.py      # PermissibleValueMeaningPlugin
│   ├── dynamic_enum_plugin.py           # DynamicEnumPlugin
│   └── binding_plugin.py                # BindingValidationPlugin
├── models.py                            # Keep existing Pydantic models
├── validator.py                         # Legacy EnumValidator (backward compat)
└── cli.py                               # Keep backward compatible CLI
```

## Implementation Steps

### Phase 1: Create Plugin Foundation
1. Add `linkml` as dependency (for ValidationPlugin base class)
2. Create `plugins/` directory
3. Create `plugins/base.py` with shared OAK adapter and caching logic
4. Extract common functionality from current `EnumValidator` into base plugin

### Phase 2: Implement PermissibleValueMeaningPlugin
1. Port existing schema validation logic to plugin
2. Implement `pre_process()`, `process()`, `post_process()` methods
3. Yield `ValidationResult` objects instead of custom models
4. Add tests for plugin

### Phase 3: Implement DynamicEnumPlugin
1. Implement enum expansion logic for `reachable_from`
2. Implement enum expansion for `matches`, `concepts`
3. Implement set operations: `include`, `minus`, `inherits`
4. Add validation logic for data instances
5. Add tests for plugin

### Phase 4: Implement BindingValidationPlugin
1. Implement binding extraction from schema
2. Implement field path navigation (binds_value_of)
3. Implement validation against enum ranges
4. Add optional label validation
5. Add tests for plugin

### Phase 5: Backward Compatibility
1. Refactor `EnumValidator` to use `PermissibleValueMeaningPlugin` internally
2. Keep existing API intact
3. Update CLI to support both legacy and plugin modes
4. Ensure all existing tests pass

### Phase 6: Documentation and Examples
1. Update README with all three use cases
2. Add plugin usage examples
3. Document integration with `linkml-validate`
4. Add example data files for each use case

## Backward Compatibility

### Keep Existing CLI Working
```bash
# Current usage (backward compatible)
linkml-term-validator schema.yaml

# Still works exactly as before
linkml-term-validator schema.yaml --cache-dir cache --verbose
```

### New Plugin-Based Usage
```bash
# Use with standard linkml-validate
linkml-validate --schema schema.yaml \
  --plugin linkml_term_validator.plugins.PermissibleValueMeaningPlugin \
  schema.yaml

# Multiple plugins for comprehensive validation
linkml-validate --schema schema.yaml \
  --plugin linkml_term_validator.plugins.DynamicEnumPlugin \
  --plugin linkml_term_validator.plugins.BindingValidationPlugin \
  data.yaml
```

## Benefits

1. **Composable**: Use plugins individually or together
2. **Reusable**: Plugins work with standard LinkML validator framework
3. **Backward Compatible**: Keep existing `EnumValidator` API
4. **Extensible**: Easy to add new validation types
5. **Integration**: Works with other LinkML validation plugins (JSON Schema, Pydantic, SHACL, etc.)
6. **Standard**: Uses LinkML's validation reporting infrastructure

## Example Usage

### Python API

```python
from linkml.validator import Validator
from linkml_term_validator.plugins import (
    PermissibleValueMeaningPlugin,
    DynamicEnumPlugin,
    BindingValidationPlugin
)

# Validate schema
plugins = [PermissibleValueMeaningPlugin()]
validator = Validator(schema="schema.yaml", validation_plugins=plugins)
report = validator.validate(schema_file)

# Validate data with dynamic enums and bindings
plugins = [
    DynamicEnumPlugin(oak_adapter_string="sqlite:obo:"),
    BindingValidationPlugin(validate_labels=True)
]
validator = Validator(schema="schema.yaml", validation_plugins=plugins)
report = validator.validate(data_file)

# Print results
print(f"Valid: {report.valid()}")
for result in report.results:
    print(f"{result.severity}: {result.message}")
```

### Combined with Other Validators

```python
from linkml.validator import Validator
from linkml.validator.plugins import JsonschemaValidationPlugin
from linkml_term_validator.plugins import DynamicEnumPlugin, BindingValidationPlugin

# Comprehensive validation: structure + ontology terms
plugins = [
    JsonschemaValidationPlugin(closed=True),  # Structural validation
    DynamicEnumPlugin(),                      # Dynamic enum validation
    BindingValidationPlugin()                 # Binding validation
]

validator = Validator(schema="schema.yaml", validation_plugins=plugins)
report = validator.validate(data_file)
```

## Testing Strategy

### Unit Tests
- Test each plugin independently
- Mock OAK adapter calls
- Test caching behavior
- Test error cases

### Integration Tests
- Test with real ontologies (GO, CHEBI, etc.)
- Test all three plugins together
- Test with LinkML validator framework
- Test CLI integration

### Test Data
- Create test schemas for each use case
- Create valid and invalid data instances
- Test edge cases (empty enums, missing fields, etc.)

## Migration Path

1. **Phase 1**: Implement plugins alongside existing code
2. **Phase 2**: Refactor `EnumValidator` to use plugins internally
3. **Phase 3**: Update documentation
4. **Phase 4**: Release with both legacy and plugin APIs
5. **Future**: Potentially deprecate legacy API in favor of plugins
