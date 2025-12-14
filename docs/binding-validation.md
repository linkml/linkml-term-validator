# Binding Validation Reference

This reference covers validation of **binding constraints on nested objects**—ensuring that fields within complex objects satisfy enum range constraints, with optional label validation.

## Overview

Binding validation uses the **BindingValidationPlugin** to validate that:

1. Fields within nested objects satisfy their binding range constraints
2. (Optionally) Labels match the ontology's canonical labels

Bindings are essential when your data uses complex objects (like `OntologyTerm` with `id` and `label`) rather than simple CURIE strings.

## CLI Usage

```bash
# Basic binding validation
linkml-term-validator validate-data data.yaml --schema schema.yaml

# With label validation (anti-hallucination)
linkml-term-validator validate-data data.yaml -s schema.yaml --labels

# With target class
linkml-term-validator validate-data data.yaml -s schema.yaml -t GeneAnnotation --labels

# With custom OAK configuration
linkml-term-validator validate-data data.yaml -s schema.yaml --oak-config oak_config.yaml --labels
```

### CLI Options

| Option | Description |
|--------|-------------|
| `--schema`, `-s` | Path to LinkML schema (required) |
| `--target-class`, `-t` | Target class for validation |
| `--labels` | Also validate labels against ontology |
| `--lenient` | Don't fail when term IDs are not found in ontology |
| `--oak-adapter` | OAK adapter string (default: `sqlite:obo:`) |
| `--oak-config` | Path to OAK configuration file |
| `--cache-dir` | Directory for cache files (default: `cache`) |
| `--verbose` / `-v` | Enable verbose output |

### Strict Mode (Default)

By default, the validator operates in **strict mode**, which fails validation when:

1. A term ID is not found in a configured ontology
2. A term ID is outside the dynamic enum closure

This catches fabricated/hallucinated term IDs that don't actually exist.

Use `--lenient` to disable strict existence checking (closure validation still applies).

## Binding Syntax

### Basic Binding

```yaml
classes:
  GeneAnnotation:
    attributes:
      go_term:
        range: OntologyTerm
        bindings:
          - binds_value_of: id              # Field to constrain
            range: BiologicalProcessEnum    # Enum defining valid values
```

### Binding Properties

| Property | Required | Description |
|----------|----------|-------------|
| `binds_value_of` | Yes | The field path within the nested object |
| `range` | Yes | The enum (static or dynamic) defining allowed values |
| `obligation_level` | No | `REQUIRED`, `RECOMMENDED`, or `OPTIONAL` |

### Obligation Levels

| Level | Description | Validation Behavior |
|-------|-------------|---------------------|
| `REQUIRED` | Must satisfy constraint | ERROR on violation |
| `RECOMMENDED` | Should satisfy constraint | WARN on violation |
| `OPTIONAL` | May satisfy constraint | No validation |

```yaml
bindings:
  - binds_value_of: id
    range: BiologicalProcessEnum
    obligation_level: REQUIRED     # Default if not specified
```

## Label Field Detection

The plugin needs to know which field contains the label to validate. There are three mechanisms:

### 1. Using `implements` (Recommended)

Use `implements` to declare the label field:

```yaml
classes:
  OntologyTerm:
    attributes:
      id:
        identifier: true
      name:
        implements:
          - rdfs:label              # Declares this is the label field
```

### 2. Using `slot_uri`

Alternatively, use `slot_uri` to declare the field's semantic meaning:

```yaml
classes:
  OntologyTerm:
    attributes:
      id:
        identifier: true
      label:
        slot_uri: rdfs:label        # Also declares this is the label field
```

### Supported Label Properties

| Property | Description |
|----------|-------------|
| `rdfs:label` | Standard RDF label |
| `skos:prefLabel` | SKOS preferred label |
| `schema:name` | Schema.org name |
| `oboInOwl:hasExactSynonym` | OBO exact synonym |

### 3. Convention-Based (Fallback)

If no `implements` or `slot_uri` is declared, the plugin falls back to looking for a field named `label`:

