# Anti-Hallucination Guardrails

A key use case for linkml-term-validator is preventing AI systems from hallucinating ontology identifiers.

## The Problem

Language models frequently hallucinate identifiers like gene IDs, ontology terms, and other structured references. These fake identifiers often appear structurally correct (e.g., `GO:9999999`, `CHEBI:88888`) but don't actually exist in the source ontologies.

This creates serious data quality issues:

- **Invalid references** that break data integration
- **Nonsense annotations** that corrupt curated datasets
- **False confidence** in AI-generated content

## The Solution: Dual Validation

A robust guardrail requires **dual validation**—forcing the AI to provide both the identifier AND its canonical label, then validating that they match:

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

## Implementation Pattern

### 1. Define Schemas with Binding Constraints

```yaml
prefixes:
  rdfs: http://www.w3.org/2000/01/rdf-schema#

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
    slot_usage:
      label:
        implements:
          - rdfs:label  # Explicit: this field should match ontology label

enums:
  BiologicalProcessEnum:
    reachable_from:
      source_ontology: sqlite:obo:go
      source_nodes:
        - GO:0008150  # biological_process
      relationship_types:
        - rdfs:subClassOf
```

The `implements: [rdfs:label]` declaration explicitly tells the validator that this field should be validated against the ontology's `rdfs:label`. This is more robust than relying on naming conventions.

### 2. Validate AI-Generated Outputs Before Committing

```python
from linkml.validator import Validator
from linkml_term_validator.plugins import BindingValidationPlugin

# Create validator with label checking enabled
plugin = BindingValidationPlugin(
    oak_adapter_string="sqlite:obo:",
    validate_labels=True
)
validator = Validator(schema="schema.yaml", validation_plugins=[plugin])

# Validate AI-generated data
report = validator.validate_source(loader, target_class="GeneAnnotation")

if len(report.results) > 0:
    # Reject hallucinated terms, prompt AI to regenerate
    for result in report.results:
        print(f"ERROR: {result.message}")
    raise ValueError("Invalid ontology terms detected")
```

### 3. Use Validation During Generation

The most effective approach embeds validation **during AI generation** rather than treating it as a filtering step afterward. This transforms hallucination resistance from a detection problem into a generation constraint.

**Pattern:**

1. AI generates structured output with id+label pairs
2. Validate immediately with `BindingValidationPlugin(validate_labels=True)`
3. If validation fails, provide error messages back to AI with retry opportunity
4. Only accept outputs that pass validation

## What Gets Validated

The `BindingValidationPlugin` with `validate_labels=True` checks:

1. **ID exists in ontology**: The identifier (e.g., `GO:0005515`) is a real term
2. **ID matches constraint**: The term satisfies the binding's range constraint (e.g., is a biological process)
3. **Label matches ontology**: The provided label matches the canonical label from the ontology

All three checks must pass.

## Real-World Benefits

- **Prevents fake identifiers** from entering curated datasets
- **Catches label mismatches** where AI uses real IDs but wrong labels
- **Validates dynamic constraints** (e.g., only disease terms, only neuron types)
- **Enables reliable automation** of curation tasks traditionally requiring human experts

## Example: Invalid AI Output

```yaml
annotations:
  - gene: BRCA1
    go_term:
      id: GO:0005515
      label: DNA binding  # ❌ WRONG - actual label is "protein binding"
```

**Validation result:**
```
ERROR: Label mismatch for GO:0005515
  Expected: protein binding
  Found: DNA binding
```

## Example: Hallucinated ID

```yaml
annotations:
  - gene: BRCA1
    go_term:
      id: GO:9999999  # ❌ Doesn't exist
      label: cell stuff
```

**Validation result:**
```
ERROR: GO:9999999 not found in Gene Ontology
```

## CLI Usage

```bash
# Validate with label checking enabled
linkml-term-validator validate-data data.yaml \
  --schema schema.yaml \
  --labels  # Enable label validation
```

## Best Practices

1. **Always use dual validation** (id + label) for AI-generated ontology references
2. **Provide clear error messages** back to the AI when validation fails
3. **Use dynamic enums** to constrain valid terms (e.g., only disease terms)
4. **Cache ontology labels** for fast validation during generation
5. **Validate early and often** - don't wait until after bulk generation

## Learn More

For detailed patterns and best practices on making ontology IDs hallucination-resistant in AI workflows, see:

- [Make IDs Hallucination Resistant](https://ai4curation.io/aidocs/how-tos/make-ids-hallucination-resistant/) - Comprehensive guide from the AI for Curation project
- [Python API Tutorial](notebooks/03_python_api.ipynb) - Interactive notebook demonstrating validation workflows
- [Advanced Usage](notebooks/02_advanced_usage.ipynb) - CLI patterns for binding validation

## See Also

- [Binding Validation](validation-types.md#binding-validation) - Technical details
- [Python API](notebooks/03_python_api.ipynb) - Programmatic usage
