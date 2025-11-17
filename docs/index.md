# linkml-term-validator

**Validating LinkML schemas and datasets that depend on external ontology terms**

A collection of [LinkML ValidationPlugin](https://linkml.io/linkml/code/validator.html) implementations for validating ontology term references in schemas and data.

## Key Features

- ✅ **Three composable validation plugins** for the LinkML validator framework
- ✅ **Schema validation** - Validates `meaning` fields in enum permissible values
- ✅ **Dynamic enum validation** - Validates data against `reachable_from`, `matches`, `concepts`
- ✅ **Binding validation** - Validates constraints on nested object fields
- ✅ **Multi-level caching** - In-memory + file-based for fast repeated validation
- ✅ **Ontology Access Kit (OAK)** integration - Supports multiple ontology sources
- ✅ **AI hallucination prevention** - Dual validation (ID + label) for AI-generated terms

## Quick Start

### Installation

```bash
pip install linkml-term-validator
```

### Validate a Schema

Check that `meaning` fields reference valid ontology terms:

```bash
linkml-term-validator validate-schema schema.yaml
```

### Validate Data

Validate data instances against dynamic enums and binding constraints:

```bash
linkml-term-validator validate-data data.yaml --schema schema.yaml
```

## Documentation Quick Links

### Getting Started
- [Getting Started Tutorial](notebooks/01_getting_started.ipynb) - Interactive notebook for CLI basics
- [CLI Reference](cli-reference.md) - Complete command-line documentation
- [Configuration](configuration.md) - Configure ontology adapters and caching

### Understanding Validation
- [Validation Types](validation-types.md) - Schema, dynamic enum, and binding validation explained
- [Anti-Hallucination Guardrails](anti-hallucination.md) - Preventing AI from hallucinating ontology IDs
- [Ontology Access](ontology-access.md) - How OAK adapters work

### Integration
- [linkml-validate Integration](notebooks/04_linkml_validate_integration.ipynb) - Use plugins with standard linkml-validate
- [Python API](notebooks/03_python_api.ipynb) - Programmatic usage
- [Plugin Reference](plugin-reference.md) - Complete API documentation

### Advanced Topics
- [TSV/CSV Data Validation](notebooks/05_tsv_csv_validation.ipynb) - Validating tabular data
- [Advanced Usage](notebooks/02_advanced_usage.ipynb) - Custom configs, local files, troubleshooting
- [Caching](caching.md) - Understanding the caching system

## Use Cases

- **Schema Quality Assurance** - Catch typos and mismatches in ontology term references before publishing
- **Data Validation** - Ensure curated datasets only use valid, constrained ontology terms
- **AI-Generated Content** - Prevent language models from hallucinating fake ontology identifiers
- **CI/CD Integration** - Automated validation in continuous integration pipelines
- **Flexible Constraints** - Define valid terms via ontology queries rather than hardcoded lists
