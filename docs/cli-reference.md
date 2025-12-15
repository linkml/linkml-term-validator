# CLI Reference

Complete command-line interface reference for linkml-term-validator.

## Overview

linkml-term-validator provides a single command `linkml-term-validator` with two subcommands:

- `validate-schema` - Validate schema permissible values
- `validate-data` - Validate data against dynamic enums and bindings

## Installation

```bash
pip install linkml-term-validator
```

Or with `uv`:

```bash
uv add linkml-term-validator
```

## Global Options

These options apply to all subcommands:

```bash
linkml-term-validator [OPTIONS] COMMAND [ARGS]
```

| Option | Description |
|--------|-------------|
| `--help` | Show help message and exit |

## validate-schema

Validates that `meaning` fields in enum permissible values reference valid ontology terms with correct labels.

### Syntax

```bash
linkml-term-validator validate-schema [OPTIONS] SCHEMA_PATH
```

### Arguments

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `SCHEMA_PATH` | Path | Yes | Path to LinkML schema file (`.yaml`) |

### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--config PATH` | Path | None | Path to OAK config file (`oak_config.yaml`) for per-prefix adapter configuration |
| `--adapter TEXT` | String | `"sqlite:obo:"` | Default OAK adapter string (e.g., `sqlite:obo:`, `ols:`, `bioportal:`) |
| `--cache-dir PATH` | Path | `cache` | Directory for caching ontology labels |
| `--no-cache` | Flag | False | Disable file-based caching |
| `--strict` | Flag | False | Treat warnings as errors |
| `--verbose` | Flag | False | Show detailed validation information |
| `--help` | Flag | - | Show help message and exit |

### Examples

**Basic validation:**

```bash
linkml-term-validator validate-schema schema.yaml
```

**With custom config:**

```bash
linkml-term-validator validate-schema --config oak_config.yaml schema.yaml
```

**Strict mode (warnings become errors):**

```bash
linkml-term-validator validate-schema --strict schema.yaml
```

**Using OLS instead of SQLite:**

```bash
linkml-term-validator validate-schema --adapter ols: schema.yaml
```

**Disable caching:**

```bash
linkml-term-validator validate-schema --no-cache schema.yaml
```

**Verbose output:**

```bash
linkml-term-validator validate-schema --verbose schema.yaml
```

**Custom cache directory:**

```bash
linkml-term-validator validate-schema --cache-dir /tmp/ontology-cache schema.yaml
```

### Output

**Success (no issues):**

```
✅ Validation passed!

Validation Summary:
  Enums checked: 2
  Permissible values checked: 4
  Meanings validated: 4
  Issues found: 0
```

**Failure (with issues):**

```
❌ ERROR: Label mismatch for GO:0008150
    Enum: BiologicalProcessEnum
    Permissible value: BIOLOGICAL_PROCESS
    Expected label: biological process
    Found label: biological_process

Validation Summary:
  Enums checked: 2
  Permissible values checked: 4
  Meanings validated: 4
  Issues found: 1
    Errors: 1
    Warnings: 0
```

**With unknown prefixes:**

```
✅ Validation passed!

Validation Summary:
  Enums checked: 2
  Permissible values checked: 5
  Meanings validated: 4
  Issues found: 0

⚠️  Unknown prefixes encountered (validation skipped):
  - MY_CUSTOM
  - INTERNAL

Consider adding these to oak_config.yaml to enable validation.
```

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success - no validation errors |
| 1 | Failure - validation errors found |

## validate-data

Validates data instances against dynamic enums and binding constraints.

Accepts multiple data files - each is validated independently with a summary at the end.

### Syntax

```bash
linkml-term-validator validate-data [OPTIONS] DATA_PATHS...
```

### Arguments

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `DATA_PATHS` | Path(s) | Yes | One or more paths to data files (`.yaml`, `.json`) |

### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--schema PATH` | Path | **Required** | Path to LinkML schema file |
| `--target-class TEXT` | String | None | Target class name to validate against |
| `--config PATH` | Path | None | Path to OAK config file |
| `--adapter TEXT` | String | `"sqlite:obo:"` | Default OAK adapter string |
| `--cache-dir PATH` | Path | `cache` | Directory for caching ontology labels |
| `--cache-strategy TEXT` | String | `progressive` | Caching strategy for dynamic enums: `progressive` (lazy) or `greedy` (expand upfront) |
| `--no-cache` | Flag | False | Disable file-based caching |
| `--labels` | Flag | False | Validate that labels match ontology canonical labels |
| `--lenient/--no-lenient` | Flag | False | Lenient mode: don't fail when term IDs are not found in ontology |
| `--no-dynamic-enums` | Flag | False | Skip dynamic enum validation |
| `--no-bindings` | Flag | False | Skip binding constraint validation |
| `--verbose` | Flag | False | Show detailed validation information |
| `--help` | Flag | - | Show help message and exit |

### Examples

**Basic validation:**

```bash
linkml-term-validator validate-data data.yaml --schema schema.yaml
```

**With target class:**

```bash
linkml-term-validator validate-data data.yaml --schema schema.yaml --target-class Person
```

**With label validation:**

```bash
linkml-term-validator validate-data data.yaml --schema schema.yaml --labels
```

**With custom config:**

```bash
linkml-term-validator validate-data data.yaml \
  --schema schema.yaml \
  --config oak_config.yaml
```

**Only validate bindings (skip dynamic enums):**

```bash
linkml-term-validator validate-data data.yaml \
  --schema schema.yaml \
  --no-dynamic-enums
```

**Only validate dynamic enums (skip bindings):**

```bash
linkml-term-validator validate-data data.yaml \
  --schema schema.yaml \
  --no-bindings
```

**Validate multiple files:**

```bash
linkml-term-validator validate-data data1.yaml data2.yaml data3.yaml \
  --schema schema.yaml
```

**Validate all YAML files (shell glob):**

```bash
linkml-term-validator validate-data data/*.yaml \
  --schema schema.yaml
```

**With greedy caching (expand all terms upfront):**

```bash
linkml-term-validator validate-data data.yaml \
  --schema schema.yaml \
  --cache-strategy greedy
```

**Full validation with all options:**

```bash
linkml-term-validator validate-data data.yaml \
  --schema schema.yaml \
  --target-class GeneAnnotation \
  --config oak_config.yaml \
  --cache-dir cache \
  --cache-strategy progressive \
  --labels \
  --verbose
```

### Output

**Success (single file):**

```
✅ Validation passed
```

**Success (multiple files):**

```
✅ data1.yaml
✅ data2.yaml
✅ data3.yaml

✅ All 3 files passed validation
```

**Partial failure (multiple files):**

```
✅ data1.yaml

❌ data2.yaml - 2 issue(s):
  ❌ ERROR: Value 'GO:0005575' not in enum 'BiologicalProcessEnum'
  ❌ ERROR: Value 'CL:9999999' not in enum 'CellTypeEnum'

✅ data3.yaml

Summary: 1/3 files failed, 2 total issue(s)
```

**Failure (dynamic enum violation):**

```
❌ ERROR: Value 'GO:0005575' does not satisfy dynamic enum constraint
    Class: GeneAnnotation
    Slot: go_term.id
    Enum: BiologicalProcessEnum
    Expected: Descendant of GO:0008150 (biological_process)
    Found: GO:0005575 (cellular_component)

Validation Summary:
  Dynamic enums validated: 5
  Bindings validated: 3
  Issues found: 1
    Errors: 1
```

**Failure (label mismatch with --labels):**

```
❌ ERROR: Label mismatch for GO:0007049
    Class: GeneAnnotation
    Slot: go_term.label
    Expected label: cell cycle
    Found label: cell-division cycle

Validation Summary:
  Dynamic enums validated: 5
  Bindings validated: 3
  Label validations: 3
  Issues found: 1
    Errors: 1
```

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success - no validation errors |
| 1 | Failure - validation errors found |

## Common Workflows

### CI/CD Validation

**Schema validation in CI:**

```bash
#!/bin/bash
set -e  # Exit on error

echo "Validating LinkML schemas..."
linkml-term-validator validate-schema \
  --strict \
  --config oak_config.yaml \
  --cache-dir cache \
  src/schema/main.yaml

echo "✅ Schema validation passed"
```

