# Configuration

This page details how to configure linkml-term-validator to control ontology access and validation behavior.

## OAK Configuration File

The primary way to configure ontology access is through an `oak_config.yaml` file that maps ontology prefixes to OAK adapter strings.

### Basic Structure

```yaml
# Cache strategy for dynamic enums (optional)
cache_strategy: progressive  # or "greedy"

# Ontology adapter mappings
ontology_adapters:
  # Prefix: adapter_string
  GO: sqlite:obo:go
  CHEBI: sqlite:obo:chebi
  UBERON: sqlite:obo:uberon
```

### Using the Config File

**CLI:**

```bash
linkml-term-validator validate-schema --config oak_config.yaml schema.yaml
linkml-term-validator validate-data --config oak_config.yaml data.yaml --schema schema.yaml
```

**Python API:**

```python
from linkml_term_validator.plugins import PermissibleValueMeaningPlugin

plugin = PermissibleValueMeaningPlugin(oak_config_path="oak_config.yaml")
```

### Important Behavior

When using `oak_config.yaml`:

- **ONLY prefixes listed in the config are validated**
- Unlisted prefixes are tracked as "unknown" and reported at the end
- Unknown prefixes are NOT validated (no errors/warnings for these terms)

This allows you to control which ontologies require validation and skip validation for prefixes you don't care about.

## OAK Adapter Strings

OAK adapter strings specify how to access an ontology. Different adapters support different backends and use cases.

### Common Adapters

#### sqlite:obo: (Default, Recommended)

Uses pre-downloaded OBO ontologies in SQLite format. Fast, works offline, caches locally.

**Usage:**

```yaml
ontology_adapters:
  GO: sqlite:obo:go
  CHEBI: sqlite:obo:chebi
  UBERON: sqlite:obo:uberon
```

**First-time setup:**

The first time you validate with a new ontology, OAK will download and build the SQLite database. This can take a few minutes but only happens once.

**Pros:**
- Fast queries (SQLite is efficient)
- Works offline after initial download
- No rate limiting
- Consistent results

**Cons:**
- Requires initial download
- Takes disk space (~100MB-1GB per ontology)
- Need to manually update for latest ontology versions

#### simpleobo:

Uses local OBO files directly. Good for unit tests and custom ontologies.

**Usage:**

```yaml
ontology_adapters:
  MY_CUSTOM: simpleobo:path/to/my_ontology.obo
  TEST: simpleobo:tests/data/test_ontology.obo
```

**Pros:**
- Fast, lightweight
- Perfect for testing
- No network access required
- Full control over ontology version

**Cons:**
- Limited query capabilities compared to SQLite
- Only works with OBO format
- Need to manage OBO files yourself

#### ols:

Uses the Ontology Lookup Service (EBI). Online service, no local downloads.

**Usage:**

```yaml
ontology_adapters:
  GO: ols:
  CHEBI: ols:
  UBERON: ols:
```

**Pros:**
- No local downloads
- Always uses latest ontology versions
- Works out-of-the-box

**Cons:**
- Requires internet connection
- Slower than local adapters
- Subject to rate limiting
- Service availability dependence

#### bioportal:

Uses NCBO BioPortal. Requires API key.

**Usage:**

```yaml
ontology_adapters:
  GO: bioportal:
  CHEBI: bioportal:
```

**Setup:**

Set `BIOPORTAL_API_KEY` environment variable:

```bash
export BIOPORTAL_API_KEY=your-api-key-here
linkml-term-validator validate-schema --config oak_config.yaml schema.yaml
```

**Pros:**
- Access to many ontologies
- Well-maintained service
- Good coverage

**Cons:**
- Requires API key registration
- Subject to rate limiting
- Requires internet connection

### Skipping Validation for Specific Prefixes

To skip validation for certain prefixes, use an empty string:

```yaml
ontology_adapters:
  GO: sqlite:obo:go
  CHEBI: sqlite:obo:chebi

  # Skip validation for these prefixes
  xsd: ""
  rdf: ""
  linkml: ""
  schema: ""
```

Terms with these prefixes will not be validated and won't generate warnings.

## Default Behavior (No Config File)

If you don't provide an `oak_config.yaml` file, the validator uses intelligent defaults:

**Default adapter:** `sqlite:obo:`

The validator automatically creates per-prefix adapters based on the prefix in the CURIE:

