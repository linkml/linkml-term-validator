# Ontology Access

All linkml-term-validator plugins use [OAK (Ontology Access Kit)](https://github.com/INCATools/ontology-access-kit) to access ontology data.

## OAK Adapters

An **adapter** is OAK's abstraction for accessing ontology sources. Different adapters support different backends:

- **`sqlite:obo:`** - Pre-downloaded OBO ontologies in SQLite format (default, fast, offline)
- **`simpleobo:`** - Simple local OBO files (good for unit tests)
- **`ols:`** - Ontology Lookup Service (online, no local download required)
- **`bioportal:`** - NCBO BioPortal (requires API key)
- **`ubergraph:`** - Ubergraph SPARQL endpoint

## Default Behavior

Without configuration, the validator uses `sqlite:obo:` as the default adapter, which automatically creates per-prefix adapters:

- `GO:0008150` → uses `sqlite:obo:go`
- `CHEBI:15377` → uses `sqlite:obo:chebi`
- `UBERON:0000468` → uses `sqlite:obo:uberon`

This works for any OBO ontology that has been downloaded via OAK.

## Per-Prefix Configuration

You can override the adapter for specific prefixes using an `oak_config.yaml` file:

```yaml
ontology_adapters:
  GO: sqlite:obo:go           # Use local GO database
  CHEBI: ols:                 # Use OLS for CHEBI
  MY_CUSTOM: simpleobo:my_ontology.obo  # Use local OBO file
  SKIP_THIS: ""               # Skip validation for this prefix
```

**Important**: When using `oak_config.yaml`, ONLY the prefixes listed in the config will be validated. Unlisted prefixes are reported as "unknown."

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

## Adapter Details

### sqlite:obo: (Recommended)

Uses pre-downloaded OBO ontologies in SQLite format.

**Pros:**
- Fast queries (SQLite is efficient)
- Works offline after initial download
- No rate limiting
- Consistent results

**Cons:**
- Requires initial download
- Takes disk space (~100MB-1GB per ontology)
- Need to manually update for latest ontology versions

**First-time setup:**

The first time you validate with a new ontology, OAK will download and build the SQLite database. This can take a few minutes but only happens once.

### simpleobo:

Uses local OBO files directly. Ideal for unit tests and custom ontologies.

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

### ols:

Uses the Ontology Lookup Service (EBI). Online service, no local downloads.

**Usage:**

```yaml
ontology_adapters:
  GO: ols:
  CHEBI: ols:
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

### bioportal:

Uses NCBO BioPortal. Requires API key.

**Setup:**

Set `BIOPORTAL_API_KEY` environment variable:

```bash
export BIOPORTAL_API_KEY=your-api-key-here
linkml-term-validator validate-schema --config oak_config.yaml schema.yaml
```

**Usage:**

```yaml
ontology_adapters:
  GO: bioportal:
  CHEBI: bioportal:
```

## Unknown Prefixes

When the validator encounters a prefix it doesn't recognize (not in `oak_config.yaml` or not a known OBO ontology), it:

1. Logs a warning
2. Skips validation for that term
3. Reports unknown prefixes at the end of validation

**Example output:**

```
⚠️  Unknown prefixes encountered (validation skipped):
  - MY_CUSTOM
  - INTERNAL

Consider adding these to oak_config.yaml to enable validation.
```

**To fix:** Add them to your `oak_config.yaml`:

```yaml
ontology_adapters:
  MY_CUSTOM: simpleobo:path/to/ontology.obo
  INTERNAL: ""  # Or empty string to explicitly skip
```

## See Also

- [Configuration](configuration.md) - Complete configuration guide
- [Caching](caching.md) - How ontology data is cached
- [Validation Types](validation-types.md) - Understanding the validation plugins
