---
name: linkml-term-validator
description: Set up, configure, and troubleshoot LinkML Term Validator (LTV) as deterministic ontology QC in repository hooks and CI. Use when interpreting identifier, label, enum, or binding failures; diagnosing cache and coverage gaps; or configuring ontology adapters, constraints, and severity policy.
---

# Work with ontology QC

LTV checks ontology identifiers, labels, and schema-declared term constraints.
Run these deterministic checks through repository automation. The agent helps
integrate the checker, diagnose findings, and choose sound corrections.
Maintainers own constraint and exception policy; curators decide which concept
represents the intended biology. A valid CURIE with its canonical label can
still name the wrong gene, disease, or process.

A passing automated check needs no agent reenactment. Apply semantic judgment
when selecting, repairing, or reviewing terms, rather than looking up every
validated term again on each QC run.

## Establish the validation context

Read the repository's guidance, validation recipe/wrapper, schema, ontology
config, dependency lock, and existing hook/CI output. Identify the affected
file/field, target class, enabled checks, adapter, and cache. Reuse the project
command so that reproducing a failure preserves the gate's actual policy.
An ontology lookup alone does not reproduce a binding or dynamic-enum check.

- For a new gate or hook/CI changes, read [Guardrail setup](references/guardrails.md).
- For adapters, cache coverage, schema constraints, or severity changes, read
  [Configuration and targeted diagnosis](references/configuration.md).

Installing this skill supplies instructions; it does not install LTV, activate
hooks, or establish required CI checks.

## Interpret findings in their curation context

| Finding | Agent's next step |
| --- | --- |
| ID and label agree | Report that check as passed. During curation or semantic review, compare the concept with the surrounding name and claim. |
| Label mismatch | Inspect the term's definition and context before changing either field. Replacing a wrong label with the canonical label can hide a wrong ID. |
| Outside an enum or binding | Inspect the slot's intended range, roots, relationships, and ontology source. A real term need not be an allowed value here. |
| Obsolete or Not4Curation term | Inspect ontology guidance and replacement candidates; verify semantic fit rather than substituting mechanically. |
| Unknown ID or unavailable source | Distinguish a nonexistent term from an adapter failure, unsupported prefix, or incomplete cache before changing data. |
| Passed with missing checks | Inspect unbound fields, skipped prefixes, missing synonym data, and cache coverage. Report what remains unchecked. |

Schema structure, term existence, canonical labels, enum membership, and
scientific suitability are separate claims. Do not infer all of them from one
green command. In dismech, arbitrary qualifier fields need additional checks;
an ID/label pair can pass while disagreeing with the gene named elsewhere in
the same record.

## Correct the cause and close the loop

When repairing a term, inspect definitions and ancestry against the intended
claim. Preserve a valid data assertion when infrastructure cannot validate it.
Use the project's ontology/cache refresh procedure for missing or stale data;
do not hand-edit derived cache membership to force acceptance.

For configuration work, show the effect on representative valid, invalid, and
unchecked cases. During routine data fixes, preserve enabled checks and policy:
`--lenient`, disabling labels/bindings, broader enum roots, and relaxed severity
thresholds change what the gate accepts.

Rerun the affected check after correction and the required repository checks
before delivery. Report file/field, CURIE, observed versus expected result,
semantic rationale, adapter/cache context, command and outcome, and unchecked
terms. Surface unresolved concept choices or policy decisions to the curator.