- `GO:0008150` → uses `sqlite:obo:go`
- `CHEBI:15377` → uses `sqlite:obo:chebi`
- `UBERON:0000468` → uses `sqlite:obo:uberon`
- `CL:0000540` → uses `sqlite:obo:cl`

This works for any OBO ontology that OAK knows about.

**When this works well:**

- You're using standard OBO ontologies (GO, CHEBI, UBERON, CL, etc.)
- You want all ontology terms validated
- You're okay with initial download times

**When you need a config file:**

- You want to use different adapters (e.g., OLS instead of SQLite)
- You want to skip validation for certain prefixes
- You have custom ontologies not in OBO
- You want to use local OBO files for testing

## Command-Line Options

### Cache Control

**Cache directory:**

```bash
linkml-term-validator validate-schema --cache-dir /path/to/cache schema.yaml
```

Default: `./cache`

**Disable caching:**

```bash
linkml-term-validator validate-schema --no-cache schema.yaml
```

This forces fresh lookups from the ontology source every time. Useful for testing or when you want guaranteed fresh data.

**Cache strategy (data validation):**

```bash
# Progressive (default) - validates lazily, caches valid terms as encountered
linkml-term-validator validate-data --cache-strategy progressive data.yaml -s schema.yaml

# Greedy - expands entire enum upfront and caches all terms
linkml-term-validator validate-data --cache-strategy greedy data.yaml -s schema.yaml
```

