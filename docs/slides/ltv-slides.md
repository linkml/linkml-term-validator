---
marp: true
theme: default
paginate: true
backgroundColor: #fff
backgroundImage: url('https://marp.app/assets/hero-background.svg')
---

<!-- _class: lead -->
# linkml-term-validator

## Validating Ontology Term References in LinkML Schemas and Data

**Christopher J. Mungall**
Lawrence Berkeley National Laboratory

---

## What Are Ontologies?

Ontologies are **structured vocabularies** with formal hierarchical relationships between terms.

```
                    disease (MONDO:0000001)
                   /                      \
      genetic disease                infectious disease
     (MONDO:0003847)                 (MONDO:0005550)
        /        \                      /          \
 Mendelian    chromosomal          bacterial     viral
  disease      disorder            disease      disease
```

Each term has:
- A **persistent identifier** (CURIE): `MONDO:0003847`
- A **canonical label**: "genetic disease"
- **Relationships** to other terms: subClassOf, part-of, ...

---

## Biomedical Ontologies: One Per Domain

| Ontology | Domain | Terms | Example |
|----------|--------|-------|---------|
| **GO** | Gene functions | ~45,000 | `GO:0007049` cell cycle |
| **HP** | Human phenotypes | ~18,000 | `HP:0001250` seizure |
| **MONDO** | Diseases | ~32,000 | `MONDO:0005148` diabetes |
| **CHEBI** | Chemicals | ~180,000 | `CHEBI:15377` water |
| **UBERON** | Anatomy | ~16,000 | `UBERON:0000955` brain |
| **CL** | Cell types | ~3,000 | `CL:0000540` neuron |

These ontologies are the **shared language** of biomedical data.
Virtually all structured biomedical datasets reference them.

---

## CURIEs: The Universal Identifier Pattern

**CURIE** = Compact URI = `prefix:local_id`

```yaml
# Every ontology term has a globally unique CURIE
GO:0007049       # cell cycle (Gene Ontology)
HP:0001250       # seizure (Human Phenotype Ontology)
MONDO:0005148    # type 2 diabetes mellitus
CHEBI:15377      # water
CL:0000540       # neuron

# Prefix declares the namespace
prefixes:
  GO: http://purl.obolibrary.org/obo/GO_
  HP: http://purl.obolibrary.org/obo/HP_
  MONDO: http://purl.obolibrary.org/obo/MONDO_
```

CURIEs appear in clinical records, genomic annotations, knowledge bases, EHRs, and research datasets everywhere.

---

## The Problem: Ontology References Go Wrong

Data files reference thousands of ontology terms. Things break silently:

```yaml
# Wrong ID — doesn't exist in the ontology
disease_term: MONDO:9999999

# Stale label — ontology was updated, data wasn't
term:
  id: GO:0007049
  label: cell cycle process  # Renamed to "cell cycle" in GO 2024

# Wrong scope — real term, wrong ontology branch
cell_type: GO:0008150  # This is a biological process, not a cell type

# Obsoleted term — merged into another entry
phenotype: HP:0100886  # Obsoleted, replaced by HP:0003270
```

These errors propagate silently through pipelines.

---

## Who Has This Problem? Everyone.

Any data that references ontology terms needs validation:

| Domain | Example Data | Ontologies Used |
|--------|-------------|-----------------|
| **Clinical trials** | Patient phenotypes, diagnoses | HP, MONDO, SNOMED |
| **Genomics** | Gene annotations, pathways | GO, SO, PR |
| **Knowledge bases** | Disease models, drug targets | MONDO, CHEBI, HP |
| **EHRs** | Lab results, conditions | LOINC, SNOMED, RxNorm |
| **Model organisms** | Mutant phenotypes | MP, ZP, WBPhenotype |
| **AI curation** | LLM-generated annotations | Any/all of the above |

Whether a **human** or **machine** created the data, the validation problem is the same.

---

## Why This Is Hard

Validating ontology terms requires:

- Access to **hundreds of ontologies** (GO, MONDO, HP, CL, CHEBI, ...)
- Checking term **existence** (does this CURIE resolve?)
- Checking **labels** match (is "cell cycle" the current label for GO:0007049?)
- Checking **semantic constraints** (is this term actually a disease, not a phenotype?)
- Handling **obsolescence** (has this term been deprecated or merged?)
- Doing all this **fast enough** for CI pipelines and interactive use

No standard tool did all of this for LinkML data.

---

## LinkML Enumerations: Two Approaches