```yaml
classes:
  OntologyTerm:
    attributes:
      id:
        identifier: true
      label:                        # Detected by convention
        range: string
```

## Nested Structure Validation

The plugin **recursively validates all nesting levels**, not just the top-level class. This is critical for real-world schemas.

### Example: Deeply Nested Structure

**Schema:**

```yaml
classes:
  Study:
    attributes:
      samples:
        range: Sample
        multivalued: true
        inlined_as_list: true

  Sample:
    attributes:
      annotations:
        range: Annotation
        multivalued: true
        inlined_as_list: true

  Annotation:
    attributes:
      term:
        range: OntologyTerm
        bindings:                   # ← Binding at nested level
          - binds_value_of: id
            range: AnnotationTermEnum
```

**Data:**

```yaml
samples:
  - annotations:
      - term:
          id: GO:0007049           # ← Validated!
          label: cell cycle
```

**Error message with path:**

```
ERROR: Value 'GO:9999999' not in enum 'AnnotationTermEnum'
  path: samples[0].annotations[1].term
  slot: term
  field: id
```

### Multivalued Slots

Both the parent slot and the nested objects can be multivalued:

```yaml
classes:
  Disease:
    attributes:
      affected_tissues:
        range: TissueDescriptor
        multivalued: true              # Multiple tissues
        inlined_as_list: true
        bindings:
          - binds_value_of: id
            range: AnatomyEnum
```

Each item in the list is validated independently:

```yaml
affected_tissues:
  - id: UBERON:0000955              # brain - validated
    label: brain
  - id: UBERON:0000948              # heart - validated
    label: heart
  - id: PIZZA:MARGHERITA           # INVALID - not anatomy
    label: delicious
```

## Complete Example

### Schema

```yaml
id: https://example.org/annotation-schema
name: annotation-schema
prefixes:
  GO: http://purl.obolibrary.org/obo/GO_
  rdfs: http://www.w3.org/2000/01/rdf-schema#
  linkml: https://w3id.org/linkml/

classes:
  GeneAnnotation:
    attributes:
      gene_id:
        range: string
        identifier: true
      process:
        range: GOTerm
        bindings:
          - binds_value_of: id
            range: BiologicalProcessEnum
      location:
        range: GOTerm
        bindings:
          - binds_value_of: id
            range: CellularComponentEnum

  GOTerm:
    attributes:
      id:
        identifier: true
      label:
        implements:
          - rdfs:label

enums:
  BiologicalProcessEnum:
    reachable_from:
      source_ontology: sqlite:obo:go
      source_nodes:
        - GO:0008150              # biological_process
      relationship_types:
        - rdfs:subClassOf

  CellularComponentEnum:
    reachable_from:
      source_ontology: sqlite:obo:go
      source_nodes:
        - GO:0005575              # cellular_component
      relationship_types:
        - rdfs:subClassOf
```

### Valid Data

```yaml
gene_id: BRCA1
process:
  id: GO:0007049                  # cell cycle - is a biological process
  label: cell cycle
location:
  id: GO:0005634                  # nucleus - is a cellular component
  label: nucleus
```

### Invalid Data

```yaml
gene_id: BRCA1
process:
  id: GO:0005634                  # nucleus - WRONG! Not a process
  label: nucleus
location:
  id: GO:0007049                  # cell cycle - WRONG! Not a component
  label: cell cycle
```

### Validation Commands

```bash
# Binding validation only
linkml-term-validator validate-data annotations.yaml -s schema.yaml -t GeneAnnotation
# Output: 2 binding errors (wrong enum values)

# With label validation
linkml-term-validator validate-data annotations.yaml -s schema.yaml -t GeneAnnotation --labels
# Also validates that labels match ontology
```

## Python API

```python
from linkml.validator import Validator
from linkml_runtime.loaders import yaml_loader
from linkml_term_validator.plugins import BindingValidationPlugin

# Create plugin with label validation
plugin = BindingValidationPlugin(
    oak_adapter_string="sqlite:obo:",
    validate_labels=True,          # Enable label checking
    cache_labels=True,
    cache_dir="cache",
)

# Create validator
validator = Validator(
    schema="schema.yaml",
    validation_plugins=[plugin]
)

# Validate
loader = yaml_loader.YamlLoader()
report = validator.validate_source(
    loader,
    "data.yaml",
    target_class="GeneAnnotation"
)

# Check results
for result in report.results:
    print(f"{result.severity.name}: {result.message}")
```

