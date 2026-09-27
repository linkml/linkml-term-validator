# Put LTV in repository guardrails

Read this when integrating or changing automated term validation. Routine
curation should use the project's existing recipes.

## Establish a reproducible command

Inspect task runners, existing LinkML validation plugins, dependency locks,
schema bindings, ontology config, hooks, and CI. Reuse or extend the established
entry point. Record which fields/checks are covered and which results block
editing or merging; use that contract for human and agent changes alike.

For a uv project without LTV, use `uv add linkml-term-validator` and commit
dependency/lock changes as part of setup. The skill alone does not install the
runtime. At setup or upgrade, check the locked release's subcommand help:
current upstream flags may not exist in a consuming project's pinned version.

For a project with `schema.yaml`, target class `Sample`, and
`conf/oak_config.yaml`, these are separate checks:

```bash
uv run --locked linkml-term-validator validate-schema schema.yaml \
  --config conf/oak_config.yaml --cache-dir cache --strict --verbose

uv run --locked linkml-term-validator validate-data data.yaml \
  --schema schema.yaml --target-class Sample \
  --config conf/oak_config.yaml --cache-dir cache --labels --fail-on any
```

Adapt the class and paths to the actual project. The schema command checks
enum meanings; the data command checks dynamic enums and bindings. Include
structural LinkML validation separately, or verify an existing plugin pipeline
covers all intended checks. For `linkml-validate` integrations, verify plugin
severity and the host runner's exit policy: it can differ from LTV's CLI.
See [Configuration](configuration.md) before choosing thresholds.

## Prepare ontology data and account for coverage

Determinism depends on the tool, config, ontology version, and caches as well as
the input record. Pin or record ontology releases and cache provenance. Decide
how refreshes are reviewed and how newly introduced CURIEs acquire coverage.

Label caches establish ID/label resolution; enum caches establish membership
in particular value sets. Neither is interchangeable with the other.
Progressive enum caches may be partial. Do not call a cached set complete
without establishing its completeness.

`--offline` uses existing validator caches only, even with a local OBO adapter.
It does not populate them from that adapter. Missing rows remain unresolved;
cached labels do not supply the synonym data used for Not4Curation checking.
A cache hit therefore does not prove all enabled checks ran. Prepare and check
new terms using the configured source, then retain explicit coverage diagnostics
in an offline gate.

Batch file validation where practical to reuse adapters and in-memory caches.
Keep whole-ontology downloads and full enum expansion out of every edit.
When switching adapters, compare both labels and relevant ancestry/membership;
two sources can agree on labels while disagreeing on allowed values.

## Integrate hooks and CI

Use a command hook for the checker. Follow the installed Claude Code version's
[hook protocol](https://code.claude.com/docs/en/hooks) and preserve existing hooks.

- For `PreToolUse`, validate candidate content in a temporary file with the
  actual tool's edit semantics, including repeated replacements. Validating
  the old file cannot reject a bad proposed edit.
- Find the checkout containing the edited path. Resolve schema, config, local
  ontologies, and caches there, including when the hook script resides in
  another worktree. Keep temporary files from changing relative-path meaning.
- Map a blocking validation failure to hook exit 2 and provide a useful stderr
  diagnosis. Forwarding CLI exit 1 alone does not establish a blocking hook.
  Bound subprocess runtime so the hook can report before its own timeout.
- `PostToolUse` gives feedback on saved content but cannot prevent that write.
  Tool-specific hooks miss other edit paths. Run the same repository validator
  in CI for changes made by humans, shell scripts, and agents.

Select CI inputs deliberately. Data changes need the relevant file checks;
schema, config, dependency, and ontology/cache changes can require broader
validation. Include those paths in workflow triggers and handle deletions or an
intentionally empty selection explicitly. Do not mask a crash or a selected
input set with no actual checks as a pass.

Prove the integration with a valid binding, wrong label, wrong range, unknown
CURIE, missing cache entry, and a field that lacks a binding. Verify severity
thresholds using actual runner outcomes. Include a semantically wrong but
self-consistent ID/label pair to demonstrate the remaining curation obligation.
Confirm bounded runtime, expected cache writes, and visible incomplete checks.

## Example: dismech

At [dismech's inspected revision](https://github.com/monarch-initiative/dismech/tree/6bd2810f2f896fd9aa05f8223f7f50caebe457b3),
the [recipes](https://github.com/monarch-initiative/dismech/blob/6bd2810f2f896fd9aa05f8223f7f50caebe457b3/project.justfile)
run term validation with `--labels`, target class `Disease`, and the explicit
`conf/oak_config.yaml`. The pre-edit hook blocks schema and term failures while
reference findings are advisory at that stage.

The [term wrapper](https://github.com/monarch-initiative/dismech/blob/6bd2810f2f896fd9aa05f8223f7f50caebe457b3/scripts/run_term_validator.sh)
handles a pinned release and inspects warnings and success output. Understand
that contract before replacing it with newer native flags. Broad grepping for
warnings can catch unrelated dependency warnings; do not copy it as a universal
severity implementation.

The [CI workflow](https://github.com/monarch-initiative/dismech/blob/6bd2810f2f896fd9aa05f8223f7f50caebe457b3/.github/workflows/main.yaml)
uses cached, offline schema-term checks in the merge queue after checking
schema changes online in PRs. This is a staged coverage strategy, not a promise
that offline validation resolves newly introduced terms. Its
[OAK config](https://github.com/monarch-initiative/dismech/blob/6bd2810f2f896fd9aa05f8223f7f50caebe457b3/conf/oak_config.yaml)
also documents why cache roles and adapter ancestry comparisons matter.
