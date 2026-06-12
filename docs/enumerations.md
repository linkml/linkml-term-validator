# Enumerations in LinkML: Static and Dynamic

LinkML provides two approaches to constraining values: **static enums** with explicit permissible values, and **dynamic enums** that query ontologies at validation time. Understanding when to use each is key to effective schema design.

## Static Enums

Static enums define a fixed list of allowed values directly in the schema.

### Basic Static Enum

```yaml
enums:
  SampleStatusEnum:
    permissible_values:
      PENDING:
      PROCESSING:
      COMPLETED:
      FAILED:
```

Data must match one of these exact strings:

```yaml
# Valid
status: COMPLETED

# Invalid - "completed" not in enum
status: completed
```

### Static Enum with Ontology Mappings

The `meaning` property connects permissible values to ontology terms:

```yaml
prefixes:
  GO: http://purl.obolibrary.org/obo/GO_

enums:
  BiologicalProcessEnum:
    permissible_values:
      CELL_CYCLE:
        title: cell cycle
        meaning: GO:0007049
      DNA_REPLICATION:
        title: DNA replication
        meaning: GO:0006260
      APOPTOSIS:
        title: apoptotic process
        meaning: GO:0006915
```

This approach:

- Uses human-readable keys (`CELL_CYCLE`)
- Provides display titles (`cell cycle`)
- Links to authoritative ontology terms (`GO:0007049`)

### When to Use Static Enums

Static enums are appropriate when:

| Scenario | Example |
|----------|---------|
| **Small, stable value sets** | Status codes, priority levels |
| **Domain-specific codes** | Internal project identifiers |
| **Curated subsets** | Carefully selected ontology terms |
| **Performance-critical** | No runtime ontology lookup needed |

### Validation of Static Enums

**Schema validation** (`PermissibleValueMeaningPlugin`) checks:

- Each `meaning` CURIE exists in the ontology
- The `title` matches the ontology's label (optional)

```bash
linkml-term-validator validate-schema schema.yaml
```

## Dynamic Enums

Dynamic enums define allowed values via ontology queries, evaluated at validation time.

### Basic Dynamic Enum

```yaml
enums:
  CellTypeEnum:
    reachable_from:
      source_ontology: obo:cl
      source_nodes:
        - CL:0000000  # cell
      include_self: false
      relationship_types:
        - rdfs:subClassOf
```

This allows any descendant of "cell" in the Cell Ontology—potentially thousands of terms—without listing each one.

### Dynamic Enum Parameters

| Parameter | Description | Example |
|-----------|-------------|---------|
| `source_ontology` | OAK adapter string | `obo:cl`, `sqlite:obo:go` |
| `source_nodes` | Root term(s) for the query | `[CL:0000540]` |
| `relationship_types` | Edge types to traverse | `[rdfs:subClassOf]` |
| `include_self` | Include source nodes in results | `true` or `false` |

### Common Patterns

**All subtypes of a term:**
```yaml
enums:
  NeuronTypeEnum:
    reachable_from:
      source_ontology: obo:cl
      source_nodes:
        - CL:0000540  # neuron
      include_self: false
      relationship_types:
        - rdfs:subClassOf
```

**Multiple source nodes:**
```yaml
enums:
  CancerOrInfectiousDisease:
    reachable_from:
      source_ontology: obo:mondo
      source_nodes:
        - MONDO:0004992  # cancer
        - MONDO:0005550  # infectious disease
      relationship_types:
        - rdfs:subClassOf
```

**Part-of relationships:**
```yaml
enums:
  BrainPartEnum:
    reachable_from:
      source_ontology: obo:uberon
      source_nodes:
        - UBERON:0000955  # brain
      relationship_types:
        - BFO:0000050  # part-of
```

### When to Use Dynamic Enums

Dynamic enums are appropriate when:

| Scenario | Example |
|----------|---------|
| **Large value sets** | All cell types, all diseases |
| **Evolving ontologies** | New terms added regularly |
| **Branch-based constraints** | "Any GO biological process" |
| **Avoiding maintenance** | Don't want to update schema with each ontology release |

### Validation of Dynamic Enums

**Data validation** (`DynamicEnumPlugin`) checks:

- The data value exists in the expanded enum
- Expansion queries the ontology via OAK

```bash
linkml-term-validator validate-data data.yaml -s schema.yaml -t ClassName
```

## Static vs Dynamic: Trade-offs

| Aspect | Static Enum | Dynamic Enum |
|--------|-------------|--------------|
| **Schema size** | Grows with values | Constant (just the query) |
| **Validation speed** | Fast (string match) | Slower (ontology query) |
| **Maintenance** | Manual updates | Automatic with ontology |
| **Offline use** | Always works | Needs ontology access |
| **Explicit control** | Full control over values | Delegate to ontology |
| **JSON Schema export** | Direct | Requires materialization |

## Materializing Dynamic Enums

For tools that don't support dynamic queries (like JSON Schema validators), you can materialize dynamic enums into static lists:

```bash
# Using OAK's vskit
vskit expand -s schema.yaml -o schema_expanded.yaml
```

This creates a schema with static enums populated from the query results.

## Combining Static and Dynamic

You can combine both approaches:

```yaml
enums:
  # Static subset for common cases
  CommonCellTypes:
    permissible_values:
      NEURON:
        meaning: CL:0000540
      HEPATOCYTE:
        meaning: CL:0000182
      CARDIOMYOCYTE:
        meaning: CL:0000746

  # Dynamic for full flexibility
  AllCellTypes:
    reachable_from:
      source_ontology: obo:cl
      source_nodes:
        - CL:0000000
      relationship_types:
        - rdfs:subClassOf
```

## See Also

- [Ontologies in LinkML](ontologies-primer.md) - Background on ontologies
- [Bindings Explained](bindings-explained.md) - Constraining complex objects
- [Schema Validation Reference](schema-validation.md) - Validating static enum meanings

## External Resources

- [LinkML Semantic Enumerations](https://linkml.io/linkml/schemas/enums.html)
- [LinkML Tutorial: Enumerations](https://linkml.io/linkml/intro/tutorial06.html)
