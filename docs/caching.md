# Caching

The validator uses **multi-level caching** to speed up repeated validations and avoid redundant ontology queries.

## Cache Types

There are two types of caches:

1. **Label cache** - Maps CURIEs to their canonical labels (for label validation)
2. **Enum cache** - Stores expanded dynamic enum values (for closure validation)

## In-Memory Cache

During a single validation run, ontology labels and enum values are cached in memory. If multiple fields reference the same term or enum, lookups are only done once.

This cache exists only for the duration of the validation process and is discarded afterward.

## Label Cache (File-Based)

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

### Label Cache Format

```csv
curie,label,retrieved_at
GO:0008150,biological_process,2025-11-15T10:30:00
GO:0007049,cell cycle,2025-11-15T10:30:01
```

## Enum Cache (Dynamic Enums)

Dynamic enums (those using `reachable_from`, `matches`, or `concepts`) can be cached to avoid expensive ontology traversals. Enum caches are stored in:

```
cache/
└── enums/
    ├── biologicalprocessenum_abc123.csv
    ├── cellularcomponentenum_def456.csv
    └── ...
```

### Enum Cache Format

```csv
curie
GO:0008150
GO:0007049
GO:0006260
```

The cache filename includes a hash of the enum definition, so changes to source nodes or relationship types automatically invalidate the cache.

## Enum Caching Strategies

The validator supports two strategies for caching dynamic enum values:

### Progressive Caching (Default)

**Progressive caching** validates terms lazily:

1. Check in-memory cache
2. Check file cache
3. Query ontology directly (is this term a descendant of the source nodes?)
4. If valid, add to cache for future lookups

**Benefits:**
- Scales well for large ontologies (SNOMED with 100k+ terms)
- Cache grows organically with actual usage
- Faster startup (no upfront expansion)
- Supports "lazy list" style enums (e.g., any valid chemical SMILES)

**Trade-offs:**
- First validation of each term requires ontology query
- Cache is append-only (may contain terms no longer in use)

### Greedy Caching

**Greedy caching** expands the entire enum upfront:

1. On first access, query ontology for ALL descendants
2. Cache the complete set
3. Subsequent lookups are simple set membership checks

**Benefits:**
- Deterministic - same results every time
- No per-term ontology queries after initial expansion
- Good for smaller, frequently-validated enums

**Trade-offs:**
- Slow startup for large ontologies
- Memory-intensive for large closures
- May cache terms never actually used

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

# Use greedy caching strategy (expand all upfront)
linkml-term-validator validate-data data.yaml -s schema.yaml --cache-strategy greedy

# Use progressive caching strategy (default, lazy validation)
linkml-term-validator validate-data data.yaml -s schema.yaml --cache-strategy progressive
```

### Python API

```python
from linkml_term_validator.plugins import DynamicEnumPlugin, BindingValidationPlugin
from linkml_term_validator.models import CacheStrategy

# Progressive caching (default) - recommended for large ontologies
plugin = DynamicEnumPlugin(
    cache_dir="/path/to/cache",
    cache_labels=True,
    cache_strategy=CacheStrategy.PROGRESSIVE,
)

# Greedy caching - expand all upfront
plugin = BindingValidationPlugin(
    cache_dir="/path/to/cache",
    cache_labels=True,
    cache_strategy=CacheStrategy.GREEDY,
)
```

### YAML Configuration (oak_config.yaml)

```yaml
# Set cache strategy globally
cache_strategy: progressive  # or "greedy"

# Configure ontology adapters
ontology_adapters:
  GO: sqlite:obo:go
  HP: sqlite:obo:hp
  CL: sqlite:obo:cl
```

### linkml-validate Configuration

```yaml
plugins:
  "linkml_term_validator.plugins.DynamicEnumPlugin":
    oak_adapter_string: "sqlite:obo:"
    cache_labels: true
    cache_dir: cache
    cache_strategy: progressive  # or "greedy"
```

## Choosing a Cache Strategy

| Use Case | Recommended Strategy |
|----------|---------------------|
| Large ontologies (SNOMED, NCBI Taxonomy) | Progressive |
| Small, stable enums (< 1000 terms) | Greedy |
| First-time validation of new dataset | Progressive |
| Repeated validation of same dataset | Either (after initial cache) |
| CI/CD pipelines | Greedy (deterministic) |
| Interactive development | Progressive (faster startup) |

**Rule of thumb**: Start with progressive (the default). Switch to greedy only if you need deterministic behavior or are validating the same small dataset repeatedly.

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

## Reproducibility: Versioning Ontology Snapshots

A key benefit of file-based caching is **reproducible validation**. By committing the cache directory alongside your schema, you create a versioned snapshot of the ontology state.

### Why This Matters

Ontologies evolve over time:

- Labels change (e.g., "cell cycle process" → "cell cycle")
- Terms are deprecated or merged
- New terms are added
- Hierarchies are restructured

Without a snapshot, validation results may differ depending on when you run them—the same data might pass today but fail next month after an ontology update.

### Versioning Strategy

```
my-schema/
├── schema.yaml           # Your LinkML schema
├── cache/                # Ontology snapshot (commit this!)
│   ├── go/
│   │   └── terms.csv
│   └── cl/
│       └── terms.csv
└── .gitignore            # DON'T ignore cache/
```

When you release a schema version, the cache captures the exact ontology labels at that point in time. Anyone validating against that schema version gets consistent results.

### Workflow

1. **Initial setup**: Run validation to populate cache
2. **Commit cache**: Include `cache/` in version control
3. **Release together**: Schema + cache = reproducible validation
4. **Update intentionally**: When you want new ontology labels, clear cache and regenerate

```bash
# Populate cache for a new release
rm -rf cache/
linkml-term-validator validate-schema schema.yaml
git add cache/
git commit -m "Update ontology snapshot for v2.0"
```

### Trade-offs

| Approach | Pros | Cons |
|----------|------|------|
| **Commit cache** | Reproducible, offline, fast | May miss ontology updates |
| **Fresh lookups** | Always current | Results vary over time, slower |

For most use cases, **reproducibility trumps currency**—you want validation to behave consistently.

## Cache Safety

The cache is **read-only during validation** and only contains:

- CURIEs (ontology identifiers)
- Canonical labels
- Timestamps

Cached data cannot affect validation logic, only speed up lookups.

## See Also

- [Configuration](configuration.md) - Complete configuration options
- [Ontology Access](ontology-access.md) - How ontology adapters work