### Plugin Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `oak_adapter_string` | `str` | `"sqlite:obo:"` | Default OAK adapter |
| `oak_config_path` | `str \| None` | `None` | Path to OAK config file |
| `validate_labels` | `bool` | `False` | Also validate labels |
| `strict` | `bool` | `True` | Fail when term IDs not found in configured ontologies |
| `cache_labels` | `bool` | `True` | Enable file-based caching |
| `cache_dir` | `str` | `"cache"` | Cache directory |

## Error Messages

### Binding Violation (Static Enum)

```
ERROR: Value 'GO:0005634' not in enum 'BiologicalProcessEnum'
  path: process
  slot: process
  field: id
```

### Binding Violation (Dynamic Enum Closure)

When the term exists but is outside the ontology closure:

```
ERROR: Value 'GO:0005634' not in dynamic enum (expanded from ontology) 'BiologicalProcessEnum'
  path: process
  slot: process
  field: id
  allowed_values: 29688 terms
```

### Term Not Found (Strict Mode)

When a term ID doesn't exist in a configured ontology:

```
ERROR: Term 'GO:9999999' not found in ontology
  path: process
  slot: process
  field: id
  prefix: GO (configured in oak_config)
```

### Label Mismatch (with `--labels`)

```
ERROR: Label mismatch for GO:0007049
  Expected (from data): "Cell Cycle"
  Found (from ontology): "cell cycle"
  path: process.label
```

### Nested Path Example

```
ERROR: Value 'GO:9999999' not in enum 'CellularComponentEnum'
  path: samples[0].annotations[2].term
  slot: term
  field: id
```

## Anti-Hallucination Use Case

When validating AI-generated data, enable label validation to catch hallucinated terms:

```python
# AI might generate plausible-looking but wrong data:
# {
#   "id": "GO:0007049",
#   "label": "DNA repair"        # WRONG! Actual label is "cell cycle"
# }

plugin = BindingValidationPlugin(validate_labels=True)
# This will catch the mismatch!
```

See [Anti-Hallucination Guardrails](anti-hallucination.md) for more details.

## Combining with DynamicEnumPlugin

For comprehensive validation, use both plugins:

```python
from linkml.validator import Validator
from linkml_term_validator.plugins import (
    DynamicEnumPlugin,
    BindingValidationPlugin,
)

plugins = [
    DynamicEnumPlugin(),                      # Direct enum slots
    BindingValidationPlugin(validate_labels=True),  # Nested object bindings
]

validator = Validator(schema="schema.yaml", validation_plugins=plugins)
```

- **DynamicEnumPlugin**: Validates slots that directly use dynamic enum ranges
- **BindingValidationPlugin**: Validates fields within nested objects via bindings

## Common Patterns

### Reusable Term Class

```yaml
classes:
  Term:
    attributes:
      id:
        identifier: true
      label:
        implements:
          - rdfs:label

  GeneAnnotation:
    attributes:
      process:
        range: Term
        bindings:
          - binds_value_of: id
            range: ProcessEnum
      component:
        range: Term
        bindings:
          - binds_value_of: id
            range: ComponentEnum
```

### Slot Usage Override

Override bindings in subclasses:

```yaml
classes:
  Annotation:
    attributes:
      term:
        range: Term

  GeneAnnotation:
    is_a: Annotation
    slot_usage:
      term:
        bindings:
          - binds_value_of: id
            range: GOTermEnum
```

## See Also

- [Bindings Explained](bindings-explained.md) - Conceptual overview
- [Data Validation](data-validation.md) - Dynamic enum validation
- [Anti-Hallucination Guardrails](anti-hallucination.md) - AI validation
- [Plugin Reference](plugin-reference.md) - Complete API reference
