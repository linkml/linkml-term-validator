# Caching

The validator uses **multi-level caching** to speed up repeated validations and avoid redundant ontology queries.

## In-Memory Cache

During a single validation run, ontology labels are cached in memory. If multiple permissible values reference the same term, it's only looked up once.

This cache exists only for the duration of the validation process and is discarded afterward.

## File-Based Cache

Labels are persisted to CSV files in the cache directory (default: `./cache`):

```
cache/
├── go/
│   └── terms.csv      # GO term labels
├── chebi/
│   └── terms.csv      # CHEBI term labels
└── uberon/
    └── terms.csv      # UBERON term labels
```

### Cache File Format

Cache files use a simple CSV format:

```csv
curie,label,retrieved_at
GO:0008150,biological_process,2025-11-15T10:30:00
GO:0007049,cell cycle,2025-11-15T10:30:01
```

## Cache Behavior

- **First run**: Queries ontology databases, saves results to cache
- **Subsequent runs**: Loads from cache files (very fast, no network/database access)
- **Cache location**: Configurable via `--cache-dir` flag
- **Disable caching**: Use `--no-cache` flag

## Configuration

### CLI

```bash
# Use custom cache directory
linkml-term-validator validate-schema --cache-dir /path/to/cache schema.yaml

# Disable caching
linkml-term-validator validate-schema --no-cache schema.yaml
```

### Python API

```python
from linkml_term_validator.plugins import DynamicEnumPlugin

plugin = DynamicEnumPlugin(
    cache_dir="/path/to/cache",
    cache_labels=True  # Enable/disable file-based caching
)
```

### linkml-validate Configuration

```yaml
plugins:
  "linkml_term_validator.plugins.DynamicEnumPlugin":
    oak_adapter_string: "sqlite:obo:"
    cache_labels: true
    cache_dir: cache
```

## When to Clear Cache

You might want to clear the cache if:

- **Ontology databases have been updated** and you need the latest labels
- **You suspect stale or incorrect labels** in cached data
- **You're testing validation behavior** and want to force fresh lookups

```bash
# Clear cache for specific ontology
rm -rf cache/go/

# Clear entire cache
rm -rf cache/
```

## Performance Benefits

Caching provides significant performance improvements:

- **First validation**: May take several seconds per ontology (database queries)
- **Cached validations**: Typically < 100ms (CSV file reads)
- **No network dependency**: Cached validations work offline

## Cache Safety

The cache is **read-only during validation** and only contains:
- CURIEs (ontology identifiers)
- Canonical labels
- Timestamps

Cached data cannot affect validation logic, only speed up lookups.

## See Also

- [Configuration](configuration.md) - Complete configuration options
- [Ontology Access](ontology-access.md) - How ontology adapters work