### Static Enums — curated list
```yaml
enums:
  VitalStatusEnum:
    permissible_values:
      ALIVE:
        meaning: NCIT:C37987
      DECEASED:
        meaning: NCIT:C28554
```

### Dynamic Enums — ontology query
```yaml
enums:
  NeuronTypeEnum:
    reachable_from:
      source_ontology: obo:cl
      source_nodes:
        - CL:0000540  # neuron
      relationship_types:
        - rdfs:subClassOf
```

---

## Dynamic Enums: Query, Don't Enumerate

Instead of listing every valid cell type, **define a constraint**:

> "Any descendant of CL:0000540 (neuron) via subClassOf"

This captures **thousands of terms** with a single declaration:

- `CL:0000127` — astrocyte
- `CL:0000598` — pyramidal neuron
- `CL:0000099` — interneuron
- ... and all future terms added to the ontology

**But who validates that data actually satisfies these constraints?**

---

## LinkML Bindings: Constraining Complex Objects

Bindings connect a field inside a nested object to a value set:

```yaml
classes:
  GeneAnnotation:
    slots:
      - ontology_term
    slot_usage:
      ontology_term:
        range: OntologyTerm
        bindings:
          - binds_value_of: id
            range: BiologicalProcessEnum

  OntologyTerm:
    slots:
      - id
      - label
```

The `id` field of `OntologyTerm` must be in `BiologicalProcessEnum`.

---

## Enter: linkml-term-validator

A **general-purpose validation framework** for any LinkML data that references ontology terms.

**Three composable plugins:**

| Plugin | Validates |
|--------|-----------|
| `PermissibleValueMeaningPlugin` | `meaning` fields in static enums |
| `DynamicEnumPlugin` | Data against dynamic enums |
| `BindingValidationPlugin` | Binding constraints + label correctness |

Validates that term references are **real**, **current**, and **correctly scoped** — regardless of whether a human or AI created the data.

All powered by **OAK (Ontology Access Kit)** for ontology access.

---

## Architecture

```
                    ┌─────────────────────┐
                    │   LinkML Validator   │
                    │     Framework        │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
   ┌──────────▼──────┐ ┌──────▼──────┐ ┌───────▼────────┐
   │ PermissibleValue│ │  Dynamic    │ │   Binding      │
   │ MeaningPlugin   │ │  EnumPlugin │ │   Validation   │
   │                 │ │             │ │   Plugin       │
   └────────┬────────┘ └──────┬──────┘ └───────┬────────┘
            │                 │                 │
            └────────────┬────┴─────────────────┘
                         │
              ┌──────────▼──────────┐
              │   OAK (Ontology     │
              │   Access Kit)       │
              │                     │
              │  sqlite:obo:go      │
              │  sqlite:obo:mondo   │
              │  sqlite:obo:hp ...  │
              └─────────────────────┘
```

---

## Plugin 1: PermissibleValueMeaningPlugin

Validates `meaning` fields in **static enum** permissible values.

**Checks:**
- Does the CURIE exist in the ontology?
- Does the label match? (optional)

```bash
linkml-term-validator validate-schema schema.yaml
```

```
⚠️  WARNING: Label mismatch
    Enum: BiologicalProcessEnum
    Value: CELL_CYCLE
    Expected label: cell cycle process
    Found label: cell cycle
    Meaning: GO:0007049
```

---

## Plugin 2: DynamicEnumPlugin

Validates data values against **dynamic enums** defined via `reachable_from`, `matches`, or `concepts`.

```bash
linkml-term-validator validate-data neurons.yaml --schema schema.yaml
```

```
❌ ERROR: Value 'GO:0008150' not in dynamic enum NeuronTypeEnum
   Expected one of the descendants of CL:0000540
```

Supports two caching strategies:
- **Progressive** (default): validate lazily, cache as you go
- **Greedy**: expand entire closure upfront

---

## Plugin 3: BindingValidationPlugin

Validates **nested object fields** against binding constraints.

```yaml
# Data with a label error
annotations:
  - gene: BRCA1
    go_term:
      id: GO:0005515
      label: DNA binding  # ❌ WRONG — actual label is "protein binding"
```

```bash
linkml-term-validator validate-data data.yaml -s schema.yaml --labels
```

```
ERROR: Label mismatch for GO:0005515
  Expected: protein binding
  Found: DNA binding
```

---

## Multi-Level Caching

