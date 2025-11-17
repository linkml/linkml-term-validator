# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

linkml-term-validator is a collection of LinkML ValidationPlugin implementations for validating ontology term references in LinkML schemas and datasets. The project provides three composable plugins:

1. **PermissibleValueMeaningPlugin** - Validates `meaning` fields in enum permissible values
2. **DynamicEnumPlugin** - Validates data against dynamic enums (reachable_from, matches, concepts)
3. **BindingValidationPlugin** - Validates binding constraints on nested object fields

All plugins use OAK (Ontology Access Kit) for ontology access and support multi-level caching (in-memory + file-based) for performance.

The project uses a `./cache` folder for storing validation data.

## Development Environment

This project uses modern Python tooling with `uv` for dependency management.

### Setup

```bash
just install
```

This runs `uv sync --group dev` to install all dependencies including dev tools.

## Common Commands

The project uses `just` as the command runner. All common operations are defined in `justfile`.

### Testing

```bash
just test           # Run all tests: pytest, mypy, and format checks (excludes integration)
just test-full      # Run all tests including integration tests
just pytest         # Run pytest only (unit tests, excludes integration)
just pytest-integration  # Run ALL tests including integration tests
just doctest        # Run doctests from source modules
just mypy           # Run type checking
just format         # Run ruff linting
```

To run a single test file:
```bash
uv run pytest tests/test_simple.py
```

To run a specific test:
```bash
uv run pytest tests/test_simple.py::test_simple
```

#### Integration Tests

Integration tests are marked with `@pytest.mark.integration` and are skipped by default.
These tests make real calls to ontology services (OLS, etc.) and require network access.

Run integration tests explicitly:
```bash
just pytest-integration  # or: uv run pytest -m ""
```

Run only integration tests:
```bash
uv run pytest -m integration
```

Integration tests use `requests-cache` to cache HTTP responses, speeding up repeated runs.
Cache is stored in `tests/output/requests-cache.sqlite`.

### Documentation

```bash
just _serve         # Run mkdocs documentation server (internal recipe)
just render-notebooks  # Execute notebooks and render to HTML for docs (recommended)
just run-notebooks  # Run notebooks with papermill (testing only)
just run-notebook NOTEBOOK  # Run a specific notebook
just jupyter        # Start Jupyter Lab for interactive notebook development
just clean-notebooks  # Clean notebook outputs
```

**Notebook Rendering Approach:**
- Source notebooks live in `notebooks/` (git-tracked)
- `just render-notebooks` executes with papermill → converts to HTML with nbconvert
- HTML output goes to `docs/notebooks/` (git-ignored, rendered in mkdocs)
- We use pre-rendered HTML instead of mkdocs plugins for:
  - Separation of concerns (execution failures don't break docs build)
  - Fast mkdocs builds (just copies pre-rendered HTML)
  - Full Jupyter Lab styling via nbconvert
  - CI-friendly workflow

## Project Structure

- `src/linkml_term_validator/` - Main source code
  - `cli.py` - Typer-based CLI interface
  - `plugins/` - ValidationPlugin implementations
    - `permissible_value_meaning_plugin.py` - Schema validation
    - `dynamic_enum_plugin.py` - Dynamic enum validation
    - `binding_validation_plugin.py` - Binding constraint validation
  - `utils/` - Shared utilities
    - `oak_utils.py` - OAK adapter management and caching
  - `__init__.py` - Package initialization
  - `_version.py` - Version management
- `tests/` - Test suite
  - `data/` - Example test data (schemas, data files, OBO files)
  - `test_cli.py` - CLI tests using CliRunner
  - `test_dynamic_enums.py` - Dynamic enum validation tests
  - Other test files
- `docs/` - MkDocs documentation
- `notebooks/` - Jupyter notebook tutorials (source notebooks, not in docs/)
  - `01_getting_started.ipynb` - Basic usage tutorial
  - `02_advanced_usage.ipynb` - Advanced features (bindings, custom configs)
  - `output/` - Generated notebook outputs (git-ignored)
- `examples/` - Example usage of the datamodel
- `project.justfile` - Project-specific just recipes (notebook automation)

## CLI Architecture

The project provides a single CLI command `linkml-term-validator` with subcommands using Typer:

- `validate-schema` - Validate schema permissible values (uses PermissibleValueMeaningPlugin)
- `validate-data` - Validate data against dynamic enums and bindings (uses DynamicEnumPlugin and/or BindingValidationPlugin)

The CLI is defined in `src/linkml_term_validator/cli.py` and registered in `pyproject.toml` under `[project.scripts]`.

### CLI Testing

CLI tests use `typer.testing.CliRunner` (the canonical approach for Typer/Click testing). See `tests/test_cli.py` for examples. DO NOT use subprocess-based testing.

## Testing Practices

- Uses pytest with parametrization and fixtures
- Doctests in source modules are encouraged
- Test data lives in `tests/data/`
  - `test_ontology.obo` - Local OBO file for fast offline unit tests (use `simpleobo:tests/data/test_ontology.obo`)
  - `test_oak_config.yaml` - OAK config mapping TEST prefix to local file
  - `dynamic_enum_schema.yaml` - Schema with dynamic enums for testing
- CLI tests use `typer.testing.CliRunner` (see `tests/test_cli.py`)
- Examples output is generated in `examples/output/` (git-ignored)
- Notebook outputs generated in `notebooks/output/` (git-ignored)

### Unit Testing with Local OBO Files

For fast, offline unit tests that need ontology data, use the `simpleobo` adapter with local OBO files:

```python
# In test data
source_ontology: simpleobo:tests/data/test_ontology.obo

# With OAK config to map prefixes
ontology_adapters:
  TEST: simpleobo:tests/data/test_ontology.obo
```

This avoids network calls and database downloads during unit testing.

## Code Quality Tools

- **Type checking**: mypy with configuration in `mypy.ini`
- **Linting**: ruff
- **Spell checking**: codespell and typos (configured in `pyproject.toml`)
- **Pre-commit hooks**: `.pre-commit-config.yaml`

## Important Notes

- The `.github/copilot-instructions.md` is a symlink to this file (created by `just goosehints`)
- The `.goosehints` file is also a symlink to this file
- Python version requirement: >=3.10,<4.0

## Key Implementation Details

### Validator API

The correct way to use the LinkML Validator API:

```python
from linkml.validator import Validator
from linkml_runtime.loaders import yaml_loader

validator = Validator(schema="schema.yaml", validation_plugins=[plugin])
loader = yaml_loader.YamlLoader()
report = validator.validate_source(loader, target_class="ClassName")  # NOT validate_from_source()

# Check results
if len(report.results) == 0:  # NOT report.valid()
    print("Valid!")
```

### Dynamic Enum Semantics

When using `reachable_from` in dynamic enums, note that:
- Source nodes themselves are EXCLUDED by default
- Only descendants (via specified relationship types) are included
- Test data should use child/descendant terms, not the source node itself

### OAK Configuration

- Default adapter: `sqlite:obo:` (auto-creates per-prefix adapters)
- Custom adapters via `oak_config.yaml` (see `examples/oak_config.yaml`)
- For unit tests: use `simpleobo:path/to/file.obo` for local OBO files
