# Bindings: Constraining Complex Objects

Bindings are a powerful LinkML feature that constrain fields within complex (nested) objects to specific value sets. This page explains the concept, draws parallels with FHIR terminology bindings, and shows how linkml-term-validator implements binding validation.

## The Problem: Validating Nested Structures

Consider a schema for gene annotations:

```yaml
classes:
  GeneAnnotation:
    attributes:
      gene:
        range: string
      ontology_term:
        range: OntologyTerm
        inlined: true

  OntologyTerm:
    attributes:
      id:
        range: string
      label:
        range: string
```

This allows any `OntologyTerm`:

```yaml
gene: BRCA1
ontology_term:
  id: GO:0007049       # Valid GO term
  label: cell cycle

gene: TP53
ontology_term:
  id: PIZZA:MARGHERITA  # Nonsense!
  label: delicious
```

**Problem:** The `OntologyTerm` class is generic—it accepts any CURIE. But for gene annotations, we want to restrict terms to the Gene Ontology.

## The Solution: Bindings

Bindings constrain a specific field within a complex object to an enum range:

```yaml
classes:
  GeneAnnotation:
    attributes:
      gene:
        range: string
      ontology_term:
        range: OntologyTerm
        inlined: true
        bindings:
          - binds_value_of: id
            range: GOBiologicalProcessEnum

enums:
  GOBiologicalProcessEnum:
    reachable_from:
      source_ontology: obo:go
      source_nodes:
        - GO:0008150  # biological_process
      relationship_types:
        - rdfs:subClassOf
```

Now the `id` field within `ontology_term` must be a GO biological process term.

## Anatomy of a Binding

```yaml
bindings:
  - binds_value_of: id              # Which field to constrain
    range: GOBiologicalProcessEnum  # The enum defining allowed values
    obligation_level: RECOMMENDED   # How strictly to enforce
```

### Binding Properties

| Property | Description |
|----------|-------------|
| `binds_value_of` | The field path within the nested object to validate |
| `range` | The enum (static or dynamic) defining allowed values |
| `obligation_level` | `REQUIRED`, `RECOMMENDED`, or `OPTIONAL` |

## Comparison with FHIR Terminology Bindings

LinkML bindings are conceptually similar to [FHIR terminology bindings](https://build.fhir.org/terminologies.html), which connect data elements to controlled vocabularies.

### FHIR Binding Strengths

FHIR defines five binding strengths:

| Strength | Description | LinkML Equivalent |
|----------|-------------|-------------------|
| **Required** | Must use a code from the value set | `obligation_level: REQUIRED` |
| **Extensible** | Should use value set codes if applicable | `obligation_level: RECOMMENDED` |
| **Preferred** | Encouraged but not required | `obligation_level: RECOMMENDED` |
| **Example** | Illustrative only | No validation |

### FHIR CodeableConcept

In FHIR, a `CodeableConcept` is similar to LinkML's pattern of an object with `id` and `label`:

```json
{
  "coding": [
    {
      "system": "http://snomed.info/sct",
      "code": "386661006",
      "display": "Fever"
    }
  ],
  "text": "Fever"
}
```

FHIR bindings constrain which codes are valid for a CodeableConcept in a given context—exactly what LinkML bindings do.

### Key Insight from FHIR

FHIR's documentation emphasizes: *"A binding strength of 'required' or 'extensible' does not indicate that ALL of the codes in the bound value set will be supported. It constrains the set of codes that are allowed to be shared."*

This is equally true in LinkML: bindings constrain what values are **valid**, not what values implementations must support.

## Use Cases for Bindings

### 1. Descriptor Pattern

Wrap ontology terms in descriptive objects:

```yaml
classes:
  DiseaseDescriptor:
    attributes:
      preferred_term:
        range: string
      synonyms:
        range: string
        multivalued: true
      term:
        range: OntologyTerm
        bindings:
          - binds_value_of: id
            range: DiseaseEnum
```

### 2. Multi-Context Annotations

Same class, different bindings in different contexts:

```yaml
classes:
  Sample:
    attributes:
      tissue:
        range: OntologyTerm
        bindings:
          - binds_value_of: id
            range: AnatomyEnum

      cell_type:
        range: OntologyTerm
        bindings:
          - binds_value_of: id
            range: CellTypeEnum
```

### 3. Deeply Nested Structures

Bindings validate at all nesting levels:

```yaml
classes:
  Study:
    attributes:
      samples:
        range: Sample
        multivalued: true

  Sample:
    attributes:
      annotations:
        range: Annotation
        multivalued: true

  Annotation:
    attributes:
      term:
        range: OntologyTerm
        bindings:
          - binds_value_of: id
            range: AnnotationTermEnum
```

## Label Validation

Beyond validating the `id`, you may want to verify the `label` matches the ontology:

```yaml
classes:
  OntologyTerm:
    attributes:
      id:
        identifier: true
      label:
        implements:
          - rdfs:label  # Declares this field holds the rdfs:label
```

With `--labels` flag, the validator checks that provided labels match the ontology's canonical labels.

## Anti-Hallucination Benefits

Bindings are particularly valuable for validating AI-generated content. By requiring both:

1. A valid ontology term ID
2. A matching label

...you ensure the AI correctly retrieved real ontology data rather than hallucinating plausible-looking identifiers. See [Anti-Hallucination Guardrails](anti-hallucination.md).

## Validation with linkml-term-validator

### CLI

```bash
# Validate bindings (and dynamic enums)
linkml-term-validator validate-data data.yaml -s schema.yaml -t ClassName

# Also validate labels match ontology
linkml-term-validator validate-data data.yaml -s schema.yaml -t ClassName --labels
```

### Python API

```python
from linkml_term_validator.plugins import BindingValidationPlugin

plugin = BindingValidationPlugin(
    validate_labels=True,
    oak_adapter_string="sqlite:obo:"
)
```

## Common Patterns

### Reusable Term Classes

Define a generic term class, constrain via bindings:

```yaml
classes:
  Term:
    attributes:
      id:
        identifier: true
      label:
        implements:
          - rdfs:label

  GeneAnnotation:
    attributes:
      process:
        range: Term
        bindings:
          - binds_value_of: id
            range: BiologicalProcessEnum
      location:
        range: Term
        bindings:
          - binds_value_of: id
            range: CellularComponentEnum
```

### Slot Usage for Context-Specific Bindings

Override bindings in subclasses:

```yaml
classes:
  Annotation:
    attributes:
      term:
        range: Term

  GeneAnnotation:
    is_a: Annotation
    slot_usage:
      term:
        bindings:
          - binds_value_of: id
            range: GOTermEnum
```

## See Also

- [Ontologies in LinkML](ontologies-primer.md) - Background on ontologies
- [Enumerations](enumerations.md) - Static and dynamic enums
- [Binding Validation Reference](binding-validation.md) - Detailed validation behavior
- [Anti-Hallucination Guardrails](anti-hallucination.md) - Using bindings for AI validation

## External Resources

- [LinkML Bindings Documentation](https://linkml.io/linkml-model/latest/docs/bindings/)
- [FHIR Terminology Bindings](https://build.fhir.org/terminologies.html)
- [FHIR Binding Strength](https://build.fhir.org/valueset-binding-strength.html)
