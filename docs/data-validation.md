# Data Validation Reference (Dynamic Enums)

This reference covers validation of **data values against dynamic enums**—ensuring that data values satisfy ontology-based constraints defined via `reachable_from`, `matches`, or `concepts`.

## Overview

Data validation uses the **DynamicEnumPlugin** to validate that data values satisfy dynamic enum constraints. Unlike static enums where valid values are listed explicitly, dynamic enums define valid values via ontology queries evaluated at validation time.

## CLI Usage

```bash
# Basic data validation
linkml-term-validator validate-data data.yaml --schema schema.yaml

# With target class
linkml-term-validator validate-data data.yaml -s schema.yaml -t Person

# With custom OAK configuration
linkml-term-validator validate-data data.yaml -s schema.yaml --oak-config oak_config.yaml

# Multiple data files
linkml-term-validator validate-data data1.yaml data2.yaml -s schema.yaml
```

### CLI Options

| Option | Description |
|--------|-------------|
| `--schema`, `-s` | Path to LinkML schema (required) |
| `--target-class`, `-t` | Target class for validation |
| `--oak-adapter` | OAK adapter string (default: `sqlite:obo:`) |
| `--oak-config` | Path to OAK configuration file |
| `--cache-dir` | Directory for cache files (default: `cache`) |
| `--verbose` / `-v` | Enable verbose output |

## Dynamic Enum Syntax

### `reachable_from` - Ontology Traversal

The most common pattern—allows any term reachable from source nodes via specified relationships:

```yaml
enums:
  NeuronTypeEnum:
    reachable_from:
      source_ontology: sqlite:obo:cl
      source_nodes:
        - CL:0000540           # neuron
      relationship_types:
        - rdfs:subClassOf
      include_self: false      # Default: false
```

#### Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `source_ontology` | Yes | OAK adapter string (e.g., `sqlite:obo:cl`, `obo:go`) |
| `source_nodes` | Yes | Root term(s) for traversal |
| `relationship_types` | Yes | Edge types to traverse |
| `include_self` | No | Include source nodes in results (default: `false`) |

#### Important Semantics

**Source nodes are EXCLUDED by default.** This means:

```yaml
# This enum allows descendants of CL:0000540, but NOT CL:0000540 itself
enums:
  NeuronTypeEnum:
    reachable_from:
      source_nodes:
        - CL:0000540  # neuron
```

```yaml
# Valid data - a specific neuron type (descendant)
cell_type: CL:0000117  # CNS neuron - is-a neuron

# INVALID - the source node itself
cell_type: CL:0000540  # neuron - excluded by default!
```

To include the source node, set `include_self: true`:

```yaml
enums:
  NeuronOrSubtype:
    reachable_from:
      source_nodes:
        - CL:0000540
      include_self: true      # Now CL:0000540 is also valid
```

### `matches` - Pattern Matching

Allows values matching a regex pattern:

```yaml
enums:
  GOTermPattern:
    matches:
      source_ontology: sqlite:obo:go
      pattern: "GO:[0-9]{7}"   # GO term format
```

### `concepts` - Explicit List

Allows a specific set of concepts (similar to static enum but defined differently):

```yaml
enums:
  SpecificTerms:
    concepts:
      - CL:0000540   # neuron
      - CL:0000746   # cardiomyocyte
      - CL:0000182   # hepatocyte
```

## Validation Process

For each slot in the data with a dynamic enum range:

1. **Extract the enum definition** from the schema
2. **Evaluate the constraint**:
   - For `reachable_from`: Query the ontology for descendants
   - For `matches`: Apply regex to the value
   - For `concepts`: Check membership
3. **Report violations** as ERROR severity

## Examples

### Complete Schema and Data Example

**Schema (`schema.yaml`):**

```yaml
id: https://example.org/cell-schema
name: cell-schema
prefixes:
  CL: http://purl.obolibrary.org/obo/CL_
  linkml: https://w3id.org/linkml/

classes:
  CellAnnotation:
    attributes:
      id:
        range: string
        identifier: true
      cell_type:
        range: NeuronTypeEnum      # Uses dynamic enum

enums:
  NeuronTypeEnum:
    description: Any type of neuron
    reachable_from:
      source_ontology: sqlite:obo:cl
      source_nodes:
        - CL:0000540              # neuron
      relationship_types:
        - rdfs:subClassOf
```

**Valid data (`valid_data.yaml`):**

```yaml
- id: cell-001
  cell_type: CL:0000117           # CNS neuron - valid

- id: cell-002
  cell_type: CL:0000111           # peripheral neuron - valid
```

**Invalid data (`invalid_data.yaml`):**

```yaml
- id: cell-001
  cell_type: CL:0000540           # neuron - INVALID (source node excluded)

- id: cell-002
  cell_type: GO:0008150           # biological_process - INVALID (wrong ontology)

- id: cell-003
  cell_type: CL:0000746           # cardiomyocyte - INVALID (not a neuron)
```