See [Caching](caching.md#enum-caching-strategies) for details on when to use each strategy.

### Validation Behavior

**Strict mode:**

```bash
linkml-term-validator validate-schema --strict schema.yaml
```

Treats all warnings as errors. Useful for CI/CD pipelines where you want strict enforcement.

**Verbose output:**

```bash
linkml-term-validator validate-schema --verbose schema.yaml
```

Shows detailed information about what's being validated and any issues encountered.

**Label validation (data validation only):**

```bash
linkml-term-validator validate-data --labels data.yaml --schema schema.yaml
```

Also validates that labels in the data match the canonical labels from the ontology.

**Selective plugin usage (data validation only):**

```bash
# Only validate dynamic enums, skip bindings
linkml-term-validator validate-data --no-bindings data.yaml --schema schema.yaml

# Only validate bindings, skip dynamic enums
linkml-term-validator validate-data --no-dynamic-enums data.yaml --schema schema.yaml
```

## Example Configurations

### Standard OBO Ontologies

```yaml
# oak_config.yaml
ontology_adapters:
  GO: sqlite:obo:go
  CHEBI: sqlite:obo:chebi
  UBERON: sqlite:obo:uberon
  CL: sqlite:obo:cl
  MONDO: sqlite:obo:mondo
  HP: sqlite:obo:hp
```

### Mixed Online/Offline

```yaml
# oak_config.yaml
ontology_adapters:
  # Use SQLite for commonly-used ontologies
  GO: sqlite:obo:go
  CHEBI: sqlite:obo:chebi

  # Use OLS for rarely-used ontologies (avoid downloads)
  FYPO: ols:
  WBPhenotype: ols:

  # Skip validation for metadata prefixes
  linkml: ""
  schema: ""
```

### Local Testing Setup

```yaml
# oak_config.yaml (for tests)
ontology_adapters:
  # Use local OBO file for fast, offline testing
  TEST: simpleobo:tests/data/test_ontology.obo

  # Real ontologies for integration tests
  GO: sqlite:obo:go
```

### Custom Ontology

```yaml
# oak_config.yaml
ontology_adapters:
  # Standard ontologies
  GO: sqlite:obo:go
  CHEBI: sqlite:obo:chebi

  # Your custom ontology from local file
  MY_ORG: simpleobo:ontologies/my_org_ontology.obo
```

### CI/CD Pipeline

For CI/CD, you typically want:

- **Strict mode** - treat warnings as errors
- **Configured adapters** - control which ontologies are validated
- **Caching** - cache between runs for speed

**GitHub Actions example:**

```yaml
# .github/workflows/validate.yml
name: Validate Schemas

on: [push, pull_request]

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.10'

      - name: Install dependencies
        run: |
          pip install linkml-term-validator

      - name: Cache ontology databases
        uses: actions/cache@v3
        with:
          path: cache
          key: ontology-cache-${{ hashFiles('oak_config.yaml') }}

      - name: Validate schemas
        run: |
          linkml-term-validator validate-schema \
            --strict \
            --config oak_config.yaml \
            --cache-dir cache \
            src/schema/
```

## Plugin Configuration (Python API)

When using plugins programmatically, you can configure them via constructor arguments:

### PermissibleValueMeaningPlugin

```python
from linkml_term_validator.plugins import PermissibleValueMeaningPlugin

plugin = PermissibleValueMeaningPlugin(
    oak_adapter_string="sqlite:obo:",  # Default adapter
    oak_config_path="oak_config.yaml",  # Optional config file
    strict_mode=False,                   # Treat warnings as errors
    cache_labels=True,                   # Enable file-based caching
    cache_dir="cache",                   # Cache directory
)
```

### DynamicEnumPlugin

```python
from linkml_term_validator.plugins import DynamicEnumPlugin
from linkml_term_validator.models import CacheStrategy

plugin = DynamicEnumPlugin(
    oak_adapter_string="sqlite:obo:",
    oak_config_path="oak_config.yaml",
    cache_labels=True,
    cache_dir="cache",
    cache_strategy=CacheStrategy.PROGRESSIVE,  # or GREEDY
)
```

### BindingValidationPlugin

```python
from linkml_term_validator.plugins import BindingValidationPlugin
from linkml_term_validator.models import CacheStrategy

plugin = BindingValidationPlugin(
    oak_adapter_string="sqlite:obo:",
    oak_config_path="oak_config.yaml",
    validate_labels=True,  # Also check labels match ontology
    cache_labels=True,
    cache_dir="cache",
    cache_strategy=CacheStrategy.PROGRESSIVE,  # or GREEDY
)
```

## linkml-validate Configuration

When using plugins with `linkml-validate`, configure them via YAML:

```yaml
# validation_config.yaml
schema: schema.yaml
target_class: Person

data_sources:
  - data.yaml

plugins:
  JsonschemaValidationPlugin:
    closed: true

  "linkml_term_validator.plugins.DynamicEnumPlugin":
    oak_adapter_string: "sqlite:obo:"
    cache_labels: true
    cache_dir: cache
    cache_strategy: progressive  # or "greedy"
    oak_config_path: oak_config.yaml

  "linkml_term_validator.plugins.BindingValidationPlugin":
    oak_adapter_string: "sqlite:obo:"
    validate_labels: true
    cache_labels: true
    cache_dir: cache
    cache_strategy: progressive  # or "greedy"
    oak_config_path: oak_config.yaml
```

Then:

```bash
linkml-validate --config validation_config.yaml
```

## Troubleshooting

### "Unknown prefix" warnings

**Symptom:** You see warnings like:

```
⚠️  Unknown prefixes encountered (validation skipped):
  - MY_CUSTOM
  - SOME_ONTOLOGY
```

**Cause:** These prefixes aren't in your `oak_config.yaml` (or you're not using a config file and OAK doesn't recognize them).

**Solution:** Add them to your config file:

```yaml
ontology_adapters:
  MY_CUSTOM: simpleobo:path/to/ontology.obo
  SOME_ONTOLOGY: sqlite:obo:some_ontology
```

Or, if you don't want to validate them, explicitly skip:

```yaml
ontology_adapters:
  MY_CUSTOM: ""
```

### "Downloading my.db.gz" errors

**Symptom:** Long download times or errors downloading ontology databases.

**Cause:** Using `sqlite:obo:` adapter for the first time with a new ontology.

**Solutions:**

1. **Be patient** - first download can take 5-10 minutes for large ontologies
2. **Use local files for testing** - switch to `simpleobo:` for unit tests
3. **Pre-download in CI/CD** - cache the `cache/` directory between runs
4. **Use OLS** - switch to `ols:` adapter to avoid downloads

### Stale cache data

**Symptom:** Validation shows old labels even though ontology has been updated.

**Cause:** File-based cache hasn't been refreshed.

**Solution:**

```bash
# Clear cache and re-validate
rm -rf cache/
linkml-term-validator validate-schema schema.yaml
```

Or use `--no-cache` to bypass caching:

```bash
linkml-term-validator validate-schema --no-cache schema.yaml
```

## See Also

- [CLI Reference](cli-reference.md) - Complete command-line documentation
- [Plugin Reference](plugin-reference.md) - Python API documentation
- [Ontology Access](ontology-access.md) - How OAK adapters work
- [Caching](caching.md) - Understanding the caching system
