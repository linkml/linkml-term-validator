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

### Broken-adapter safeguards

`reachable_from` validation fails loudly (CLI exit code `3`) rather than
silently mislabeling terms when the configured ontology adapter returns a
broken graph. Two distinct defects are caught:

**1. Reachability inconsistent by CURIE (`InconsistentReachabilityError`).**
A correct ontology is round-trip consistent: if `D` is a descendant of `S`,
then `S` is among `D`'s ancestors. The progressive per-value check answers
"is `S` an ancestor of the value?" by **CURIE**, so an adapter that reports a
term under a different CURIE than the one used to root the enum returns wrong
negatives with no error. Before reporting such a negative, the validator samples
a few of the source node's descendants and checks the round trip; if a genuine
descendant does not report the source node among its ancestors by CURIE, it
raises instead.

The motivating case
([dismech#7012](https://github.com/monarch-initiative/dismech/issues/7012)) is a
CURIE/identifier merge, **not** a broken hierarchy: OLS4 conflates
`MONDO:0000001` with a cross-ontology term also labelled "disease" and returns
the term at IRI `.../MONDO_0000001` under the wrong `obo_id` `AFO_O:0000001`.
The hierarchy is *correct by IRI* — the root really is an ancestor — but every
CURIE-matching consumer (oaklib's OLS adapter, and therefore this validator)
saw the ancestor as `AFO_O:0000001`, so `MONDO:0000001` was unmatchable by CURIE
and every MONDO term silently failed ancestor-based reachability.

That specific OLS defect was fixed upstream
([EBISPOT/ols4#1334](https://github.com/EBISPOT/ols4/issues/1334)); the guard is
retained as a general safety net against this class of adapter identifier
corruption (and is covered end-to-end by an OLS integration test).

**2. Empty expansion (`EmptyReachableClosureError`).** If the enum's top-level
`reachable_from` resolves at least one source node yet the **whole enum** expands
to nothing, the enum matches no term and a greedy/materialized expansion would
cache an *empty-but-complete* closure that poisons later runs. Expansion raises
instead of persisting it. The decision is made against the **entire expanded
enum** — including any `permissible_values`, `concepts`, `include:` and
`inherits:` contributions — so a source node whose only contribution would be
empty is fine as long as *something else* populates the enum. (A `reachable_from`
nested only inside an `include:` branch is not guarded here; it is fail-safe —
never a false abort — but can still cache an empty closure.)

This also fires when the top-level `reachable_from` closure is **non-empty** but a
`minus:` (or other set operation) removes every term — the enum still matches
nothing. In that case the error says so explicitly (a schema/set-arithmetic
problem, not a misconfigured adapter), so you're pointed at the `minus:`/`include:`
clauses rather than the source node.

`include_self: true` does not mask the check: when the traversal reaches nothing
real and the enum's only members are its own `reachable_from` source nodes (and the
enum declares no `concepts`/`permissible_values`/`matches`/`include`/`inherits`
clause that could have contributed them), the enum is treated as empty and flagged.
A legitimate union that lists a branch root together with a specific sub-branch
(`source_nodes: [parent, child]`) still expands normally — the check keys on whether
any source actually reached a term, not on subtracting source nodes from the result.

The **round-trip check (1)** never flags a legitimate config:

- A genuinely out-of-enum term keeps the two directions in agreement (it is
  absent from both), so a correct negative is never flagged.
- A **childless leaf source** reaches nothing, so nothing round-trips — a
  correct negative under it stays a quiet `False` on the progressive path.
- A **multi-source union** (a leaf branch alongside a populated one) still
  round-trips through the populated branch.

The **empty-expansion check (2)** fires when the enum effectively matches nothing
— either the whole expansion is empty, or (with `include_self: true`) the only
members are the source nodes themselves because the traversal reached nothing, as
described above. Such an enum matches no useful term, so failing loud (rather than
silently materializing an empty closure) is the safe outcome; a union with any
populated branch still expands non-empty and is unaffected.

The fix is to configure a local, deterministic adapter (e.g.
`sqlite:obo:mondo`) for the affected prefix, or correct the source node.

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
