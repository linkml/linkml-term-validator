# Plugin Reference

This page provides complete API reference documentation for the three LinkML ValidationPlugin implementations provided by linkml-term-validator.

All plugins are designed to work with the [LinkML Validator framework](https://linkml.io/linkml/code/validator.html) and can be used standalone or composed together.

## PermissibleValueMeaningPlugin

Validates that `meaning` fields in enum permissible values reference valid ontology terms with correct labels.

**Module:** `linkml_term_validator.plugins.permissible_value_meaning_plugin`

### Constructor

```python
from linkml_term_validator.plugins import PermissibleValueMeaningPlugin

plugin = PermissibleValueMeaningPlugin(
    oak_adapter_string="sqlite:obo:",
    oak_config_path=None,
    strict_mode=False,
    cache_labels=True,
    cache_dir="cache",
)
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `oak_adapter_string` | `str` | `"sqlite:obo:"` | Default OAK adapter string for ontology access |
| `oak_config_path` | `str \| None` | `None` | Path to `oak_config.yaml` for per-prefix adapter configuration |
| `strict_mode` | `bool` | `False` | If `True`, treat all warnings as errors |
| `cache_labels` | `bool` | `True` | Enable file-based caching of ontology labels |
| `cache_dir` | `str` | `"cache"` | Directory for cache files |

### Usage

**Basic usage:**

```python
from linkml.validator import Validator
from linkml_term_validator.plugins import PermissibleValueMeaningPlugin

# Create plugin
plugin = PermissibleValueMeaningPlugin()

# Create validator with plugin
validator = Validator(
    schema="schema.yaml",
    validation_plugins=[plugin]
)

# Validate schema file
report = validator.validate_file("schema.yaml")

# Check results
if len(report.results) == 0:
    print("✅ All permissible values validated successfully")
else:
    for result in report.results:
        print(f"{result.severity.name}: {result.message}")
```

**With custom configuration:**

```python
plugin = PermissibleValueMeaningPlugin(
    oak_config_path="oak_config.yaml",
    strict_mode=True,
    cache_dir="custom_cache"
)
```

**With OLS adapter:**

```python
plugin = PermissibleValueMeaningPlugin(
    oak_adapter_string="ols:",
    cache_labels=False  # Don't cache when using online service
)
```

### What It Validates

For each enum in the schema:

1. Extracts all permissible values with `meaning` fields
2. For each meaning CURIE (e.g., `GO:0008150`):
   - Retrieves the canonical label from the ontology
   - Compares against expected labels from the schema
   - Reports mismatches or missing terms

**Expected labels are derived from:**

- Permissible value name (e.g., `BIOLOGICAL_PROCESS`)
- `title` field
- `description` field
- `aliases` list
- Annotation values for tags like `label`, `display_name`, `synonym`

### Validation Results

Results use LinkML's `ValidationResult` class with the following severity levels:

| Severity | Condition |
|----------|-----------|
| `ERROR` | Label mismatch for configured prefix (or any prefix in strict mode) |
| `WARN` | Label mismatch for unconfigured prefix (non-strict mode) |
| `INFO` | Missing term from unconfigured prefix |

### Example

**Schema:**

```yaml
enums:
  BiologicalProcessEnum:
    permissible_values:
      BIOLOGICAL_PROCESS:
        title: biological process
        meaning: GO:0008150
      CELL_CYCLE:
        title: cell cycle
        meaning: GO:0007049
```

**Code:**

```python
from linkml.validator import Validator
from linkml_term_validator.plugins import PermissibleValueMeaningPlugin

plugin = PermissibleValueMeaningPlugin()
validator = Validator(schema="schema.yaml", validation_plugins=[plugin])
report = validator.validate_file("schema.yaml")

print(f"Validated {len(report.results)} permissible values")
```

## DynamicEnumPlugin

Validates data values against dynamic enum constraints defined via `reachable_from`, `matches`, or `concepts`.

**Module:** `linkml_term_validator.plugins.dynamic_enum_plugin`

### Constructor

```python
from linkml_term_validator.plugins import DynamicEnumPlugin

plugin = DynamicEnumPlugin(
    oak_adapter_string="sqlite:obo:",
    oak_config_path=None,
    cache_labels=True,
    cache_dir="cache",
)
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `oak_adapter_string` | `str` | `"sqlite:obo:"` | Default OAK adapter string for ontology access |
| `oak_config_path` | `str \| None` | `None` | Path to `oak_config.yaml` for per-prefix adapter configuration |
| `cache_labels` | `bool` | `True` | Enable file-based caching of ontology labels |
| `cache_dir` | `str` | `"cache"` | Directory for cache files |

### Usage

**Basic usage:**

```python
from linkml.validator import Validator
from linkml_term_validator.plugins import DynamicEnumPlugin

# Create plugin
plugin = DynamicEnumPlugin()

# Create validator
validator = Validator(
    schema="schema.yaml",
    validation_plugins=[plugin]
)

# Validate data file
report = validator.validate_file("data.yaml")

# Check results
if len(report.results) == 0:
    print("✅ All dynamic enum constraints satisfied")
else:
    for result in report.results:
        print(f"{result.severity.name}: {result.message}")
```

**With YamlLoader (recommended for YAML data):**

```python
from linkml.validator import Validator
from linkml_runtime.loaders import yaml_loader
from linkml_term_validator.plugins import DynamicEnumPlugin

plugin = DynamicEnumPlugin()
validator = Validator(schema="schema.yaml", validation_plugins=[plugin])

loader = yaml_loader.YamlLoader()
report = validator.validate_source(loader, "data.yaml", target_class="Person")
```

### What It Validates

For each slot in the data with a dynamic enum range:

1. Checks if the value satisfies the enum constraint
2. For `reachable_from`: validates value is a descendant of source nodes
3. For `matches`: validates value matches the specified pattern
4. For `concepts`: validates value is one of the specified concepts

**Important semantics:**

- For `reachable_from`, source nodes themselves are **EXCLUDED** by default
- Only descendants (via specified relationship types) are included

### Validation Results

| Severity | Condition |
|----------|-----------|
| `ERROR` | Value does not satisfy dynamic enum constraint |

### Example

**Schema:**

```yaml
classes:
  Neuron:
    attributes:
      cell_type:
        range: NeuronTypeEnum

enums:
  NeuronTypeEnum:
    reachable_from:
      source_ontology: sqlite:obo:cl
      source_nodes:
        - CL:0000540  # neuron
      relationship_types:
        - rdfs:subClassOf
```

**Data:**

```yaml
neurons:
  - cell_type: CL:0000100  # Valid - descendant of CL:0000540
  - cell_type: GO:0008150  # INVALID - not a neuron type
```

**Code:**

```python
from linkml.validator import Validator
from linkml_runtime.loaders import yaml_loader
from linkml_term_validator.plugins import DynamicEnumPlugin

plugin = DynamicEnumPlugin()
validator = Validator(schema="schema.yaml", validation_plugins=[plugin])

loader = yaml_loader.YamlLoader()
report = validator.validate_source(loader, "neurons.yaml", target_class="Neuron")

# Will show ERROR for GO:0008150
for result in report.results:
    print(f"{result.severity.name}: {result.message}")
```

## BindingValidationPlugin

Validates that nested object fields satisfy binding range constraints, optionally checking labels match ontology.

**Module:** `linkml_term_validator.plugins.binding_validation_plugin`

### Constructor

```python
from linkml_term_validator.plugins import BindingValidationPlugin

plugin = BindingValidationPlugin(
    oak_adapter_string="sqlite:obo:",
    oak_config_path=None,
    validate_labels=False,
    cache_labels=True,
    cache_dir="cache",
)
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `oak_adapter_string` | `str` | `"sqlite:obo:"` | Default OAK adapter string for ontology access |
| `oak_config_path` | `str \| None` | `None` | Path to `oak_config.yaml` for per-prefix adapter configuration |
| `validate_labels` | `bool` | `False` | If `True`, also validate that labels match ontology canonical labels |
| `cache_labels` | `bool` | `True` | Enable file-based caching of ontology labels |
| `cache_dir` | `str` | `"cache"` | Directory for cache files |

### Usage

**Basic usage (binding validation only):**

```python
from linkml.validator import Validator
from linkml_term_validator.plugins import BindingValidationPlugin

# Create plugin without label validation
plugin = BindingValidationPlugin()

validator = Validator(
    schema="schema.yaml",
    validation_plugins=[plugin]
)

report = validator.validate_file("data.yaml")
```

**With label validation (anti-hallucination mode):**

```python
# Enable label validation for AI-generated data
plugin = BindingValidationPlugin(validate_labels=True)

validator = Validator(schema="schema.yaml", validation_plugins=[plugin])
report = validator.validate_file("ai_generated_data.yaml")

# Will catch both binding violations AND label mismatches
for result in report.results:
    print(f"{result.severity.name}: {result.message}")
```

**With custom configuration:**

```python
plugin = BindingValidationPlugin(
    oak_config_path="oak_config.yaml",
    validate_labels=True,
    cache_dir="custom_cache"
)
```

### What It Validates

For each slot with binding constraints:

1. **Binding constraint validation** (always):
   - Checks that the value in the nested object's field satisfies the binding's range constraint
   - Range is typically a dynamic enum (e.g., only biological processes, only neuron types)

2. **Label validation** (if `validate_labels=True`):
   - Detects label fields via `slot.implements` (e.g., `implements: [rdfs:label]`)
   - Falls back to convention (field named `label`) if no `implements` declared
   - Retrieves the canonical label from the ontology
   - Compares against the label provided in the data
   - Reports mismatches

### Nested Structure Support

The plugin **recursively validates bindings at all nesting levels**, not just the top-level target class. This is essential for schemas using the common patterns:

- **Descriptor pattern**: Wrapper objects with ontology term references
- **Annotation pattern**: Annotations with bound term fields
- **Deeply nested structures**: e.g., `Disease → Pathophysiology → CellTypes → Term`

**Example nested schema:**

```yaml
classes:
  Disease:
    attributes:
      disease_term:
        range: DiseaseDescriptor
        inlined: true
      pathophysiology:
        range: Pathophysiology
        multivalued: true
        inlined_as_list: true

  Pathophysiology:
    attributes:
      cell_types:
        range: CellTypeDescriptor
        multivalued: true
        inlined_as_list: true

  DiseaseDescriptor:
    attributes:
      term:
        range: Term
        bindings:
          - binds_value_of: id
            range: DiseaseTermEnum  # ← Validated!

  CellTypeDescriptor:
    attributes:
      term:
        range: Term
        bindings:
          - binds_value_of: id
            range: CellTypeEnum  # ← Also validated!
```

Error messages include the full JSON path to the violation:

```
❌ ERROR: Value 'CL:9999999' not in enum 'CellTypeEnum'
    path: pathophysiology[0].cell_types[1].term
    slot: term
    field: id
```

### Validation Results

| Severity | Condition |
|----------|-----------|
| `ERROR` | Binding constraint violated |
| `ERROR` | Label mismatch (if `validate_labels=True`) |
| `WARN` | Label mismatch for unconfigured prefix (if `validate_labels=True`) |

### Example

**Schema:**

```yaml
prefixes:
  rdfs: http://www.w3.org/2000/01/rdf-schema#

classes:
  GeneAnnotation:
    attributes:
      gene:
        range: string
      go_term:
        range: GOTerm
        bindings:
          - binds_value_of: id
            range: BiologicalProcessEnum

  GOTerm:
    attributes:
      id:
        range: string
      label:
        range: string
        implements:
          - rdfs:label  # Explicit label field declaration

enums:
  BiologicalProcessEnum:
    reachable_from:
      source_ontology: sqlite:obo:go
      source_nodes:
        - GO:0008150  # biological_process
      relationship_types:
        - rdfs:subClassOf
```

**Data:**

```yaml
annotations:
  - gene: BRCA1
    go_term:
      id: GO:0007049  # Valid - is a biological process
      label: cell cycle
  - gene: EGFR
    go_term:
      id: GO:0005575  # INVALID - cellular_component, not a process
      label: cellular_component
```

**Code:**

```python
from linkml.validator import Validator
from linkml_runtime.loaders import yaml_loader
from linkml_term_validator.plugins import BindingValidationPlugin

# With label validation enabled
plugin = BindingValidationPlugin(validate_labels=True)
validator = Validator(schema="schema.yaml", validation_plugins=[plugin])

loader = yaml_loader.YamlLoader()
report = validator.validate_source(
    loader,
    "annotations.yaml",
    target_class="GeneAnnotation"
)

# Will show ERROR for GO:0005575 (wrong category)
# Will also validate that labels match ontology
for result in report.results:
    print(f"{result.severity.name}: {result.message}")
```

## Composing Multiple Plugins

You can use multiple plugins together for comprehensive validation:

```python
from linkml.validator import Validator
from linkml.validator.plugins import JsonschemaValidationPlugin
from linkml_term_validator.plugins import (
    DynamicEnumPlugin,
    BindingValidationPlugin,
)

# Build validation pipeline
plugins = [
    JsonschemaValidationPlugin(closed=True),  # Structural validation
    DynamicEnumPlugin(),                       # Dynamic enum validation
    BindingValidationPlugin(validate_labels=True),  # Binding + label validation
]

# Create validator with all plugins
validator = Validator(
    schema="schema.yaml",
    validation_plugins=plugins
)

# Validate data
report = validator.validate_file("data.yaml")

# Check results
if len(report.results) == 0:
    print("✅ All validations passed")
else:
    # Group results by severity
    errors = [r for r in report.results if r.severity.name == "ERROR"]
    warnings = [r for r in report.results if r.severity.name == "WARN"]

    print(f"Errors: {len(errors)}")
    print(f"Warnings: {len(warnings)}")

    for result in report.results:
        print(f"{result.severity.name}: {result.message}")
```

## Integration with linkml-validate

All plugins can be used with the standard `linkml-validate` command via configuration files.

**Configuration file:**

```yaml
# validation_config.yaml
schema: schema.yaml
target_class: Person

data_sources:
  - data.yaml

plugins:
  # Standard LinkML validation
  JsonschemaValidationPlugin:
    closed: true

  # Dynamic enum validation
  "linkml_term_validator.plugins.DynamicEnumPlugin":
    oak_adapter_string: "sqlite:obo:"
    cache_labels: true
    cache_dir: cache

  # Binding validation with label checking
  "linkml_term_validator.plugins.BindingValidationPlugin":
    oak_adapter_string: "sqlite:obo:"
    validate_labels: true
    cache_labels: true
    cache_dir: cache
```

**Run validation:**

```bash
linkml-validate --config validation_config.yaml
```

## Advanced Usage

### Custom Validation Workflow

```python
from linkml.validator import Validator
from linkml_runtime.loaders import yaml_loader
from linkml_term_validator.plugins import (
    DynamicEnumPlugin,
    BindingValidationPlugin,
)

# Build custom validation pipeline
def validate_ai_generated_data(data_path, schema_path):
    """Validate AI-generated data with anti-hallucination checks."""

    plugins = [
        DynamicEnumPlugin(oak_adapter_string="sqlite:obo:"),
        BindingValidationPlugin(
            validate_labels=True,  # Enable label checking
            oak_adapter_string="sqlite:obo:"
        ),
    ]

    validator = Validator(schema=schema_path, validation_plugins=plugins)
    loader = yaml_loader.YamlLoader()

    report = validator.validate_source(
        loader,
        data_path,
        target_class="GeneAnnotation"
    )

    # Fail fast on any errors
    if len(report.results) > 0:
        errors = [r for r in report.results if r.severity.name == "ERROR"]
        if errors:
            raise ValueError(f"Validation failed with {len(errors)} error(s)")

    return report

# Use in AI pipeline
try:
    report = validate_ai_generated_data(
        "ai_output.yaml",
        "schema.yaml"
    )
    print("✅ AI-generated data validated successfully")
except ValueError as e:
    print(f"❌ {e}")
    # Retry AI generation or fail
```

### Accessing Validator State

```python
from linkml_term_validator.plugins import DynamicEnumPlugin

plugin = DynamicEnumPlugin()

# Access the OAK validator (for advanced use cases)
oak_validator = plugin.get_validator()

# Check unknown prefixes encountered during validation
unknown_prefixes = oak_validator.get_unknown_prefixes()
if unknown_prefixes:
    print("Unknown prefixes encountered:")
    for prefix in unknown_prefixes:
        print(f"  - {prefix}")
```

### Testing Plugins

```python
import pytest
from linkml.validator import Validator
from linkml_term_validator.plugins import DynamicEnumPlugin

def test_dynamic_enum_validation():
    """Test dynamic enum validation with local OBO file."""

    plugin = DynamicEnumPlugin(
        oak_config_path="tests/data/test_oak_config.yaml"
    )

    validator = Validator(
        schema="tests/data/dynamic_enum_schema.yaml",
        validation_plugins=[plugin]
    )

    report = validator.validate_file("tests/data/valid_data.yaml")

    # Should have no errors
    assert len(report.results) == 0

def test_invalid_dynamic_enum():
    """Test that invalid values are caught."""

    plugin = DynamicEnumPlugin(
        oak_config_path="tests/data/test_oak_config.yaml"
    )

    validator = Validator(
        schema="tests/data/dynamic_enum_schema.yaml",
        validation_plugins=[plugin]
    )

    report = validator.validate_file("tests/data/invalid_data.yaml")

    # Should have errors
    errors = [r for r in report.results if r.severity.name == "ERROR"]
    assert len(errors) > 0
```

## See Also

- [CLI Reference](cli-reference.md) - Command-line usage
- [Configuration](configuration.md) - Configuring OAK adapters
- [Validation Types](validation-types.md) - Understanding validation types
- [Anti-Hallucination Guardrails](anti-hallucination.md) - Preventing AI hallucinations
- [Tutorials](notebooks/03_python_api.ipynb) - Interactive Python API tutorial
