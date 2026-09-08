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

Dynamic enums (those using `reachable_from`, `concepts`, enum expressions, or inherited enums) can be cached to avoid expensive ontology traversals. Enum cache keys also account for `matches` expressions, although full `matches` validation is not implemented yet. Enum caches are stored in:

```
cache/
└── enums/
    ├── biologicalprocessenum_abc123.csv
    ├── biologicalprocessenum_abc123.csv.complete
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

An enum cache is only treated as a **full authoritative expansion** when a sibling `.complete` marker file exists. Unmarked enum cache CSVs are treated as append-only positive membership caches.

## Enum Caching Strategies

The validator supports two strategies for caching dynamic enum values:

### Progressive Caching (Default)

**Progressive caching** validates enums lazily:

1. Check in-memory cache
2. Check file cache for positive hits
3. If the enum cache has a `.complete` marker, treat it as the full closure
4. Otherwise fall back to ontology reachability checks and append valid hits
5. Optionally use `--saturate-enum-caches` to materialize the full closure and write the `.complete` marker

**Benefits:**
- Avoids upfront expansion for enums that are never used
- Safely reuses existing partial caches from earlier runs or versions
- Faster startup (no upfront expansion)
- Can still be promoted to a closed cache when you want warm negative lookups too

**Trade-offs:**
- Unseen valid terms still require ontology checks until the cache is saturated
- Full warm negative lookups require either saturation or greedy mode

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

- **First run**: Queries ontology databases and saves label / enum caches
- **Subsequent runs**: Loads warm label caches, positive enum hits, and any `.complete` enum closures from disk
- **Cache location**: Configurable via `--cache-dir` flag
- **Disable all file caching**: Use `--no-cache`
- **Disable only enum expansion caching**: Use `--no-cache-enum-expansions`
- **Close enum caches on demand**: Use `--saturate-enum-caches`

## Offline Mode

Offline mode (`--offline`) forces validation to run **entirely from the cache**,
with a guarantee that no external ontology access ever happens. In offline mode
the validator never builds an OAK adapter, so it cannot download an ontology
database or contact a remote service (OLS, etc.). Every label and enum-membership
check is resolved exclusively from the CSV cache in `--cache-dir`.

This is useful for:

- **Air-gapped or network-restricted environments** where outbound access is unavailable or forbidden
- **CI/CD pipelines** that must be reproducible and must not depend on remote ontology services
- **Guaranteeing determinism** — results depend only on the committed cache, never on live ontology state

### Preparing a cache for offline use

Because offline mode only reads the cache, the cache must already contain
everything the validation needs:

- **Labels**: run the same validation online at least once (with caching enabled) so the required labels are written to `cache/<prefix>/terms.csv`.
- **Dynamic enums**: materialize full enum closures with `--saturate-enum-caches` (or `--cache-strategy greedy`) so the `.complete` marker is written. A dynamic enum without a complete cache cannot be validated offline (membership can only be confirmed from a materialized closure); in that case each value is reported as an error whose message points you at the unmaterialized closure rather than claiming the data is invalid. Note that `--saturate-enum-caches` is a **no-op offline** (it cannot build the closure without ontology access), so run it online.

A typical workflow is to populate the cache online once, commit the `cache/`
directory to version control, and then validate offline everywhere else:

```bash
# 1. Populate the cache online (materializing dynamic enum closures)
linkml-term-validator validate-data data.yaml -s schema.yaml \
  --cache-dir cache --saturate-enum-caches

# 2. Commit the cache, then validate offline anywhere (no network access)
linkml-term-validator validate-data data.yaml -s schema.yaml \
  --cache-dir cache --offline
```

Offline mode reads the cache even when `--no-cache` /
`cache_labels=False` is set — since the cache is the only permitted source,
reading it is always enabled.

### Uncached terms are errors

In offline mode a term that cannot be resolved from the cache is always reported
as an **ERROR** (non-zero exit code), regardless of `--strict` or whether the
prefix is configured in `oak_config.yaml`. This is stricter than online
validation, where an unconfigured prefix is treated as "skipped" (INFO). The
guarantee is deliberate: an offline run must not pass green while silently
leaving terms unvalidated. No exception is raised — every miss is collected and
surfaced in the normal validation report alongside any other issues, and the
command exits non-zero if any errors were found. To make an offline run pass,
populate the cache (labels and, for dynamic enums, a materialized `.complete`
closure) so every referenced term is present.

### Configuration

```bash
# CLI: force offline validation on any command
linkml-term-validator validate-schema schema.yaml --offline
linkml-term-validator validate-data data.yaml -s schema.yaml --offline
linkml-term-validator validate-text-file document.md --offline
```

```python
# Python API: pass offline=True to any plugin or to ValidationConfig
from linkml_term_validator.plugins import DynamicEnumPlugin

plugin = DynamicEnumPlugin(cache_dir="cache", offline=True)
```

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

# In progressive mode, fully materialize and mark enum caches complete
linkml-term-validator validate-data data.yaml -s schema.yaml --saturate-enum-caches
```

### Python API

```python
from linkml_term_validator.plugins import DynamicEnumPlugin, BindingValidationPlugin
from linkml_term_validator.models import CacheStrategy

# Progressive caching (default) - recommended for large ontologies
plugin = DynamicEnumPlugin(
    cache_dir="/path/to/cache",
    cache_labels=True,
    cache_enum_expansions=True,
    cache_strategy=CacheStrategy.PROGRESSIVE,
    saturate_enum_caches=False,
)

# Greedy caching - expand all upfront
plugin = BindingValidationPlugin(
    cache_dir="/path/to/cache",
    cache_labels=True,
    cache_enum_expansions=True,
    cache_strategy=CacheStrategy.GREEDY,
)
```

### YAML Configuration (oak_config.yaml)

```yaml
# Set cache strategy globally
cache_strategy: progressive  # or "greedy"
cache_enum_expansions: true
saturate_enum_caches: false

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
    cache_enum_expansions: true
    saturate_enum_caches: false
    cache_dir: cache
    cache_strategy: progressive  # or "greedy"
```

## Choosing a Cache Strategy

| Use Case | Recommended Strategy |
|----------|---------------------|
| Large ontologies (SNOMED, NCBI Taxonomy) | Progressive |
| Small, stable enums (< 1000 terms) | Greedy |
| First-time validation of new dataset | Progressive |
| Repeated validation of same dataset | Progressive + `--saturate-enum-caches`, or Greedy |
| CI/CD pipelines | Greedy (deterministic) |
| Interactive development | Progressive (faster startup) |

**Rule of thumb**: Start with progressive (the default). Use `--saturate-enum-caches` when you want to close and reuse caches without switching the whole run to greedy mode.

## When to Clear Cache

You might want to clear the cache if:

- **Ontology databases have been updated** and you need the latest labels
- **You suspect stale or incorrect labels** in cached data
- **You're testing validation behavior** and want to force fresh lookups

You do **not** need to clear the enum cache to adopt the
[Not4Curation check](plugin-reference.md#not4curation-check). The cache only
records membership; the check runs on every accepted value during an online
run, so one online pass surfaces every flagged term already cached. Offline,
synonyms are not available and such terms are reported as *unchecked* (a
non-gating note), never as clean.

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
