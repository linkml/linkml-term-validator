# Plugin Reference

`linkml-term-validator` provides LinkML `ValidationPlugin` implementations plus a standalone schema validator. Import plugins from `linkml_term_validator.plugins` unless you need an internal module directly.

## Shared Options

The ontology-backed plugins share these constructor options:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `oak_adapter_string` | `sqlite:obo:` | Default OAK adapter string |
| `cache_labels` | `True` | Write ontology labels to the file cache |
| `cache_dir` | `cache` | Directory for label and enum caches |
| `oak_config_path` | `None` | Path to `oak_config.yaml` |
| `offline` | `False` | Read only from cache; never build OAK adapters |
| `severity_overrides` | `None` | Map an error mode to the severity it is reported at |
| `check_not4curation` | `True` | Flag terms their ontology marks as not for annotation (see [Not4Curation check](#not4curation-check)) |
| `not4curation_markers` | `None` | Custom marker substrings; `None` uses the built-in list |

Dynamic enum capable plugins also accept:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `cache_enum_expansions` | `True` | Write dynamic enum expansion caches |
| `saturate_enum_caches` | `False` | Materialize full closures during progressive validation |
| `cache_strategy` | `progressive` | `progressive` or `greedy` |

### Severity Overrides

Every result the plugins emit carries an error mode (the result's `type`) and a
default severity. `severity_overrides` remaps any of them:

```python
from linkml_term_validator.plugins import BindingValidationPlugin

plugin = BindingValidationPlugin(
    severity_overrides={"binding_label_mismatch": "WARN"},
)
```

This matters most in CI. `linkml-validate` exits non-zero **only** when a result
has severity `ERROR`, so anything reported at `WARN` is printed while the
process still exits 0. Remapping a single mode lets a project tighten or relax
one specific check without touching the severity of everything else.

The available error modes, and the severity each is reported at by default:

| Error mode | Default | Emitted by |
|------------|---------|------------|
| `binding_validation` | `ERROR` | `BindingValidationPlugin` |
| `binding_label_invalid` | `ERROR` | `BindingValidationPlugin` |
| `binding_label_mismatch` | `ERROR` | `BindingValidationPlugin` |
| `binding_not4curation` | `ERROR` | `BindingValidationPlugin` |
| `term_not_found` | `ERROR` | `BindingValidationPlugin` |
| `dynamic_enum_validation` | `ERROR` | `DynamicEnumPlugin` |
| `dynamic_enum_not4curation` | `ERROR` | `DynamicEnumPlugin` |
| `permissible_value_meaning` | `ERROR` | `PermissibleValueMeaningPlugin` |
| `permissible_value_obsolete` | `ERROR` | `PermissibleValueMeaningPlugin` |
| `permissible_value_not4curation` | `ERROR` | `PermissibleValueMeaningPlugin` |
| `permissible_value_label_mismatch` | `WARN` (`ERROR` under `strict_mode`) | `PermissibleValueMeaningPlugin` |

Keys and values accept either strings or the `ErrorMode` / `Severity` enum
members; severities are case-insensitive and `WARNING` is accepted as an alias
for `WARN`. An unrecognized key or severity raises `ValueError` rather than
being ignored, so a typo cannot silently leave a problem at its default
severity.

The mapping may also live in `oak_config.yaml`, alongside the adapter map:

```yaml
ontology_adapters:
  GO: sqlite:obo:go

severity_overrides:
  binding_label_mismatch: WARN
```

Constructor arguments win over the config file. For
`permissible_value_label_mismatch`, an explicit override wins over `strict_mode`.

### Not4Curation check

Some ontologies keep terms for hierarchy completeness that they explicitly do
**not** want used for annotation, and mark them with a synonym rather than an
obsoletion axiom. RGD's ontologies (XCO, CMO, MMO, RS) write a related synonym
reading literally `Not4Curation`; some other OBO ontologies use
`not_recommended_for_annotation`. Such a term exists, has a matching label,
is not obsolete, and is reachable from a `reachable_from` source node, so it
passed every other check here. `XCO:0000294` (estrogen/estrogen analog) is a
real example: it validated cleanly as an exposure term while its own
maintainers say not to use it (see
[issue #70](https://github.com/linkml/linkml-term-validator/issues/70)).

Every ontology-backed plugin now reads a term's aliases and reports it when
any alias carries a "do not annotate" marker:

| Error mode | Where |
|------------|-------|
| `binding_not4curation` | a bound field's value that passed its enum check |
| `dynamic_enum_not4curation` | a slot value that passed a dynamic enum check |
| `permissible_value_not4curation` | a permissible value's `meaning` |

The message quotes the ontology's own wording:

```
ERROR: Ontology term XCO:0000294 is marked 'Not4Curation' by its ontology (not recommended for annotation)
```

**Matching.** Aliases are folded to lowercase alphanumerics and tested for each
marker as a substring, so `Not4Curation`, `not4curation` and
`not_recommended_for_annotation` all hit. The default markers are
`not4curation`, `notforcuration` and `notrecommendedforannotation`. The check
is deliberately not restricted to particular ontologies: on one that never
uses the convention it simply never matches, at the cost of one alias query
per term (near-free for a local `sqlite:` adapter; for `ols:` it reads the
term payload the label lookup already fetched, so no extra round trip).

**Fail by default.** All three modes default to `ERROR`. A warning nobody
reads reproduces the exact gap the check closes, so demote it only while
working through a backlog:

```yaml
severity_overrides:
  binding_not4curation: WARN
```

**Switching off or narrowing.** In `oak_config.yaml` (read by the plugins, by
`EnumValidator`, and so by every CLI command):

```yaml
check_not4curation: false          # disable entirely
not4curation_markers:              # or replace the marker list
  - not4curation
  - do_not_annotate
```

or per plugin with `check_not4curation=False` / `not4curation_markers=[...]`,
or on the CLI with `--no-check-not4curation`. Like `cache_strategy`, the
config-file keys win over the constructor argument.

**Terms that could not be checked.** A marker *is* a synonym, so a term whose
synonyms could not be read has not been vetted. That happens offline (synonyms
are not cached), for a prefix with no adapter, or for an adapter that exposes
no aliases. Those CURIEs are collected rather than treated as clean:
`plugin.get_not4curation_unchecked()` and `EnumValidator.get_not4curation_unchecked()`
return them, and the CLI prints them as a non-gating note:

```
ℹ️  Not4Curation check skipped for 3 term(s) (offline: synonyms are not cached, so the marker cannot be read):
  - XCO:0000294
  ...
  Run once online to check these terms.
```

**Existing caches.** The enum cache is the offline positive-hit set for
`reachable_from`. A flagged CURIE cached before this check existed still
validates offline, and offline there is no way to read the marker. The check
runs on the accepted value on every online run, so one online pass surfaces
every flagged term already in the cache; you do not need to rebuild the cache
to adopt it, but an offline-only pipeline will keep reporting those terms as
unchecked until it runs online once.

#### Which commands honor it

`severity_overrides` changes the severity a *plugin* reports at, so it takes
effect wherever that plugin runs — but whether the severity changes an exit code
depends on the command:

| Command | Effect |
|---------|--------|
| `linkml-validate` | Full effect: exits non-zero only on `ERROR` |
| `linkml-term-validator validate-data` | Results are reported at the new severity, but the default `--fail-on any` exits 1 on any result regardless. Pass `--fail-on error` to make the severity decide. There is no `severity_overrides` CLI flag — set it in `oak_config.yaml` and pass that with `-c`, as with other plugin config |
| `linkml-term-validator validate-schema` | **No effect.** This command uses `EnumValidator`, a separate implementation that does not run `PermissibleValueMeaningPlugin`. Use `strict_mode` there |

## PermissibleValueMeaningPlugin

Validates schema enum permissible value `meaning` fields. It checks that each CURIE resolves and that the ontology label matches one of the schema-provided labels.

```python
from linkml_term_validator.plugins import PermissibleValueMeaningPlugin

plugin = PermissibleValueMeaningPlugin(
    oak_adapter_string="sqlite:obo:",
    oak_config_path="oak_config.yaml",
    strict_mode=False,
    cache_labels=True,
    cache_dir="cache",
    offline=False,
)
```

Additional option:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `strict_mode` | `False` | Treat warnings as errors |

For direct schema validation outside LinkML's plugin lifecycle, use `EnumValidator`:

```python
from linkml_term_validator.models import ValidationConfig
from linkml_term_validator.validator import EnumValidator

config = ValidationConfig(
    oak_config_path="oak_config.yaml",
    cache_dir="cache",
)
validator = EnumValidator(config)
result = validator.validate_schema("schema.yaml")

if result.has_errors():
    for issue in result.issues:
        print(f"{issue.severity}: {issue.message}")
```

## DynamicEnumPlugin

Validates data values whose slot range is a dynamic enum.

```python
from linkml.validator import Validator
from linkml.validator.loaders import YamlLoader
from linkml_term_validator.plugins import DynamicEnumPlugin

plugin = DynamicEnumPlugin(
    oak_adapter_string="sqlite:obo:",
    oak_config_path="oak_config.yaml",
    cache_strategy="progressive",
)

validator = Validator(schema="schema.yaml", validation_plugins=[plugin])
loader = YamlLoader("data.yaml")
report = validator.validate_source(loader, target_class="Sample")
```

It validates:

- `reachable_from`: descendants by default, or ancestors when `traverse_up: true`
- `include_self`: source nodes are excluded by default and included only when true
- `concepts`: explicit concept lists
- `include`, `minus`, and `inherits`: enum expression composition
- static `permissible_values`: both permissible value names and `meaning` CURIEs

`matches` expressions are recognized as dynamic enum definitions and included in cache keys, but full pattern-match validation is not implemented yet.

## BindingValidationPlugin

Validates LinkML `bindings` on nested objects and, by default, validates labels on the same nested object.

```python
from linkml.validator import Validator
from linkml.validator.loaders import YamlLoader
from linkml_term_validator.plugins import BindingValidationPlugin

plugin = BindingValidationPlugin(
    oak_adapter_string="sqlite:obo:",
    oak_config_path="oak_config.yaml",
    validate_labels=True,
    strict=True,
)

validator = Validator(schema="schema.yaml", validation_plugins=[plugin])
loader = YamlLoader("data.yaml")
report = validator.validate_source(loader, target_class="GeneAnnotation")
```

Additional options:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `validate_labels` | `True` | Check nested label fields against ontology labels |
| `strict` | `True` | Fail when configured term IDs cannot be resolved |

The plugin:

- Recursively walks nested objects, including multivalued nested objects
- Applies `binds_value_of` to extract the field constrained by the binding
- Validates static and dynamic enum ranges
- Uses slots with `implements: [rdfs:label]` or `slot_uri: rdfs:label` as label fields
- Falls back to a field named `label` when no label property is declared

## Composition

Use both data plugins when a schema has direct dynamic enum slots and nested binding objects:

```python
from linkml.validator import Validator
from linkml.validator.loaders import YamlLoader
from linkml_term_validator.plugins import DynamicEnumPlugin, BindingValidationPlugin

plugins = [
    DynamicEnumPlugin(oak_config_path="oak_config.yaml"),
    BindingValidationPlugin(oak_config_path="oak_config.yaml"),
]

validator = Validator(schema="schema.yaml", validation_plugins=plugins)
report = validator.validate_source(YamlLoader("data.yaml"), target_class="Sample")
```

The CLI `validate-data` command creates this composition by default. Disable either part with `--no-dynamic-enums` or `--no-bindings`.

## linkml-validate Configuration

All plugins can be configured for `linkml-validate`:

```yaml
schema: schema.yaml
target_class: Sample
data_sources:
  - data.yaml

plugins:
  "linkml_term_validator.plugins.DynamicEnumPlugin":
    oak_adapter_string: "sqlite:obo:"
    oak_config_path: oak_config.yaml
    cache_dir: cache
    cache_strategy: progressive

  "linkml_term_validator.plugins.BindingValidationPlugin":
    oak_adapter_string: "sqlite:obo:"
    oak_config_path: oak_config.yaml
    validate_labels: true
    cache_dir: cache
    cache_strategy: progressive
```

```bash
linkml-validate --config validation_config.yaml
```

## See Also

- [CLI Reference](cli-reference.md)
- [Configuration](configuration.md)
- [Caching](caching.md)
- [Schema Validation](schema-validation.md)
- [Data Validation](data-validation.md)
- [Binding Validation](binding-validation.md)
