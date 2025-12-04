# Schema Validation Reference

This reference covers validation of **permissible value meanings** in LinkML schemas—ensuring that `meaning` CURIEs in static enums reference valid ontology terms with correct labels.

## Overview

Schema validation uses the **PermissibleValueMeaningPlugin** to validate that:

1. Each `meaning` CURIE exists in the source ontology
2. The canonical label matches an expected label from the schema

This is performed at **schema authoring time**, not data validation time.

## CLI Usage

```bash
# Basic schema validation
linkml-term-validator validate-schema schema.yaml

# With strict mode (treat warnings as errors)
linkml-term-validator validate-schema --strict schema.yaml

# With custom OAK configuration
linkml-term-validator validate-schema --oak-config oak_config.yaml schema.yaml

# With OLS adapter (online lookup)
linkml-term-validator validate-schema --oak-adapter "ols:" schema.yaml
```

### CLI Options

| Option | Description |
|--------|-------------|
| `--strict` | Treat warnings as errors |
| `--oak-adapter` | OAK adapter string (default: `sqlite:obo:`) |
| `--oak-config` | Path to OAK configuration file |
| `--cache-dir` | Directory for cache files (default: `cache`) |
| `--verbose` / `-v` | Enable verbose output |

## What Gets Validated

### Permissible Value Structure

For each enum in the schema with permissible values containing `meaning` fields:

```yaml
enums:
  BiologicalProcessEnum:
    permissible_values:
      CELL_CYCLE:
        title: cell cycle           # ← Expected label source
        meaning: GO:0007049         # ← CURIE to validate
      DNA_REPLICATION:
        title: DNA replication
        meaning: GO:0006260
        aliases:
          - DNA synthesis           # ← Alternative expected labels
```

### Validation Steps

1. **Term Existence**: The CURIE (e.g., `GO:0007049`) is looked up in the ontology
2. **Label Retrieval**: The canonical label is retrieved from the ontology
3. **Label Matching**: The canonical label is compared against expected labels

### Expected Label Sources

The validator checks the canonical label against multiple sources (in order):

1. **Permissible value name** (e.g., `CELL_CYCLE` → normalized to "cell cycle")
2. **`title` field** (e.g., `title: cell cycle`)
3. **`description` field**
4. **`aliases` list** (e.g., `aliases: [DNA synthesis, DNA synth]`)
5. **Annotation values** for tags: `label`, `display_name`, `synonym`

### Label Normalization

Before comparison, labels are normalized:

- Lowercased
- Underscores converted to spaces
- Multiple whitespace collapsed to single space
- Leading/trailing whitespace trimmed

This means `BIOLOGICAL_PROCESS`, `biological process`, and `Biological Process` all match.

## Severity Levels

| Severity | Condition |
|----------|-----------|
| **ERROR** | Label mismatch for a configured prefix (in strict mode: any prefix) |
| **ERROR** | Term does not exist in a configured ontology |
| **WARN** | Label mismatch for an unconfigured prefix |
| **INFO** | Unconfigured prefix encountered |

### Configured vs Unconfigured Prefixes

A **configured prefix** is one explicitly mapped in your `oak_config.yaml`:

```yaml
# oak_config.yaml
ontology_adapters:
  GO: sqlite:obo:go    # GO is configured
  CL: sqlite:obo:cl    # CL is configured
  # HP is NOT configured - will use default adapter
```

Errors for configured prefixes are always raised. For unconfigured prefixes, mismatches are warnings (unless `--strict` is used).

## Examples

### Basic Schema

```yaml
id: https://example.org/my-schema
name: my-schema
prefixes:
  GO: http://purl.obolibrary.org/obo/GO_
  linkml: https://w3id.org/linkml/

enums:
  ProcessEnum:
    permissible_values:
      CELL_CYCLE:
        title: cell cycle
        meaning: GO:0007049
      APOPTOSIS:
        title: apoptotic process
        meaning: GO:0006915
```

