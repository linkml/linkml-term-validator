# Configure and diagnose term checks

## Identify what is actually constrained

Start from the failing field and schema, not just the CURIE. Inspect the target
class, slot range, binding `binds_value_of`, enum definitions, and label
annotations. Canonical labels can be declared with `implements: [rdfs:label]`.
Arbitrary nested `id`/`label` pairs do not acquire a binding just because their
names look familiar.

Static enum `meaning` checks, dynamic enums, and bindings cover different
inputs. For `reachable_from`, inspect source nodes and relationship types:
roots are excluded unless `include_self` is enabled. Also inspect `matches`
and `concepts` definitions where used. Diagnose a wrong range before widening
an enum; expansion changes which concepts the schema permits.

A structural validator may permit an unconstrained qualifier, while LTV never
checks its contents. Make the coverage gap explicit and use a dedicated field
or additional validator according to the project's schema policy.

## Configure ontology access

Pass the project's config explicitly with `--config` (`-c` in LTV).
The default `sqlite:obo:` adapter can download databases; `ols:` uses a remote
service; `simpleobo:` reads a local file. Choose sources for their required
capabilities and version, not merely to silence an unavailable service.

For a small local ontology at `ontologies/test.obo`:

```yaml
ontology_adapters:
  TEST: simpleobo:ontologies/test.obo
check_not4curation: true
```

Resolve relative paths from the repository context used by the runner. Check
prefix spelling and the schema's `source_ontology` too; it can select a
different source for an enum. Inspect skipped/unconfigured-prefix diagnostics
instead of assuming every CURIE in the file was checked.

Not4Curation checks are enabled by default unless configuration disables them;
an explicit check/no-check CLI flag takes precedence. Missing synonym data is
reported as unchecked and does not itself fail the command. Offline runs cannot
read these markers from label caches. A successful exit is not proof that this
check covered every term.

## Diagnose cache problems

Record cache paths and ontology source/version before refreshing. With
`--offline`, adapters are not constructed even for local OBO files. An absent
label or membership row can mean missing coverage, not an invalid term.

Distinguish label cache presence from membership in the slot's dynamic enum.
Progressive enum caches accumulate values and can be incomplete; do not infer
the ontology's full allowed set by reading one CSV. Use the project's refresh
or expansion workflow and verify the result against the configured source.
A refreshed label alone does not resolve a missing ancestor relationship.
After schema, ontology, or adapter changes, recheck cache validity rather than
assuming old rows still describe the new constraints.

## Separate reported severity from the runner's exit rule

Check the installed release and exact entry point before changing policy.

| Entry point | Policy to inspect |
| --- | --- |
| `validate-data` | `--fail-on any` (default) fails on every result; `error` fails on ERROR; `warn` includes WARN. `--strict` raises an `error` threshold to include warnings without enabling disabled checks. |
| `validate-schema` | Uses its own `--strict` behavior for label and unresolved-term checks; it has no `--fail-on` option. Ordinary warnings can finish with exit 0. |
| `validate-text-file` | `--strict` makes unresolved CURIEs errors even for unconfigured prefixes; it is not the data command's severity threshold. |
| `linkml-validate` with LTV plugins | Inspect both plugin result severity and the host runner's failure threshold. A printed warning need not fail the host command. |

In schema validation, label mismatches for explicitly configured prefixes are
already errors. With a fallback adapter and an unconfigured prefix they can be
warnings; `--strict` promotes them. Preserve the config when reproducing this.

For data/plugin validation, the shared OAK config can override individual error
modes, for example:

```yaml
severity_overrides:
  binding_label_mismatch: ERROR
```

This sets result severity, not the CLI threshold. Use recognized error-mode
names from the installed release and verify with a deliberate mismatch.
`--lenient` disables term-existence failures in data validation independently
of the failure threshold. Do not add it, disable a check, or demote findings as
an incidental data repair.

An ontology service failure is reported as "Unable to validate at this time"
with exit 2; invocation errors can also use 2. Inspect the diagnostic before
classifying the run. Data validation findings normally fail with exit 1 when
they meet the chosen threshold. Project wrappers may implement additional
policy, so preserve their diagnostics as well.

## Investigate text annotations and semantic mismatches

For `@term CURIE "label"` annotations, `validate-text-file` can check extracted
pairs. Custom formats need `--regex`, `--curie-group`, and `--label-group`.
Confirm the extractor finds intended annotations; zero matches is not coverage.

If the pair resolves correctly but conflicts with the record's surrounding
name or claim, inspect definitions and source evidence before changing it.
Never automatically canonicalize the label of an ID copied from an unverified
research report. A corrected label can make the wrong biological entity
self-consistent and conceal the mistake. Consult the
[LTV documentation](https://linkml.io/linkml-term-validator/) for the installed
release's plugin and binding options.
