# Semantic Artefacts in LinkML: A Primer

This page introduces ontologies, vocabularies, and controlled value sets—collectively called **semantic artefacts**—and how they integrate with LinkML schemas for term validation.

## What are Semantic Artefacts?

**Semantic artefacts** are structured collections of terms with formal definitions. They include:

- **Ontologies** - Rich hierarchical structures with relationships (Gene Ontology, SNOMED CT)
- **Controlled vocabularies** - Curated term lists with definitions (ISO codes, industry standards)
- **Value sets** - Enumerated lists of allowed values (status codes, categories)
- **Code systems** - Standardized identifier schemes (country codes, currency codes)

All share a common pattern: **terms with unique identifiers and human-readable labels**.

## Why Validation Matters: The Opaque ID Problem

Many standardized code systems use **opaque identifiers**—codes that don't reveal their meaning:

| System | Code | Meaning |
|--------|------|---------|
| ISO 4217 (Currency) | `CHF` | Swiss Franc |
| ISO 3166-1 (Country) | `CHE` | Switzerland |
| NAICS (Industry) | `5112` | Software Publishers |
| OMB Race/Ethnicity | `2106-3` | White |
| Gene Ontology | `GO:0007049` | cell cycle |
| ICD-10 | `E11.9` | Type 2 diabetes mellitus without complications |

When IDs are opaque, **you can't tell if data is correct by looking at it**. Is `CHE` a country or currency? Is `5112` the right industry code? Without validation, errors slip through.

This is where linkml-term-validator helps: it verifies that codes exist in their source vocabularies and that labels match.

## Anatomy of a Term

Whether from an ontology or a simple code list, terms share common elements:

```
┌─────────────────────────────────────────────┐
│  Term: GO:0007049                           │
├─────────────────────────────────────────────┤
│  Label: cell cycle                          │
│  Definition: The series of events by which  │
│              a cell replicates...           │
│  Synonyms: cell division cycle, CDC         │
│  Parent: GO:0008150 (biological_process)    │
└─────────────────────────────────────────────┘
```

For validation, the key properties are:

- **Identifier (CURIE)**: Unique, stable reference
- **Label**: Canonical human-readable name
- **Relationships**: Hierarchy enabling "is-a" queries

## CURIEs: Compact Identifiers

Terms are identified by **CURIEs** (Compact URIs):

```
GO:0008150
│   └──── Local identifier
└──────── Prefix (namespace)
```

CURIEs expand to full URIs:

- `GO:0008150` → `http://purl.obolibrary.org/obo/GO_0008150`
- `ISO4217:CHF` → (currency code for Swiss Franc)
- `NAICS:5112` → (Software Publishers sector)

LinkML schemas declare prefixes:

```yaml
prefixes:
  GO: http://purl.obolibrary.org/obo/GO_
  ISO4217: https://example.org/iso4217/
  NAICS: https://example.org/naics/
```

## Examples Across Domains

### Life Sciences

| Vocabulary | Prefix | Domain | Example |
|------------|--------|--------|---------|
| Gene Ontology | GO | Biological processes | `GO:0007049` (cell cycle) |
| Cell Ontology | CL | Cell types | `CL:0000540` (neuron) |
| MONDO | MONDO | Diseases | `MONDO:0004975` (Alzheimer disease) |
| ChEBI | CHEBI | Chemicals | `CHEBI:15377` (water) |

### Industry & Standards

| Vocabulary | Prefix | Domain | Example |
|------------|--------|--------|---------|
| ISO 4217 | ISO4217 | Currency codes | `CHF` (Swiss Franc) |
| ISO 3166-1 | ISO3166 | Country codes | `CHE` (Switzerland) |
| NAICS | NAICS | Industry sectors | `5112` (Software Publishers) |
| OMB Categories | OMB | Race/ethnicity | `2106-3` (White) |

### Environmental & Materials

| Vocabulary | Prefix | Domain | Example |
|------------|--------|--------|---------|
| ENVO | ENVO | Environments | `ENVO:00000015` (ocean) |
| ChEBI | CHEBI | Chemical elements | `CHEBI:33256` (gallium) |

See the [LinkML Value Sets collection](https://linkml.io/valuesets/) for many more examples.

## The Data Consistency Problem

When collecting data across teams or systems, inconsistency creeps in:

**Without controlled vocabularies:**

```yaml
# Team A
location: "ocean"

# Team B
location: "Ocean"

# Team C
location: "marine environment"

# Team D
location: "sea"
```

**With controlled vocabularies:**

```yaml
# All teams use the same identifier
location:
  id: ENVO:00000015
  label: ocean
```

Now data is:

- **Consistent**: Same concept, same ID
- **Validatable**: We can check `ENVO:00000015` exists
- **Interoperable**: Systems can exchange and merge data
- **Queryable**: Find all marine samples via hierarchy

This pattern applies whether you're tracking environmental samples, financial transactions, or patient diagnoses.

## Hierarchical Queries

Many semantic artefacts organize terms hierarchically:

```
ENVO:00000015 (ocean)
├── ENVO:00000016 (sea)
├── ENVO:01000023 (coastal ocean)
└── ENVO:01000024 (deep ocean)
    ├── ENVO:01000025 (hadal zone)
    └── ENVO:01000026 (abyssal zone)
```

This enables powerful queries:

- "All ocean environments" = `ENVO:00000015` and all its descendants
- Dynamic enums can express this: "any subtype of ocean"

## LinkML Integration Patterns

### 1. Static Enum Meanings

Map your codes to authoritative terms:

```yaml
enums:
  CurrencyEnum:
    permissible_values:
      USD:
        meaning: ISO4217:USD
        title: US Dollar
      EUR:
        meaning: ISO4217:EUR
        title: Euro
      CHF:
        meaning: ISO4217:CHF
        title: Swiss Franc
```

### 2. Dynamic Enums

Query a vocabulary for allowed values:

```yaml
enums:
  OceanEnvironmentEnum:
    reachable_from:
      source_ontology: obo:envo
      source_nodes:
        - ENVO:00000015  # ocean
      relationship_types:
        - rdfs:subClassOf
```

### 3. Bindings on Complex Objects

Constrain fields within nested structures:

```yaml
classes:
  Sample:
    attributes:
      environment:
        range: EnvironmentTerm
        bindings:
          - binds_value_of: id
            range: OceanEnvironmentEnum
```

## See Also

- [Enumerations in LinkML](enumerations.md) - Static and dynamic enums
- [Bindings Explained](bindings-explained.md) - Complex object constraints
- [Ontology Access](ontology-access.md) - OAK configuration details

## External Resources

- [LinkML Value Sets](https://linkml.io/valuesets/) - Collection of commonly used value sets
- [LinkML Helps with Ontology Use](https://oboacademy.github.io/obook/lesson/linkml-helps-with-ontology-use/) - OBO Academy tutorial
- [LinkML Semantic Enumerations](https://linkml.io/linkml/schemas/enums.html) - Official documentation
- [OBO Foundry](https://obofoundry.org/) - Open biomedical ontologies
- [Ontology Lookup Service](https://www.ebi.ac.uk/ols/) - Search ontologies online