```
cache/
├── go/
│   └── terms.csv        # Label cache (CURIE → label)
├── chebi/
│   └── terms.csv
├── mondo/
│   └── terms.csv
└── enums/
    ├── neurontypeenum_abc123.csv    # Enum closure cache
    └── diseaseenum_def456.csv
```

**Two cache types:**
- **Label cache** — maps CURIEs to canonical labels (CSV per prefix)
- **Enum cache** — stores expanded dynamic enum closures

---

## Caching: Why It Matters

| Scenario | Without Cache | With Cache |
|----------|--------------|------------|
| First run (10 ontologies) | ~30-60 seconds | ~30-60 seconds |
| Subsequent runs | ~30-60 seconds | **< 1 second** |
| CI pipeline (per commit) | Minutes | **Milliseconds** |
| Offline validation | Fails | Works |

**Key insight:** commit the cache to version control for **reproducible validation**. Cache = versioned ontology snapshot.

---

## Cache Stability (v0.3.0)

Early cache implementations produced **spurious diffs** on every run:
- Timestamps changed
- Sort order varied
- Whitespace inconsistencies

**v0.3.0 fixed this:**
- Deterministic sorting of cached terms
- Preserved timestamps for unchanged entries
- Stable CSV output formatting

Result: `git diff` on cache files shows **only real ontology changes**.

---

## CLI: Schema Validation

```bash
# Validate meaning fields in enum permissible values
linkml-term-validator validate-schema schema.yaml

# Strict mode: warnings become errors
linkml-term-validator validate-schema --strict schema.yaml

# Custom OAK adapter configuration
linkml-term-validator validate-schema --config oak_config.yaml schema.yaml

# Custom cache directory
linkml-term-validator validate-schema --cache-dir ./my-cache schema.yaml
```

---

## CLI: Data Validation

```bash
# Validate dynamic enums + bindings (default: both)
linkml-term-validator validate-data data.yaml --schema schema.yaml

# Specify target class
linkml-term-validator validate-data data.yaml -s schema.yaml -t Person

# Enable label validation
linkml-term-validator validate-data data.yaml -s schema.yaml --labels

# Only bindings, skip dynamic enums
linkml-term-validator validate-data data.yaml -s schema.yaml --no-dynamic-enums

# Only dynamic enums, skip bindings
linkml-term-validator validate-data data.yaml -s schema.yaml --no-bindings
```

---

## Integration with linkml-validate

Use plugins directly with the standard `linkml-validate` command:

```yaml
# validation_config.yaml
plugins:
  JsonschemaValidationPlugin:
    closed: true

  "linkml_term_validator.plugins.DynamicEnumPlugin":
    oak_adapter_string: "sqlite:obo:"
    cache_labels: true
    cache_dir: cache

  "linkml_term_validator.plugins.BindingValidationPlugin":
    validate_labels: true
    cache_dir: cache
```

```bash
linkml-validate --config validation_config.yaml
```

---

## Python API: Composable Pipeline

```python
from linkml.validator import Validator
from linkml.validator.plugins import JsonschemaValidationPlugin
from linkml_term_validator.plugins import (
    DynamicEnumPlugin,
    BindingValidationPlugin,
)

# Build a comprehensive validation pipeline
plugins = [
    JsonschemaValidationPlugin(closed=True),  # Structural
    DynamicEnumPlugin(),                       # Dynamic enums
    BindingValidationPlugin(validate_labels=True),  # Bindings
]

validator = Validator(schema="schema.yaml", validation_plugins=plugins)
report = validator.validate("data.yaml")
```

---

## Real-World Use Case: DisMech Knowledge Base

**Disease Mechanisms Knowledge Base** — curating mechanistic models of rare diseases.

| Metric | Value |
|--------|-------|
| Disorders modeled | 507 |
| Ontologies referenced | 16 (MONDO, HP, GO, CL, UBERON, ...) |
| Ontology terms per model | ~10-50 |
| Total term references | Thousands |

**Challenge:** Every term must be valid across 16 ontologies. Manual checking is impossible at this scale.

---

## DisMech: CI-Driven Validation

```yaml
# GitHub Actions workflow
- name: Validate ontology terms
  run: |
    linkml-term-validator validate-data \
      dismech_data.yaml \
      --schema dismech_schema.yaml \
      --labels \
      --cache-dir cache/
```

**Results:**
- Catches invalid/deprecated terms on every PR
- Label drift detected automatically when ontologies update
- Cache committed to repo — validation runs in seconds
- Contributors get immediate feedback, no ontology expertise needed

