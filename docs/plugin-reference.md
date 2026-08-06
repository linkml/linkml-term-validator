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
    severity_overrides={"binding_label_mismatch": "ERROR"},
)
```

This matters most in CI. `linkml-validate` exits non-zero **only** when a result
has severity `ERROR`, so a label mismatch — which defaults to `WARN` — is
printed but the process still exits 0. Promoting the mode makes the same
mismatch a hard failure, without turning every unrelated warning into one.

The available error modes, and the severity each is reported at by default:

| Error mode | Default | Emitted by |
|------------|---------|------------|
| `binding_validation` | `ERROR` | `BindingValidationPlugin` |
| `binding_label_invalid` | `WARN` | `BindingValidationPlugin` |
| `binding_label_mismatch` | `WARN` | `BindingValidationPlugin` |
| `term_not_found` | `ERROR` | `BindingValidationPlugin` |
| `dynamic_enum_validation` | `ERROR` | `DynamicEnumPlugin` |
| `permissible_value_meaning` | `ERROR` | `PermissibleValueMeaningPlugin` |
| `permissible_value_obsolete` | `ERROR` | `PermissibleValueMeaningPlugin` |
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
  binding_label_mismatch: ERROR
```

Constructor arguments win over the config file. For
`permissible_value_label_mismatch`, an explicit override wins over `strict_mode`.

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
