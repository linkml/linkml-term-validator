---
name: linkml-term-validator
description: Validate ontology CURIEs, labels, enum meanings, dynamic enums, and binding constraints with LinkML Term Validator (LTV). Use for term checks in LinkML schemas, YAML/JSON data, or annotated text, including obsolete and Not4Curation terms and offline cache limitations.
---

# Validate ontology terms

Use LTV for ontology-backed checks. It complements structural LinkML validation;
a valid identifier alone does not prove that a term is appropriate for the data.

## Choose input and ontology source

Examples use `uvx linkml-term-validator`; use `uv run linkml-term-validator`
inside its checkout. Skill installation does not install the Python runtime.
Check subcommand help against the installed release before selecting flags.

Inspect the schema's prefixes, enum definitions, bindings, and target class.
Reuse the project's `oak_config.yaml` where present. The default OAK adapter
`sqlite:obo:` can download ontology databases. `ols:` uses a remote service;
`simpleobo:/absolute/path/to/ontology.obo` uses a local OBO file. A config can map
prefixes to different adapters:

```yaml
ontology_adapters:
  TEST: simpleobo:/absolute/path/to/test.obo
```

Choose the ontology version and source deliberately; do not invent identifiers
or change valid data because a service is unavailable. Record the adapter and
cache directory with the results.

## Run the appropriate validation

Check `meaning` fields in a schema's permissible values:

```bash
uvx linkml-term-validator validate-schema schema.yaml \
  --config oak_config.yaml --cache-dir terms_cache
```

Check dataset dynamic enums, bindings, and labels:

```bash
uvx linkml-term-validator validate-data data.yaml --schema schema.yaml \
  --target-class Sample --config oak_config.yaml --cache-dir terms_cache
```

Replace `Sample` with the actual schema class. Dynamic enums can use
`reachable_from`, `matches`, or `concepts`. For reachability, inspect the root
and relationship types: source nodes are excluded unless `include_self` is
enabled. Do not mistake a root's exclusion for an invalid ontology identifier.

For text containing annotations such as `@term CURIE "label"`:

```bash
uvx linkml-term-validator validate-text-file document.md \
  --config oak_config.yaml --cache-dir terms_cache --strict --verbose
```

For a different text format, use `--regex` with `--curie-group` and
`--label-group`. Confirm that the regex extracted the intended pairs; zero
matches is not evidence that every term in the document is valid.

## Interpret failures and incomplete checks

- Distinguish unresolved identifiers, label mismatches, obsolete terms,
  Not4Curation markers, and terms outside a required enum or binding range.
  Verify replacement candidates against the ontology before proposing edits.
- `--offline` resolves from existing caches only, including when an adapter is
  local. Cache misses or incomplete enum expansions are unresolved work, not
  proof that a term does not exist. Keep label/enum caches for repeatable checks.
- Not4Curation checks are enabled by default unless config overrides them;
  explicit flags take precedence. Unavailable synonym data, including offline
  runs, is reported as **unchecked**, not passed.
- Read diagnostics alongside the exit code. For `validate-schema` and
  `validate-data`, `--fail-on` controls the severity threshold; default `any`
  fails on every result. `--strict` raises the threshold to include warnings
  when `--fail-on error` is used. It does not enable disabled checks.
- `validate-text-file --strict` has a different purpose: unresolvable CURIEs
  become errors even for unconfigured prefixes. `--lenient` on data validation
  disables term-existence failures; do not use it to conceal service outages.
- An ontology service failure is reported as "Unable to validate at this time"
  with exit code 2; inspect the message because CLI usage errors can also use 2.
  Separate these from data validation failures (exit 1).

Return the affected file/field, CURIE, expected and observed label or constraint,
ontology source, check settings, and remaining unchecked terms. Only edit data
or schemas when requested, and rerun the same checks after corrections.

See the [LTV documentation](https://linkml.io/linkml-term-validator/)
for binding schemas, cache configuration, and validation plugins.