**Validation:**

```bash
linkml-term-validator validate-schema schema.yaml
```

**Output (valid):**

```
✅ Validated 2 permissible value meanings
```

### Schema with Error

```yaml
enums:
  ProcessEnum:
    permissible_values:
      WRONG_LABEL:
        title: wrong label here     # This won't match!
        meaning: GO:0007049         # Canonical label is "cell cycle"
```

**Output:**

```
❌ ERROR: Label mismatch for GO:0007049
   Expected: "wrong label here"
   Found: "cell cycle"
   Permissible value: WRONG_LABEL
```

### Schema with Aliases

Use aliases when your preferred name differs from the ontology label:

```yaml
enums:
  ProcessEnum:
    permissible_values:
      PROGRAMMED_CELL_DEATH:
        title: Programmed Cell Death    # Human-friendly name
        meaning: GO:0006915
        aliases:
          - apoptotic process           # Matches ontology label
          - apoptosis
```

This will validate successfully because "apoptotic process" is in the aliases.

## Python API

```python
from linkml.validator import Validator
from linkml_term_validator.plugins import PermissibleValueMeaningPlugin

# Create plugin
plugin = PermissibleValueMeaningPlugin(
    oak_adapter_string="sqlite:obo:",
    strict_mode=False,
    cache_labels=True,
    cache_dir="cache",
)

# Create validator
validator = Validator(
    schema="schema.yaml",
    validation_plugins=[plugin]
)

# Validate the schema file itself
report = validator.validate_file("schema.yaml")

# Check results
if len(report.results) == 0:
    print("All permissible values validated successfully")
else:
    for result in report.results:
        print(f"{result.severity.name}: {result.message}")
```

### Plugin Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `oak_adapter_string` | `str` | `"sqlite:obo:"` | Default OAK adapter |
| `oak_config_path` | `str \| None` | `None` | Path to OAK config file |
| `strict_mode` | `bool` | `False` | Treat warnings as errors |
| `cache_labels` | `bool` | `True` | Enable file-based caching |
| `cache_dir` | `str` | `"cache"` | Cache directory |

## Common Issues

### "Term not found" Error

```
ERROR: Term GO:9999999 not found in ontology
```

**Causes:**
- Typo in the CURIE
- Using an obsolete term ID
- Ontology not available/configured

**Solutions:**
1. Verify the CURIE exists in the ontology (use [OLS](https://www.ebi.ac.uk/ols/))
2. Check for typos
3. Configure the correct OAK adapter

### "Label mismatch" Warning

```
WARN: Label mismatch for GO:0007049
  Expected: "Cell Cycle"
  Found: "cell cycle"
```

**This is usually fine** - the validation is case-insensitive after normalization. If you see this warning, double-check your `title` or add the exact label as an alias.

### "Unknown prefix" Info

```
INFO: Unknown prefix CUSTOM - skipping validation
```

**Causes:**
- Using a custom/internal prefix not in standard ontologies
- Prefix not configured in `oak_config.yaml`

**Solutions:**
1. Add the prefix to your `oak_config.yaml` if it maps to an ontology
2. Ignore if it's intentionally internal

## Best Practices

1. **Use `title` for human-readable names**: The `title` field is your primary expected label
2. **Use `aliases` for synonyms**: Add the ontology's canonical label as an alias if your preferred name differs
3. **Configure known prefixes**: Create an `oak_config.yaml` for prefixes you use frequently
4. **Run in CI/CD**: Add schema validation to your CI pipeline to catch issues early
5. **Use strict mode for releases**: Use `--strict` when preparing a schema for release

## See Also

- [Enumerations](enumerations.md) - Understanding static vs dynamic enums
- [Configuration](configuration.md) - OAK adapter configuration
- [Plugin Reference](plugin-reference.md) - Complete API reference
- [CLI Reference](cli-reference.md) - All CLI options
