# How-To: Validate Bindings with Dynamic Enums

This guide shows you how to validate ontology term bindings in nested objects, including validation against dynamic enum closures and catching fabricated term IDs.

## Prerequisites

- linkml-term-validator installed
- A LinkML schema with bindings
- Data to validate
- (Optional) An oak_config.yaml for custom ontology adapters

## Goal

Validate that:

1. Term IDs in nested objects belong to the correct ontology branch (closure validation)
2. Term IDs actually exist in the ontology (strict mode)
3. (Optionally) Labels match the ontology's canonical labels

## Step 1: Define Your Schema with Bindings

Create a schema with a reusable term class and bindings that constrain the `id` field to a dynamic enum:

```yaml
# schema.yaml
id: https://example.org/gene-annotations
name: gene-annotations
prefixes:
  GO: http://purl.obolibrary.org/obo/GO_
  linkml: https://w3id.org/linkml/

default_prefix: gene-annotations
default_range: string

classes:
  GeneAnnotation:
    tree_root: true
    attributes:
      gene_id:
        identifier: true
      process:
        description: Biological process annotation
        range: Term
        inlined: true
        bindings:
          - binds_value_of: id
            range: BiologicalProcessEnum

  Term:
    description: Reusable ontology term class
    attributes:
      id:
        description: CURIE (e.g., GO:0007049)
      label:
        description: Human-readable label
        implements:
          - rdfs:label

enums:
  BiologicalProcessEnum:
    description: GO biological processes
    reachable_from:
      source_ontology: sqlite:obo:go
      source_nodes:
        - GO:0008150  # biological_process
      relationship_types:
        - rdfs:subClassOf
      include_self: true
```

Key points:

- `bindings` on the `process` slot constrains `term.id` to `BiologicalProcessEnum`
- `reachable_from` defines a dynamic enum based on ontology traversal
- `implements: [rdfs:label]` marks the label field for validation

## Step 2: Create Test Data

**Valid data** (`valid_data.yaml`):

```yaml
gene_id: BRCA1
process:
  id: GO:0007049    # cell cycle - IS a biological process
  label: cell cycle
```

**Invalid data - wrong branch** (`wrong_branch.yaml`):

```yaml
gene_id: BRCA1
process:
  id: GO:0005634    # nucleus - NOT a biological process (it's a cellular component)
  label: nucleus
```

**Invalid data - fabricated ID** (`fabricated.yaml`):

```yaml
gene_id: BRCA1
process:
  id: GO:9999999    # Doesn't exist in GO!
  label: made up term
```

## Step 3: Run Validation

### Basic Binding Validation

```bash
# Validates against the dynamic enum closure
linkml-term-validator validate-data valid_data.yaml -s schema.yaml -t GeneAnnotation
```

Output:
```
Validation passed
```

### Catch Wrong Branch

```bash
linkml-term-validator validate-data wrong_branch.yaml -s schema.yaml -t GeneAnnotation
```

Output:
```
Validation failed with 1 issue(s):

  ERROR: Value 'GO:0005634' not in dynamic enum (expanded from ontology) 'BiologicalProcessEnum'
      path: process
      slot: process
      field: id
      allowed_values: 29688 terms
```

### Catch Fabricated IDs (Strict Mode)

By default, strict mode is enabled. This catches fabricated term IDs:

```bash
linkml-term-validator validate-data fabricated.yaml -s schema.yaml -t GeneAnnotation
```

Output:
```
Validation failed with 2 issue(s):

  ERROR: Value 'GO:9999999' not in dynamic enum (expanded from ontology) 'BiologicalProcessEnum'
      ...
  ERROR: Term 'GO:9999999' not found in ontology
      ...
      prefix: GO (configured in oak_config)
```

### Disable Strict Mode (Lenient)

If you want to skip the existence check:

```bash
linkml-term-validator validate-data fabricated.yaml -s schema.yaml -t GeneAnnotation --lenient
```

This will only report the closure error, not the "term not found" error.

## Step 4: Add Label Validation (Anti-Hallucination)

Enable label validation to catch mismatched labels:

```bash
linkml-term-validator validate-data data.yaml -s schema.yaml -t GeneAnnotation --labels
```

If the data has:
```yaml
process:
  id: GO:0007049
  label: DNA repair  # WRONG! Should be "cell cycle"
```

Output:
```
WARNING: Label mismatch for GO:0007049
  Expected: "DNA repair"
  Ontology: "cell cycle"
```

## Step 5: Use Custom Ontology Configuration

For local ontologies or specific adapters, create an `oak_config.yaml`:

```yaml
# oak_config.yaml
ontology_adapters:
  GO: sqlite:obo:go
  HP: sqlite:obo:hp
  MONDO: sqlite:obo:mondo
  # For local OBO files:
  # TEST: simpleobo:path/to/test.obo
```

Then reference it:

```bash
linkml-term-validator validate-data data.yaml -s schema.yaml -t GeneAnnotation \
    --config oak_config.yaml
```

## Python API

```python
from linkml.validator import Validator
from linkml.validator.loaders import YamlLoader
from linkml_term_validator.plugins import BindingValidationPlugin

# Create plugin with options
plugin = BindingValidationPlugin(
    validate_labels=True,      # Check labels match ontology
    strict=True,               # Fail on non-existent terms (default)
    cache_labels=True,         # Cache lookups to disk
    cache_dir="cache",
    oak_config_path="oak_config.yaml",
)

# Create validator
validator = Validator(
    schema="schema.yaml",
    validation_plugins=[plugin]
)

# Validate
loader = YamlLoader("data.yaml")
report = validator.validate_source(loader, target_class="GeneAnnotation")

# Check results
if len(report.results) == 0:
    print("Validation passed!")
else:
    for result in report.results:
        print(f"{result.severity.name}: {result.message}")
```

## Common Issues and Solutions

### Issue: "Term not found" for valid terms

**Cause**: The prefix isn't configured in oak_config.yaml

**Solution**: Add the prefix to your oak_config.yaml:
```yaml
ontology_adapters:
  GO: sqlite:obo:go  # Add this line
```

### Issue: Dynamic enum expansion is slow

**Cause**: Large ontologies take time to traverse

**Solutions**:
1. Enable caching with `--cache-dir cache`
2. Use more specific source nodes (lower in the hierarchy)
3. Consider using a local sqlite database instead of downloading

### Issue: Unknown prefix warnings

**Cause**: Data contains prefixes not in your oak_config.yaml

**Solution**: Either add the prefix to oak_config.yaml or ignore if expected:
```yaml
ontology_adapters:
  KNOWN_PREFIX: sqlite:obo:known
  # Unknown prefixes will be skipped with a warning
```

## Validation Summary

| Validation Type | What it Catches | Flag |
|----------------|-----------------|------|
| Binding + Dynamic Enum | Terms outside ontology branch | (default) |
| Strict Mode | Fabricated/non-existent term IDs | `--no-lenient` (default) |
| Label Validation | Mismatched labels | `--labels` |

## Next Steps

- [Binding Validation Reference](binding-validation.md) - Complete reference
- [Bindings Explained](bindings-explained.md) - Conceptual overview
- [Anti-Hallucination Guardrails](anti-hallucination.md) - Using validation with AI
- [Configuration](configuration.md) - oak_config.yaml options
