# Validation Types

linkml-term-validator provides three distinct types of validation, each implemented as a composable LinkML ValidationPlugin.

## Schema Validation (Permissible Values)

**Plugin**: `PermissibleValueMeaningPlugin`

Validates that `meaning` fields in enum permissible values reference valid ontology terms with correct labels.

### What it checks

- The ontology term exists in the source ontology
- The term's canonical label matches the expected label from the schema
- Expected labels can come from: permissible value name, `title`, `description`, `aliases`, or annotation values

### Example

**Schema:**

```yaml
enums:
  BiologicalProcessEnum:
    permissible_values:
      BIOLOGICAL_PROCESS:
        title: biological process
        meaning: GO:0008150  # ← Validated
```

**What gets validated:**

1. Check that `GO:0008150` exists in the Gene Ontology
2. Retrieve the canonical label from GO (should be "biological_process")
3. Compare against expected labels: "BIOLOGICAL_PROCESS", "biological process"
4. Report if there's a mismatch or if the term doesn't exist

### When to use

- Validating LinkML schemas before publishing
- CI/CD checks on schema files
- Catching typos in term references

### Commands

```bash
# Basic validation
linkml-term-validator validate-schema schema.yaml

# With strict mode
linkml-term-validator validate-schema --strict schema.yaml
```

## Dynamic Enum Validation

**Plugin**: `DynamicEnumPlugin`

Validates data values against dynamic enum constraints defined via `reachable_from`, `matches`, or `concepts`.

Dynamic enums define valid values using ontology queries rather than explicit lists, enabling flexible, extensible validation.

### What it checks

- Data values match the ontology query constraints
- For `reachable_from`: value is a descendant of specified source nodes
- For `matches`: value matches the specified pattern
- For `concepts`: value is one of the specified concepts

### Example

**Schema:**

```yaml
enums:
  NeuronTypeEnum:
    description: Any neuron type
    reachable_from:
      source_ontology: sqlite:obo:cl
      source_nodes:
        - CL:0000540  # neuron
      relationship_types:
        - rdfs:subClassOf
```

**Data:**

```yaml
neurons:
  - id: "1"
    cell_type: CL:0000100  # Valid - is a descendant of CL:0000540
  - id: "2"
    cell_type: GO:0008150  # INVALID - not a neuron type
```

### Important semantics

- Source nodes themselves are **EXCLUDED** by default
- Only descendants (via specified relationship types) are included
- Use actual descendant terms in your data, not the source node

### When to use

- Validating curated data files
- Ensuring data conforms to ontology-based constraints
- Flexible validation without hardcoding all valid values

### Commands

```bash
# Validate data with dynamic enums
linkml-term-validator validate-data data.yaml --schema schema.yaml

# With specific target class
linkml-term-validator validate-data data.yaml -s schema.yaml -t Person
```

## Binding Validation

**Plugin**: `BindingValidationPlugin`

Validates that nested object fields satisfy binding range constraints.

Bindings allow you to constrain the values of nested object fields based on dynamic enums, providing type safety for ontology references.

### What it checks

- The value in the nested object's field matches the binding's range constraint
- Optionally: the label in the nested object matches the ontology's canonical label
- **Recursively validates all nested structures** - bindings on deeply nested classes are validated with full JSON path tracking

### Explicit Label Field Declaration

Use `implements` or `slot_uri` to explicitly declare which fields should be validated as labels:

```yaml
classes:
  GOTerm:
    attributes:
      id:
        identifier: true
      name:
        implements:
          - rdfs:label  # Option 1: implements list
      # OR
      label:
        slot_uri: rdfs:label  # Option 2: slot_uri
```

Supported label properties:

- `rdfs:label` - Standard RDF label
- `skos:prefLabel` - SKOS preferred label
- `schema:name` - Schema.org name
- `oboInOwl:hasExactSynonym` - OBO exact synonym

If no `implements` or `slot_uri` is specified, the validator falls back to looking for a field named `label` (convention-based).

### Example

**Schema:**

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
            range: BiologicalProcessEnum  # ← Binding constraint

  GOTerm:
    slots:
      - id
      - label

enums:
  BiologicalProcessEnum:
    reachable_from:
      source_ontology: sqlite:obo:go
      source_nodes:
        - GO:0008150  # biological_process
      relationship_types:
        - rdfs:subClassOf
```

**Data:**

```yaml
annotations:
  - gene: BRCA1
    go_term:
      id: GO:0007049  # Valid - is a biological process
      label: cell cycle
```

**What gets validated:**

1. Check that `go_term.id` value (`GO:0007049`) satisfies the `BiologicalProcessEnum` constraint
2. If `--labels` flag is used, also check that `go_term.label` matches the canonical label from GO

### When to use

- Validating complex nested data structures
- Enforcing ontology constraints on object references
- Label validation for AI-generated data

### Commands

```bash
# Validate bindings only
linkml-term-validator validate-data data.yaml --schema schema.yaml

# Also validate labels match ontology
linkml-term-validator validate-data data.yaml --schema schema.yaml --labels
```

## Label Matching

When validating `meaning` fields, the validator compares the ontology's canonical label against multiple expected sources:

1. **Permissible value name** (e.g., `BIOLOGICAL_PROCESS`)
2. **Title field** (e.g., `title: biological process`)
3. **Description field**
4. **Aliases** (e.g., `aliases: [biological_process, bio process]`)
5. **Annotation values** for tags like `label`, `display_name`, `synonym`

Labels are normalized before comparison:

- Lowercased
- Spaces/underscores treated as equivalent
- Multiple whitespace collapsed to single space

This flexible matching reduces false positives from minor formatting differences.

## Severity Levels

Validation results are reported with different severity levels:

- **ERROR** - Serious issue that should block data/schema acceptance
  - Configured prefix with label mismatch
  - Missing term from configured ontology
  - Dynamic enum constraint violation
  - Binding constraint violation

- **WARN** - Potential issue that may need attention
  - Label mismatch in non-strict mode
  - Unconfigured prefix with label mismatch (when verbose)

- **INFO** - Informational message
  - Unconfigured prefix encountered
  - Unknown prefix summary

### Strict Mode

Use `--strict` to treat all warnings as errors:

```bash
linkml-term-validator validate-schema --strict schema.yaml
```

## See Also

- [Configuration](configuration.md) - How to configure ontology adapters
- [Caching](caching.md) - Understanding the caching system
- [Anti-Hallucination](anti-hallucination.md) - Using validation for AI guardrails
