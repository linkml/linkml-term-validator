# CLI Reference

Complete command-line interface reference for `linkml-term-validator`.

## Commands

`linkml-term-validator` currently exposes five commands:

| Command | Purpose |
|---------|---------|
| `validate-schema` | Validate `meaning` fields in schema enum permissible values |
| `validate-data` | Validate data against dynamic enums and binding constraints |
| `validate` | Auto-detect schema validation vs data validation based on `--schema` |
| `migrate-cache` | Normalize label cache CSV files after upgrades or relabeling |
| `validate-text-file` | Validate CURIE/label pairs embedded in text or Markdown |

## validate-schema

Validates that enum permissible value `meaning` fields reference resolvable ontology terms and that schema labels match ontology labels.

```bash
linkml-term-validator validate-schema [OPTIONS] SCHEMA_PATH
```

| Option | Default | Description |
|--------|---------|-------------|
| `--adapter`, `-a` | `sqlite:obo:` | Default OAK adapter string |
| `--strict` | `false` | Treat warnings as errors |
| `--no-cache` | `false` | Disable label cache writes |
| `--cache-dir` | `cache` | Directory for label and enum caches |
| `--config`, `-c` | none | Path to `oak_config.yaml` |
| `--offline` | `false` | Resolve only from the cache; never build OAK adapters |
| `--verbose`, `-v` | `false` | Print validation summary details |

Examples:

```bash
linkml-term-validator validate-schema schema.yaml
linkml-term-validator validate-schema schema.yaml --strict
linkml-term-validator validate-schema schema.yaml --config oak_config.yaml
linkml-term-validator validate-schema schema.yaml --offline --cache-dir cache
```

## validate-data

Validates one or more YAML/JSON data files against dynamic enum ranges and/or binding constraints.

```bash
linkml-term-validator validate-data [OPTIONS] DATA_PATHS...
```

| Option | Default | Description |
|--------|---------|-------------|
| `--schema`, `-s` | required | LinkML schema path |
| `--target-class`, `-t` | none | Target class for validation |
| `--bindings / --no-bindings` | `--bindings` | Enable or disable binding validation |
| `--dynamic-enums / --no-dynamic-enums` | `--dynamic-enums` | Enable or disable dynamic enum validation |
| `--labels / --no-labels` | `--labels` | Enable or disable ontology label checks in bindings |
| `--lenient / --no-lenient` | `--no-lenient` | Do not fail binding existence checks when term IDs are not found |
| `--adapter`, `-a` | `sqlite:obo:` | Default OAK adapter string |
| `--no-cache` | `false` | Disable file-based label and enum cache writes |
| `--cache-dir` | `cache` | Directory for label and enum caches |
| `--cache-enum-expansions / --no-cache-enum-expansions` | `--cache-enum-expansions` | Enable or disable dynamic enum expansion cache writes |
| `--saturate-enum-caches / --no-saturate-enum-caches` | `--no-saturate-enum-caches` | Materialize full dynamic enum closures and mark caches complete |
| `--config`, `-c` | none | Path to `oak_config.yaml` |
| `--cache-strategy` | `progressive` | `progressive` for lazy checks or `greedy` for upfront expansion |
| `--offline` | `false` | Resolve only from the cache; never build OAK adapters |
| `--fail-on` | `any` | Which results exit non-zero: `any`, `error` (matches `linkml-validate`), or `warn` |
| `--strict` | `false` | Treat warnings as errors: guarantees `WARN` exits non-zero, overriding `--fail-on error` |

Examples:

```bash
linkml-term-validator validate-data data.yaml --schema schema.yaml
linkml-term-validator validate-data data.yaml -s schema.yaml -t GeneAnnotation
linkml-term-validator validate-data data/*.yaml -s schema.yaml --config oak_config.yaml
linkml-term-validator validate-data data.yaml -s schema.yaml --no-bindings
linkml-term-validator validate-data data.yaml -s schema.yaml --no-dynamic-enums
linkml-term-validator validate-data data.yaml -s schema.yaml --no-labels
linkml-term-validator validate-data data.yaml -s schema.yaml --saturate-enum-caches
linkml-term-validator validate-data data.yaml -s schema.yaml --strict
```

