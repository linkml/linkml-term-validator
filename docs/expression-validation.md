# Grammar-Based Codes (UCUM, SMILES, InChI)

**Plugin**: `ExpressionValidationPlugin`

Most of this package checks ontology terms: identifiers like `GO:0008150` that
are *looked up*. Some code systems have no list to look them up in. Their
valid codes are defined by a **grammar**, so the set of codes is open ended and
each value is validated by **parsing** it:

| Grammar | Example | What a valid value means | Parser | Install |
|---------|---------|--------------------------|--------|---------|
| `ucum` | `mg/dL`, `m.s-2`, `10*3/uL` | Parses as a [UCUM](https://ucum.org) (case-sensitive) unit expression | [ucumvert](https://pypi.org/project/ucumvert/) | `linkml-term-validator[ucum]` (Python ≥ 3.11) |
| `smiles` | `CCO` | Well-formed SMILES that also passes valence and aromaticity checks | [RDKit](https://www.rdkit.org) | `linkml-term-validator[chem]` |
| `inchi` | `InChI=1S/C2H6O/c1-2-3/h3H,2H2,1H3` | Accepted by the InChI library | RDKit | `linkml-term-validator[chem]` |
| `inchikey` | `LFQSCWFLJHTTHZ-UHFFFAOYSA-N` | Has the InChIKey layout (a key is a hash, so only its shape is checkable) | built in | nothing extra |

`pip install 'linkml-term-validator[expressions]'` installs every parser.

HL7/FHIR call these **grammar-based** code systems, and SNOMED CT calls the
same idea **compositional** or **post-coordinated**: a code is built from
parts at the point of use (`mg/dL` = `m` + `g` + `/` + `d` + `L`) instead of
being picked from a pre-built list. InChI is also **content-derived**: it is
computed from the structure it identifies.

## Declaring a grammar

Annotate a slot with `expression_grammar`:

```yaml
slots:
  smiles:
    range: string
    annotations:
      expression_grammar: smiles
```

Or annotate a type, and every slot whose range is that type (or a type
derived from it) is checked:

```yaml
types:
  UcumCode:
    typeof: string
    annotations:
      expression_grammar: ucum

slots:
  unit:
    range: UcumCode
```

Slots without the annotation are ignored, so turning the plugin on never
changes the result for a schema that does not use it.

## What it checks

- Every value of an annotated slot, including each item of a multivalued slot
- **At any depth**: like `BindingValidationPlugin`, it walks nested objects, so
  the `unit` of each `Measurement` inside a `Sample` is checked, reported with
  its path (`measurements[1].unit`)
- An empty value is invalid under every grammar

## Results

| Error mode | Default | Meaning |
|------------|---------|---------|
| `expression_invalid` | `ERROR` | The value does not parse; the message gives the parser's reason |
| `expression_grammar_unknown` | `ERROR` | The annotation names a grammar that does not exist (a typo in the schema) |
| `expression_grammar_unavailable` | `ERROR` | The grammar's parser is not installed, so those values were **not checked** |

A missing parser is an error, not a silent pass: the message names the extra to
install. It is reported once per grammar per data file. If skipping the
check is deliberate, demote the mode:

```python
from linkml_term_validator.plugins import ExpressionValidationPlugin

plugin = ExpressionValidationPlugin(
    severity_overrides={"expression_grammar_unavailable": "WARN"},
)
```

The plugin needs no ontology access, so it takes no adapter, cache, or
`oak_config.yaml` options; `severity_overrides` is passed to the constructor.

## Example

```yaml
# data.yaml
id: ex:broken
unit: mg//dL
smiles: C(C
measurements:
  - value: 1.0
    unit: furlongs
```

```bash
$ linkml-term-validator validate-data data.yaml -s schema.yaml
❌ Validation failed with 3 issue(s):

  ❌ ERROR: Value 'mg//dL' at unit: not a valid UCUM unit (No terminal matches '/' in the current parser context, at line 1 col 4)
      slot: unit
      grammar: ucum
      path: unit
  ❌ ERROR: Value 'C(C' at smiles: not valid SMILES syntax
      slot: smiles
      grammar: smiles
      path: smiles
  ❌ ERROR: Value 'furlongs' at measurements[0].unit: not a valid UCUM unit (No terminal matches 'r' in the current parser context, at line 1 col 3)
      slot: unit
      grammar: ucum
      path: measurements[0].unit
```

`validate-data` runs the plugin by default; `--no-expressions` turns it off.

## Adding a grammar

Other packages can register grammars under the `linkml_term_validator.grammars`
entry point group. The entry point must resolve to a `Grammar`:

```python
# my_package/grammars.py
from linkml_term_validator.plugins.expressions import Grammar

def _load():
    import my_parser  # imported only when a schema uses the grammar

    def check(value: str) -> str | None:
        """Return None if valid, else a short reason."""
        return None if my_parser.is_valid(value) else "not a valid HGVS expression"

    return check

HGVS = Grammar("hgvs", "HGVS", _load, extra=None)
```

```toml
# pyproject.toml of my_package
[project.entry-points."linkml_term_validator.grammars"]
hgvs = "my_package.grammars:HGVS"
```

If the loader raises `ImportError`, results report `expression_grammar_unavailable`
as for the built-ins. A registered grammar cannot replace a built-in one
(`ucum`, `smiles`, `inchi`, `inchikey`).

## Not checked (yet)

- Whether a SMILES, InChI, and InChIKey on the same record describe the same
  molecule, or match the structure of a linked `CHEBI:` term
- Whether a UCUM unit has the right dimension for its slot (for example, a
  concentration)
- `unit.ucum_code` values in the schema itself
