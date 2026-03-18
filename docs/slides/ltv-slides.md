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

## The Problem: Ontology Terms Go Wrong

Data files reference thousands of ontology terms. Things break silently:

```yaml
# Wrong ID (doesn't exist)
disease_term: MONDO:9999999

# Stale label (ontology updated, your data didn't)
term:
  id: GO:0007049
  label: cell cycle process  # Was renamed to "cell cycle"

# Hallucinated by AI (structurally valid, semantically nonsense)
term:
  id: GO:0042995
  label: DNA repair  # Actually "src64B" — AI hallucinated the label
```

These errors propagate silently through pipelines.

---

## Why This Is Hard

Validating ontology terms requires:

- Access to **hundreds of ontologies** (GO, MONDO, HP, CL, CHEBI, ...)
- Checking term **existence** (does this CURIE resolve?)
- Checking **labels** match (is "cell cycle" correct for GO:0007049?)
- Checking **semantic constraints** (is this term actually a disease?)
- Doing all this **fast enough** for CI pipelines

No standard tool did all of this for LinkML data.

---

## LinkML Enumerations: Two Approaches

### Static Enums
```yaml
enums:
  VitalStatusEnum:
    permissible_values:
      ALIVE:
        meaning: NCIT:C37987
      DECEASED:
        meaning: NCIT:C28554
```

### Dynamic Enums
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

A collection of **LinkML ValidationPlugin** implementations that validate ontology term references against live ontologies.

**Three composable plugins:**

| Plugin | Validates |
|--------|-----------|
| `PermissibleValueMeaningPlugin` | `meaning` fields in static enums |
| `DynamicEnumPlugin` | Data against dynamic enums |
| `BindingValidationPlugin` | Binding constraints on nested objects |

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
# Data: AI generated this
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

## Anti-Hallucination: The Dual Validation Pattern

LLMs hallucinate ontology IDs. The fix: require **both** ID and label.

**Instead of:**
```yaml
term: GO:0005515  # Easy to hallucinate a single value
```

**Require:**
```yaml
term:
  id: GO:0005515
  label: protein binding  # Must match canonical label
```

The AI must get **two interdependent facts correct simultaneously** — much harder to fake than a single plausible-looking CURIE.

---

## Dual Validation in Practice

```python
from linkml.validator import Validator
from linkml_term_validator.plugins import BindingValidationPlugin

plugin = BindingValidationPlugin(validate_labels=True)
validator = Validator(
    schema="schema.yaml",
    validation_plugins=[plugin]
)

report = validator.validate(ai_generated_data)

if len(report.results) > 0:
    # Reject hallucinated terms, prompt AI to regenerate
    raise ValueError("Invalid ontology terms detected")
```

Embed validation **during** AI generation, not just post-hoc.

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
| Offline validation | ❌ Fails | Works |

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
