# Tests

This directory contains unit tests and integration tests for linkml-term-validator.

## Test Organization

- **Unit Tests**: Fast, isolated tests with no external dependencies
  - `test_simple.py` - Example parametrized tests
  - `test_validator.py` - Core validation logic tests (with mocked ontology access)

- **Integration Tests**: Tests that make real calls to ontology services
  - `test_integration.py` - Real OLS/ontology service tests (marked with `@pytest.mark.integration`)

- **Test Data**:
  - `data/test_schema.yaml` - Example LinkML schema with ontology terms

- **Shared Fixtures**:
  - `conftest.py` - Pytest fixtures shared across all tests (includes requests caching)

## Running Tests

### Fast tests only (default)
```bash
just test           # or: uv run pytest
```

This runs only unit tests, skipping integration tests. This is the default mode and what runs in CI.

### All tests including integration
```bash
just test-full      # or: just pytest-integration
```

This runs all tests including integration tests that make real network calls to ontology services.

### Only integration tests
```bash
uv run pytest -m integration
```

## Integration Test Requirements

Integration tests require:
- Network access to Ubergraph or other ontology SPARQL endpoints
- **OR** Pre-downloaded OBO ontology databases (via `sqlite:obo:` adapter)

### Note on Ontology Access

The integration tests currently use `ubergraph:` adapter which requires specific configuration.
In practice, most users will use `sqlite:obo:` which requires pre-downloaded ontologies.

To download ontologies for local testing:
```bash
runoak -i sqlite:obo:go dump --help  # Downloads GO ontology
```

Integration tests are automatically skipped in the default test run to keep tests fast.

### Current Status

Some integration tests may fail if:
- Ubergraph service is unavailable
- OBO ontology databases are not installed locally
- Network connectivity issues

This is expected - integration tests verify real-world ontology access patterns.

## Caching

Integration tests use `requests-cache` to cache HTTP responses, stored in:
- `output/requests-cache.sqlite`

This significantly speeds up repeated test runs. The cache is git-ignored.

## Test Markers

- `@pytest.mark.integration` - Marks tests that make real network calls
- `@pytest.mark.llm` - Marks tests that use LLM APIs (not currently used)

Both markers are configured in `pytest.ini` and excluded from default runs.

## Writing New Tests

### Unit Tests

Use mocks for external dependencies:

```python
def test_my_feature(validator):
    """Test with no external calls."""
    # Uses the validator fixture which has caching disabled
    result = validator.normalize_string("Test")
    assert result == "test"
```

### Integration Tests

Mark with `@pytest.mark.integration` and document requirements:

```python
@pytest.mark.integration
def test_real_ontology_access():
    """Test with real OLS access.

    This test requires network access to OLS.
    """
    validator = EnumValidator(ValidationConfig(oak_adapter_string="ols:"))
    label = validator.get_ontology_label("GO:0008150")
    assert label is not None
```

## Best Practices

1. **Keep unit tests fast** - No network calls, no file I/O when possible
2. **Make integration tests explicit** - Always mark with `@pytest.mark.integration`
3. **Document requirements** - Note in docstrings what external services are needed
4. **Use fixtures** - Reuse common setup through pytest fixtures
5. **Use parametrize** - Test multiple cases with `@pytest.mark.parametrize`