---

## High-Impact Use Case: AI Curation Guardrails

LLMs are increasingly used to generate ontology-annotated data. But they **hallucinate identifiers** — producing structurally valid CURIEs that don't exist or have wrong labels.

```yaml
# AI-generated annotation — looks plausible but wrong
term:
  id: GO:0042995
  label: DNA repair  # Actually "src64B" — hallucinated label
```

**The fix: dual validation.** Require both ID and label, validate both:

```yaml
term:
  id: GO:0005515
  label: protein binding  # Must match canonical label in ontology
```

The AI must get **two interdependent facts correct simultaneously**.

---

## LTV in an AI Curation Pipeline

```python
from linkml.validator import Validator
from linkml_term_validator.plugins import BindingValidationPlugin

plugin = BindingValidationPlugin(validate_labels=True)
validator = Validator(
    schema="schema.yaml",
    validation_plugins=[plugin]
)

# Validate AI-generated data before committing
report = validator.validate(ai_generated_data)

if len(report.results) > 0:
    # Reject hallucinated terms, prompt AI to regenerate
    raise ValueError("Invalid ontology terms detected")
```

Embed validation **during** AI generation, not just post-hoc.

---

## DisMech + AI: Validation at Scale

In the DisMech project, **AI agents curate 500+ disorder models**, each referencing dozens of terms across 16 ontologies.

**Without LTV:**
- Hallucinated terms enter the knowledge base undetected
- Manual review of thousands of term references is infeasible
- Errors compound as downstream analyses build on bad data

**With LTV:**
- Every AI-generated term validated against live ontologies
- Label mismatches caught immediately
- Scope violations detected (e.g., phenotype used where disease expected)
- CI pipeline rejects invalid PRs before human review

---

## Comparison with Alternatives

| Approach | Pros | Cons |
|----------|------|------|
| **Manual review** | Flexible | Doesn't scale, error-prone |
| **Custom scripts** | Tailored | Fragile, per-project, no reuse |
| **SHACL/ShEx** | Standards-based | RDF-only, complex setup |
| **JSON Schema** | Widely supported | No ontology awareness |
| **linkml-term-validator** | Ontology-aware, composable, cached | LinkML-specific |

**Key differentiator:** native integration with LinkML schemas and OAK ontology infrastructure.

---

## OAK Configuration

Control which ontologies are validated and how:

```yaml
# oak_config.yaml
ontology_adapters:
  GO: sqlite:obo:go        # Local SQLite database
  MONDO: sqlite:obo:mondo  # Auto-downloaded by OAK
  HP: sqlite:obo:hp
  CL: sqlite:obo:cl
  CUSTOM: ""                # Skip validation for this prefix
```

- Default: `sqlite:obo:` (auto-creates per-prefix adapters)
- Supports any OAK adapter: SQLite, OLS, BioPortal, local OBO files
- Unknown prefixes tracked and reported

---

## Design Principles

1. **Composable** — use one plugin or all three together
2. **Non-invasive** — standard LinkML ValidationPlugin interface
3. **Fast** — multi-level caching, progressive by default
4. **Reproducible** — commit cache for deterministic CI
5. **Flexible** — any OAK adapter, configurable per prefix
6. **Informative** — clear error messages with expected vs. actual

---

## Future Directions

**Validation Enhancements:**
- Obsolescence checking (warn on deprecated terms)
- Cross-reference validation (are mappings current?)
- Relationship validation (not just existence, but semantic type)

**Performance:**
- Parallel ontology queries
- Shared cache across projects

**Integration:**
- GitHub Actions marketplace action
- Pre-commit hook support
- VS Code / IDE integration for real-time validation

---

## Getting Started

```bash
# Install
pip install linkml-term-validator

# Validate your schema's ontology references
linkml-term-validator validate-schema my_schema.yaml

# Validate data against dynamic enums and bindings
linkml-term-validator validate-data my_data.yaml -s my_schema.yaml --labels
```

**Documentation:** https://linkml.github.io/linkml-term-validator/
**Repository:** https://github.com/linkml/linkml-term-validator
**PyPI:** https://pypi.org/project/linkml-term-validator/

---

<!-- _class: lead -->

## linkml-term-validator

### Keeping Ontology References Honest

**Try it today:**
```bash
pip install linkml-term-validator
```

**Questions?**
Christopher J. Mungall - cjmungall@lbl.gov
Lawrence Berkeley National Laboratory
