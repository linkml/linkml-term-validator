# OAK provider examples

Run these commands from the repository root.

## simpleobo

Local OBO file, no network:

```bash
uv run linkml-term-validator validate-data \
  examples/oak_providers/simpleobo_data.yaml \
  --schema examples/oak_providers/simpleobo_schema.yaml \
  --target-class Sample \
  --config examples/oak_providers/simpleobo_oak_config.yaml \
  --no-cache
```

## OLS

Online OLS lookup. Use progressive validation so individual submitted values are checked with `ancestors()` instead of materializing a full enum closure:

```bash
uv run linkml-term-validator validate-data \
  examples/oak_providers/ols_data.yaml \
  --schema examples/oak_providers/ols_schema.yaml \
  --target-class Sample \
  --config examples/oak_providers/ols_oak_config.yaml \
  --no-cache
```

## Ubergraph

Online Ubergraph lookup:

```bash
uv run linkml-term-validator validate-data \
  examples/oak_providers/ubergraph_data.yaml \
  --schema examples/oak_providers/ubergraph_schema.yaml \
  --target-class Sample \
  --config examples/oak_providers/ubergraph_oak_config.yaml \
  --no-cache
```

## OWL

OAK's `owl:` adapter supports local Functional OWL files with graph traversal in `oaklib>=0.7.0rc7`.

```bash
uv run --with oaklib==0.7.0rc7 python -m linkml_term_validator.cli validate-data \
  examples/oak_providers/owl_data.yaml \
  --schema examples/oak_providers/owl_schema.yaml \
  --target-class Sample \
  --config examples/oak_providers/owl_oak_config.yaml \
  --no-cache
```