**Validation:**

```bash
linkml-term-validator validate-data valid_data.yaml -s schema.yaml -t CellAnnotation
# ✅ Valid

linkml-term-validator validate-data invalid_data.yaml -s schema.yaml -t CellAnnotation
# ❌ 3 errors
```

### Multiple Source Nodes

Allow terms from multiple branches:

```yaml
enums:
  CancerOrInfectiousDisease:
    reachable_from:
      source_ontology: sqlite:obo:mondo
      source_nodes:
        - MONDO:0004992           # cancer
        - MONDO:0005550           # infectious disease
      relationship_types:
        - rdfs:subClassOf
```

### Part-of Relationships

Use `BFO:0000050` (part-of) instead of `rdfs:subClassOf`:

```yaml
enums:
  BrainPartEnum:
    reachable_from:
      source_ontology: sqlite:obo:uberon
      source_nodes:
        - UBERON:0000955          # brain
      relationship_types:
        - BFO:0000050             # part-of
```

### Combined Relationships

Query multiple relationship types:

```yaml
enums:
  BrainStructureEnum:
    reachable_from:
      source_ontology: sqlite:obo:uberon
      source_nodes:
        - UBERON:0000955          # brain
      relationship_types:
        - rdfs:subClassOf         # subtypes
        - BFO:0000050             # parts
```

## Python API

```python
from linkml.validator import Validator
from linkml_runtime.loaders import yaml_loader
from linkml_term_validator.plugins import DynamicEnumPlugin

# Create plugin
plugin = DynamicEnumPlugin(
    oak_adapter_string="sqlite:obo:",
    cache_labels=True,
    cache_dir="cache",
)

# Create validator
validator = Validator(
    schema="schema.yaml",
    validation_plugins=[plugin]
)

# Validate with YamlLoader
loader = yaml_loader.YamlLoader()
report = validator.validate_source(
    loader,
    "data.yaml",
    target_class="CellAnnotation"
)

# Check results
if len(report.results) == 0:
    print("All dynamic enum constraints satisfied")
else:
    for result in report.results:
        print(f"{result.severity.name}: {result.message}")
```

### Plugin Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `oak_adapter_string` | `str` | `"sqlite:obo:"` | Default OAK adapter |
| `oak_config_path` | `str \| None` | `None` | Path to OAK config file |
| `cache_labels` | `bool` | `True` | Enable file-based caching |
| `cache_dir` | `str` | `"cache"` | Cache directory |

## Error Messages

### Value Not in Enum

```
ERROR: Value 'GO:0008150' is not valid for enum 'NeuronTypeEnum'
  slot: cell_type
  instance_index: 1
```

**Cause:** The value doesn't satisfy the `reachable_from` constraint—it's not a descendant of any source node.

### Source Node Used Directly

```
ERROR: Value 'CL:0000540' is not valid for enum 'NeuronTypeEnum'
  slot: cell_type
```

**Cause:** The source node is used directly, but `include_self` is `false` (default).

**Solution:** Either:
1. Use a descendant term instead
2. Set `include_self: true` in the enum definition

## Performance Considerations

### Caching

The plugin uses two-level caching:

1. **In-memory cache**: Speeds up repeated queries in the same session
2. **File-based cache**: Persists between sessions (stored in `cache_dir`)

For large-scale validation, consider:

```python
plugin = DynamicEnumPlugin(
    cache_labels=True,
    cache_dir="cache"      # Persistent cache directory
)
```

### Ontology Download

On first use, `sqlite:obo:` adapters download ontology databases. This may take time but only happens once:

```
Downloading GO database... (first run only)
```

For offline use, pre-download ontologies:

```bash
# Pre-download with OAK
runoak -i sqlite:obo:go info GO:0008150
runoak -i sqlite:obo:cl info CL:0000540
```

## Common Issues

### "Ontology not found"

```
ERROR: Could not load ontology for prefix 'CUSTOM'
```

**Solution:** Configure the prefix in `oak_config.yaml`:

```yaml
ontology_adapters:
  CUSTOM: simpleobo:path/to/custom.obo
```

### Slow Validation

**Cause:** Large ontology queries or network latency.

**Solutions:**
1. Enable caching (`cache_labels=True`)
2. Use `sqlite:obo:` adapters instead of `ols:`
3. Pre-download ontologies

### Multivalued Slots

Dynamic enum validation works with multivalued slots:

```yaml
classes:
  CellCollection:
    attributes:
      cell_types:
        range: CellTypeEnum
        multivalued: true       # Each value is validated
```

## See Also

- [Enumerations](enumerations.md) - Understanding dynamic enum concepts
- [Binding Validation](binding-validation.md) - Validating complex objects
- [Configuration](configuration.md) - OAK adapter configuration
- [Plugin Reference](plugin-reference.md) - Complete API reference
