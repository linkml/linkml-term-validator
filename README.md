# linkml-term-validator

Validating LinkML schemas and datasets that depend on external terms

A collection of [LinkML ValidationPlugin](https://linkml.io/linkml/code/validator.html) implementations for validating ontology term references:

1. **Schema Validation**: Validate `meaning` fields in enum permissible values
2. **Data Validation**: Validate data against dynamic enums and binding constraints

## Features

* ✅ Three composable validation plugins for LinkML validator framework
* ✅ Validates `meaning` fields in `permissible_values` in LinkML schemas
* ✅ Validates data against dynamic enums (reachable_from, matches, concepts)
* ✅ Validates binding constraints on nested object fields
* ✅ Supports multiple ontology sources via [OAK (Ontology Access Kit)](https://github.com/INCATools/ontology-access-kit)
* ✅ Multi-level caching (in-memory + file-based) for fast repeated validation
* ✅ Configurable per-prefix validation via `oak_config.yaml`
* ✅ Standalone CLI + LinkML validator integration
* ✅ Tracks unknown ontology prefixes

## Installation

```bash
pip install linkml-term-validator
```

Or with `uv`:

```bash
uv add linkml-term-validator
```


## Quick Start

For interactive tutorials, see the [Jupyter notebooks](notebooks/) in the `notebooks/` directory.

### Validate Schemas

Check that `meaning` fields in your schema reference valid ontology terms:

```bash
linkml-term-validator validate-schema schema.yaml
```

### Validate Data

Validate data instances against dynamic enums and binding constraints:

```bash
linkml-term-validator validate-data data.yaml --schema schema.yaml
```

The `validate-data` command checks:
- **Dynamic enums** - values match `reachable_from`, `matches`, or `concepts` definitions
- **Binding constraints** - nested object fields satisfy binding ranges
- **Labels** (optional with `--labels`) - ontology term labels match

## Examples

### Schema Validation

Here's a LinkML schema that uses ontology terms:

```yaml
id: https://example.org/my-schema
name: my-schema
prefixes:
  GO: http://purl.obolibrary.org/obo/GO_
  CHEBI: http://purl.obolibrary.org/obo/CHEBI_

enums:
  BiologicalProcessEnum:
    description: Examples of biological processes
    permissible_values:
      BIOLOGICAL_PROCESS:
        title: biological process
        meaning: GO:0008150
      CELL_CYCLE:
        title: cell cycle
        meaning: GO:0007049

  ChemicalEntityEnum:
    description: Examples of chemical entities
    permissible_values:
      WATER:
        title: water
        meaning: CHEBI:15377
      GLUCOSE:
        title: glucose
        meaning: CHEBI:17234
```

When you run validation:

```bash
linkml-term-validator my-schema.yaml
```

The validator will:
1. Check that `GO:0008150` exists and has label "biological_process" (or "biological process")
2. Check that `GO:0007049` exists and has label "cell cycle"
3. Check that `CHEBI:15377` exists and has label "water"
4. Check that `CHEBI:17234` exists and has label "glucose"
5. Report any mismatches or missing terms

### Example Output

```
Validation Results for my-schema.yaml
============================================================
Enums checked: 2
Values checked: 4
Meanings validated: 4

✅ No issues found!
```

Or if there's an issue:

```
⚠️  WARNING: Label mismatch
    Enum: BiologicalProcessEnum
    Value: BIOLOGICAL_PROCESS
    Expected label: biological process
    Found label: biological_process
    Meaning: GO:0008150

Validation Results for my-schema.yaml
============================================================
Enums checked: 2
Values checked: 4
Meanings validated: 4

Issues found: 1
  Warnings: 1
  Errors: 0
```

### Data Validation

#### Example 1: Dynamic Enums

Schema with a dynamic enum using `reachable_from`:

```yaml
enums:
  NeuronTypeEnum:
    description: Any neuron type
    reachable_from:
      source_ontology: obo:cl
      source_nodes:
        - CL:0000540  # neuron
      relationship_types:
        - rdfs:subClassOf
```

Data file with neuron instances:

```yaml
neurons:
  - id: "1"
    cell_type: CL:0000540  # neuron - valid
  - id: "2"
    cell_type: CL:0000100  # neuron associated cell - valid (descendant)
  - id: "3"
    cell_type: GO:0008150  # biological process - INVALID
```

Validate:

```bash
linkml-term-validator validate-data neurons.yaml --schema schema.yaml
```

Output:
```
❌ Validation failed with 1 issue(s):

❌ ERROR: Value 'GO:0008150' not in dynamic enum NeuronTypeEnum
    Expected one of the descendants of CL:0000540
```

#### Example 2: Binding Constraints

Schema with binding constraints:

```yaml
classes:
  GeneAnnotation:
    slots:
      - gene
      - go_term
    slot_usage:
      go_term:
        range: GOTerm
        bindings:
          - binds_value_of: id
            range: BiologicalProcessEnum

  GOTerm:
    slots:
      - id
      - label
```

Data file:

```yaml
annotations:
  - gene: BRCA1
    go_term:
      id: GO:0008150  # biological_process
      label: biological process
```

Validate with label checking:

```bash
linkml-term-validator validate-data annotations.yaml --schema schema.yaml --labels
```

## Caching

The validator uses multi-level caching to speed up repeated validations:

### In-Memory Cache
During a single validation run, ontology labels are cached in memory. This means if multiple permissible values use the same ontology term, it's only looked up once.

### File-Based Cache
Labels are persisted to CSV files in the cache directory (default: `cache/`). The cache is organized by ontology prefix:

```
cache/
├── go/
│   └── terms.csv      # GO term labels
├── chebi/
│   └── terms.csv      # CHEBI term labels
└── uberon/
    └── terms.csv      # UBERON term labels
```

Each CSV contains:
```csv
curie,label,retrieved_at
GO:0008150,biological_process,2025-11-15T10:30:00
GO:0007049,cell cycle,2025-11-15T10:30:01
```

### Cache Behavior

- **First run**: Queries ontology databases, saves to cache
- **Subsequent runs**: Loads from cache files (very fast!)
- **Cache location**: Configurable via `--cache-dir` flag
- **Disable caching**: Use `--no-cache` flag

### When to Clear Cache

You might want to clear the cache if:
- Ontology databases have been updated
- You suspect stale or incorrect labels

```bash
# Clear cache for specific ontology
rm -rf cache/go/

# Clear entire cache
rm -rf cache/
```

## Advanced Configuration

### Per-Prefix Adapter Configuration

Create an `oak_config.yaml` to control which ontologies are validated:

```yaml
ontology_adapters:
  GO: sqlite:obo:go           # Use local GO database
  CHEBI: sqlite:obo:chebi     # Use local CHEBI database
  UBERON: sqlite:obo:uberon   # Use local UBERON database
  CUSTOM: ""                   # Skip validation for CUSTOM prefix
```

Then validate with this config:

```bash
linkml-term-validator schema.yaml --config oak_config.yaml
```

**Important**: When using `oak_config.yaml`, ONLY the prefixes listed in the config will be validated. Any prefix not in the config will be tracked as "unknown" and reported at the end of validation.

### Default Behavior (No Config File)

Without an `oak_config.yaml`, the validator uses `sqlite:obo:` as the default adapter. This automatically creates per-prefix adapters:

- `GO:0008150` → uses `sqlite:obo:go`
- `CHEBI:15377` → uses `sqlite:obo:chebi`
- `UBERON:0000468` → uses `sqlite:obo:uberon`

This works for any OBO ontology that has been downloaded via OAK.

## Usage

**linkml-term-validator** supports two main validation use cases:

#### 1. Schema Validation

Validates `meaning` fields in enum permissible values.

**CLI:**
```bash
# Validate schema permissible values
linkml-term-validator validate-schema schema.yaml

# With strict mode (warnings become errors)
linkml-term-validator validate-schema --strict schema.yaml

# With custom config
linkml-term-validator validate-schema --config oak_config.yaml schema.yaml
```

**Python API:**
```python
from linkml.validator import Validator
from linkml_term_validator.plugins import PermissibleValueMeaningPlugin

plugin = PermissibleValueMeaningPlugin(
    oak_adapter_string="sqlite:obo:",
    strict_mode=False
)

validator = Validator(schema="schema.yaml", validation_plugins=[plugin])
report = validator.validate("schema.yaml")

if len(report.results) == 0:
    print("Valid!")
else:
    for result in report.results:
        print(f"{result.severity}: {result.message}")
```

#### 2. Data Validation

Validates data instances against dynamic enums and binding constraints.

**CLI:**
```bash
# Validate data (checks both dynamic enums and bindings)
linkml-term-validator validate-data data.yaml --schema schema.yaml

# With specific target class
linkml-term-validator validate-data data.yaml -s schema.yaml -t Person

# Also validate labels match ontology
linkml-term-validator validate-data data.yaml -s schema.yaml --labels

# Only check bindings, skip dynamic enums
linkml-term-validator validate-data data.yaml -s schema.yaml --no-dynamic-enums

# Only check dynamic enums, skip bindings
linkml-term-validator validate-data data.yaml -s schema.yaml --no-bindings
```

Data validation includes two aspects:

##### Dynamic Enums

Validates against enums defined via `reachable_from`, `matches`, `concepts`.

Example schema:
```yaml
enums:
  NeuronTypeEnum:
    reachable_from:
      source_ontology: obo:cl
      source_nodes: [CL:0000540]  # neuron
      relationship_types: [rdfs:subClassOf]
```

**Python API:**
```python
from linkml.validator import Validator
from linkml_term_validator.plugins import DynamicEnumPlugin

plugin = DynamicEnumPlugin(oak_adapter_string="sqlite:obo:")
validator = Validator(schema="schema.yaml", validation_plugins=[plugin])
report = validator.validate("data.yaml")
```

##### Binding Constraints

Validates nested object fields against binding constraints.

Example schema:
```yaml
classes:
  Annotation:
    slots:
      - term
    slot_usage:
      term:
        range: Term
        bindings:
          - binds_value_of: id
            range: GOTermEnum
```

**Python API:**
```python
from linkml.validator import Validator
from linkml_term_validator.plugins import BindingValidationPlugin

plugin = BindingValidationPlugin(
    validate_labels=True  # Also check labels match ontology
)
validator = Validator(schema="schema.yaml", validation_plugins=[plugin])
report = validator.validate("data.yaml")
```

### Combining Multiple Validations

**CLI:**
```bash
# Validate data with both dynamic enums and bindings (default)
linkml-term-validator validate-data data.yaml --schema schema.yaml

# With label validation enabled
linkml-term-validator validate-data data.yaml -s schema.yaml --labels
```

**Python API:**
```python
from linkml.validator import Validator
from linkml.validator.plugins import JsonschemaValidationPlugin
from linkml_term_validator.plugins import (
    DynamicEnumPlugin,
    BindingValidationPlugin,
)

# Comprehensive validation pipeline
plugins = [
    JsonschemaValidationPlugin(closed=True),  # Structural validation
    DynamicEnumPlugin(),                       # Dynamic enum validation
    BindingValidationPlugin(validate_labels=True),  # Binding validation
]

validator = Validator(schema="schema.yaml", validation_plugins=plugins)
report = validator.validate("data.yaml")
```

## Integration with linkml-validate

The **linkml-term-validator** plugins can be used directly with the standard `linkml-validate` command via configuration files.

### Using Config Files

Create a validation config file (e.g., `validation_config.yaml`):

```yaml
# Validation configuration for linkml-validate
schema: schema.yaml
target_class: Person

data_sources:
  - data.yaml

plugins:
  # Standard JSON Schema validation
  JsonschemaValidationPlugin:
    closed: true

  # Ontology term validation for dynamic enums
  "linkml_term_validator.plugins.DynamicEnumPlugin":
    oak_adapter_string: "sqlite:obo:"
    cache_labels: true
    cache_dir: cache

  # Binding constraint validation
  "linkml_term_validator.plugins.BindingValidationPlugin":
    oak_adapter_string: "sqlite:obo:"
    validate_labels: true
    cache_labels: true
    cache_dir: cache
```

Then run validation:

```bash
linkml-validate --config validation_config.yaml
```

### Example Files

See the [examples/](examples/) directory for complete examples:
- [simple_config.yaml](examples/simple_config.yaml) - Basic validation config
- [linkml_validate_config.yaml](examples/linkml_validate_config.yaml) - Full config with ontology plugins
- [simple_schema.yaml](examples/simple_schema.yaml) - Example schema
- [simple_data.yaml](examples/simple_data.yaml) - Example data

### Plugin Configuration Options

#### DynamicEnumPlugin

```yaml
"linkml_term_validator.plugins.DynamicEnumPlugin":
  oak_adapter_string: "sqlite:obo:"  # OAK adapter (default: sqlite:obo:)
  cache_labels: true                  # Enable label caching (default: true)
  cache_dir: cache                    # Cache directory (default: cache)
  oak_config_path: oak_config.yaml    # Optional: custom OAK config
```

#### BindingValidationPlugin

```yaml
"linkml_term_validator.plugins.BindingValidationPlugin":
  oak_adapter_string: "sqlite:obo:"  # OAK adapter (default: sqlite:obo:)
  validate_labels: true               # Check labels match ontology (default: false)
  cache_labels: true                  # Enable label caching (default: true)
  cache_dir: cache                    # Cache directory (default: cache)
  oak_config_path: oak_config.yaml    # Optional: custom OAK config
```

### Programmatic Usage

You can also use the plugins programmatically:

```python
from linkml.validator import Validator
from linkml.validator.plugins import JsonschemaValidationPlugin
from linkml_term_validator.plugins import (
    DynamicEnumPlugin,
    BindingValidationPlugin,
)

# Build validation pipeline
plugins = [
    JsonschemaValidationPlugin(closed=True),
    DynamicEnumPlugin(oak_adapter_string="sqlite:obo:"),
    BindingValidationPlugin(validate_labels=True),
]

# Create validator
validator = Validator(
    schema="schema.yaml",
    validation_plugins=plugins,
)

# Validate
report = validator.validate("data.yaml")

# Check results
if len(report.results) == 0:
    print("✅ Validation passed")
else:
    for result in report.results:
        print(f"{result.severity.name}: {result.message}")
```

## Repository Structure

* [docs/](docs/) - mkdocs-managed documentation
* [src/](src/) - source files (edit these)
  * [linkml_term_validator](src/linkml_term_validator)
* [tests/](tests/) - Python tests
  * [data/](tests/data) - Example data

## Developer Tools

There are several pre-defined command-recipes available.
They are written for the command runner [just](https://github.com/casey/just/). To list all pre-defined commands, run `just` or `just --list`.

## Anti-Hallucination Guardrails for Agentic AI

While **linkml-term-validator** is designed for standard data validation, it serves a crucial role as an **anti-hallucination guardrail** for agentic AI pipelines that generate ontology term references.

### The Problem: LLMs Hallucinate Identifiers

Language models frequently hallucinate identifiers like gene IDs, ontology terms, and other structured references. These fake identifiers often appear structurally correct (e.g., `GO:9999999`, `CHEBI:88888`) but don't actually exist in the source ontologies.

### The Solution: Dual Validation Pattern

A robust guardrail requires **dual validation**—forcing the AI to provide both the identifier and its canonical label, then validating that they match:

**Instead of accepting:**
```yaml
term: GO:0005515  # Single piece of information - easy to hallucinate
```

**Require and validate:**
```yaml
term:
  id: GO:0005515
  label: protein binding  # Must match canonical label in ontology
```

This dramatically reduces hallucinations because the AI must get **two interdependent facts correct simultaneously**, which is significantly harder to fake convincingly than inventing a single plausible-looking identifier.

### Implementation in AI Pipelines

Use **linkml-term-validator** to embed validation directly into your agentic workflow:

**1. Define schemas with binding constraints:**

```yaml
classes:
  GeneAnnotation:
    slots:
      - gene
      - go_term
    slot_usage:
      go_term:
        range: GOTerm
        bindings:
          - binds_value_of: id
            range: BiologicalProcessEnum

  GOTerm:
    slots:
      - id        # AI must provide both
      - label     # fields correctly
```

**2. Validate AI-generated outputs before committing:**

```python
from linkml.validator import Validator
from linkml_term_validator.plugins import BindingValidationPlugin

# Create validator with label checking enabled
plugin = BindingValidationPlugin(validate_labels=True)
validator = Validator(schema="schema.yaml", validation_plugins=[plugin])

# Validate AI-generated data
report = validator.validate(ai_generated_data)

if len(report.results) > 0:
    # Reject hallucinated terms, prompt AI to regenerate
    raise ValueError("Invalid ontology terms detected")
```

**3. Use validation during generation (not just post-hoc):**

The most effective approach embeds validation **during AI generation** rather than treating it as a filtering step afterward. This transforms hallucination resistance from a detection problem into a generation constraint.

### Real-World Benefits

- **Prevents fake identifiers** from entering curated datasets
- **Catches label mismatches** where AI uses real IDs but wrong labels
- **Validates dynamic constraints** (e.g., only disease terms, only neuron types)
- **Enables reliable automation** of curation tasks traditionally requiring human experts

### Learn More

For detailed patterns and best practices on making ontology IDs hallucination-resistant in AI workflows, see:

- [Make IDs Hallucination Resistant](https://ai4curation.io/aidocs/how-tos/make-ids-hallucination-resistant/) - Comprehensive guide from the AI for Curation project
- [Jupyter Notebooks](notebooks/) - Interactive tutorials demonstrating validation workflows