Offline dynamic enum validation requires a complete enum cache. Populate it online first:

```bash
linkml-term-validator validate-data data.yaml -s schema.yaml \
  --cache-dir cache --saturate-enum-caches

linkml-term-validator validate-data data.yaml -s schema.yaml \
  --cache-dir cache --offline
```

## validate

Convenience command that calls schema validation unless `--schema` is supplied. With `--schema`, it validates the input as data against that schema.

```bash
linkml-term-validator validate [OPTIONS] INPUT_PATH
```

The options mirror the relevant `validate-schema` and `validate-data` options:

```bash
# Schema validation
linkml-term-validator validate schema.yaml

# Data validation
linkml-term-validator validate data.yaml --schema schema.yaml

# Data validation, warnings fail regardless of --fail-on
linkml-term-validator validate data.yaml --schema schema.yaml --strict
```

## migrate-cache

Normalizes existing label cache files.

```bash
linkml-term-validator migrate-cache [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--cache-dir` | `cache` | Directory containing cache files |
| `--adapter`, `-a` | `sqlite:obo:` | OAK adapter string used when refreshing labels |
| `--config`, `-c` | none | Path to `oak_config.yaml` |
| `--dry-run` | `false` | Preview changes without writing |
| `--refresh-labels` | `false` | Re-fetch labels from ontology and update changed labels |
| `--sort-only` | `false` | Only sort and deduplicate cache files |

Use this after upgrading, after merging cache files, or when you want deterministic cache diffs:

```bash
linkml-term-validator migrate-cache --dry-run
linkml-term-validator migrate-cache --sort-only
linkml-term-validator migrate-cache --refresh-labels --config oak_config.yaml
```

## validate-text-file

Validates CURIE/label pairs extracted from text or Markdown with a regular expression.

```bash
linkml-term-validator validate-text-file [OPTIONS] FILE_PATH
```

The default regex matches annotations like `@term GO:0008150 "biological process"`.

| Option | Default | Description |
|--------|---------|-------------|
| `--regex`, `-r` | `@term (\S+) "([^"]*)"` | Regex with capture groups for CURIE and label |
| `--curie-group` | `1` | Capture group index for the CURIE |
| `--label-group` | `2` | Capture group index for the label |
| `--strict` | `false` | Treat unresolvable CURIEs as errors, even for unconfigured prefixes |
| `--adapter`, `-a` | `sqlite:obo:` | Default OAK adapter string |
| `--config`, `-c` | none | Path to `oak_config.yaml` |
| `--no-cache` | `false` | Disable label cache writes |
| `--cache-dir` | `cache` | Directory for label caches |
| `--offline` | `false` | Resolve only from the cache; never build OAK adapters |
| `--verbose`, `-v` | `false` | Show each validated term |

Examples:

```bash
linkml-term-validator validate-text-file document.md

linkml-term-validator validate-text-file document.md \
  --regex '([^=]+)=(TEST:\d+)' \
  --label-group 1 \
  --curie-group 2 \
  --config tests/data/test_oak_config.yaml
```

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | No validation errors |
| `1` | Validation errors, missing files, invalid options, or cache migration failure |
| `2` | Unable to validate: the ontology service was unreachable (network outage, or a transient HTTP status — 5xx, 408 timeout, or 429 rate-limit), so terms could not be checked. This is distinct from invalid data — retry when the service is reachable, or use `--offline` to validate against the local cache only. Validation *fails fast*: the run aborts on the first outage rather than reporting every subsequent term as missing. Across a multi-file `validate-data` run this means one prefix's transient outage stops the whole run, including files that were fully resolvable from the local cache. |

## See Also

- [Configuration](configuration.md)
- [Caching](caching.md)
- [Schema Validation](schema-validation.md)
- [Data Validation](data-validation.md)
- [Binding Validation](binding-validation.md)