**Data validation in CI:**

```bash
#!/bin/bash
set -e

echo "Validating curated data..."
linkml-term-validator validate-data \
  data/curated/*.yaml \
  --schema src/schema/main.yaml \
  --config oak_config.yaml \
  --labels \
  --cache-dir cache

echo "✅ Data validation passed"
```

### Local Development

**Quick schema check:**

```bash
linkml-term-validator validate-schema schema.yaml
```

**Validate with fresh cache:**

```bash
rm -rf cache/
linkml-term-validator validate-schema schema.yaml
```

**Test with OLS (no local downloads):**

```bash
linkml-term-validator validate-schema --adapter ols: --no-cache schema.yaml
```

### Debugging

**Verbose output:**

```bash
linkml-term-validator validate-schema --verbose schema.yaml
```

**Check specific data file:**

```bash
linkml-term-validator validate-data \
  data/problematic.yaml \
  --schema schema.yaml \
  --verbose
```

## Configuration Files

### oak_config.yaml

Controls which ontology adapters to use for different prefixes, and optionally the caching strategy:

```yaml
# Cache strategy (optional): "progressive" (default) or "greedy"
cache_strategy: progressive

ontology_adapters:
  GO: sqlite:obo:go
  CHEBI: sqlite:obo:chebi
  UBERON: sqlite:obo:uberon

  # Skip validation
  linkml: ""
  schema: ""
```

Use with:

```bash
linkml-term-validator validate-schema --config oak_config.yaml schema.yaml
```

See [Configuration](configuration.md) for details.

## Shell Completion

### Bash

```bash
# Add to ~/.bashrc
eval "$(_LINKML_TERM_VALIDATOR_COMPLETE=bash_source linkml-term-validator)"
```

### Zsh

```bash
# Add to ~/.zshrc
eval "$(_LINKML_TERM_VALIDATOR_COMPLETE=zsh_source linkml-term-validator)"
```

### Fish

```bash
# Add to ~/.config/fish/config.fish
_LINKML_TERM_VALIDATOR_COMPLETE=fish_source linkml-term-validator | source
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `BIOPORTAL_API_KEY` | API key for BioPortal adapter | None |
| `OAK_CACHE_DIR` | Default cache directory for OAK | `~/.data/oaklib` |

## Troubleshooting

### Command not found

**Problem:**
```bash
linkml-term-validator: command not found
```

**Solution:**
Ensure the package is installed and your PATH is configured:

```bash
pip install linkml-term-validator
which linkml-term-validator
```

### Unknown prefixes

**Problem:**
```
⚠️  Unknown prefixes encountered (validation skipped):
  - MY_ONTOLOGY
```

**Solution:**
Add the prefix to your `oak_config.yaml`:

```yaml
ontology_adapters:
  MY_ONTOLOGY: sqlite:obo:my_ontology
```

Or use a local OBO file:

```yaml
ontology_adapters:
  MY_ONTOLOGY: simpleobo:path/to/ontology.obo
```

### Slow validation

**Problem:**
Validation takes a long time on first run.

**Solution:**
This is expected when using `sqlite:obo:` adapter for the first time. OAK is downloading and building the ontology database. Subsequent runs will be fast due to caching.

To speed up development:
- Use `simpleobo:` adapter with local OBO files for testing
- Cache the `cache/` directory in CI/CD
- Use `ols:` adapter to avoid local downloads (slower per query but no initial download)

### Stale cache

**Problem:**
Validation shows old labels even though ontology has been updated.

**Solution:**
Clear the cache:

```bash
rm -rf cache/
linkml-term-validator validate-schema schema.yaml
```

Or disable caching:

```bash
linkml-term-validator validate-schema --no-cache schema.yaml
```

## See Also

- [Configuration](configuration.md) - Detailed configuration options
- [Plugin Reference](plugin-reference.md) - Python API documentation
- [Tutorials](notebooks/01_getting_started.ipynb) - Interactive tutorials
- [Validation Types](validation-types.md) - Understanding validation types
- [Anti-Hallucination Guardrails](anti-hallucination.md) - Preventing AI hallucinations
