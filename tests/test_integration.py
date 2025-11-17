"""Integration tests for EnumValidator with real ontology access.

These tests make real calls to ontology databases and are marked with
@pytest.mark.integration. They are skipped in the default test run
to keep tests fast. Run with: just pytest-integration

Note: These tests require:
- Pre-downloaded OBO ontology databases (GO, CHEBI, etc.)
- OAK with sqlite:obo: adapter working

If these tests fail, you need to set up your OAK environment properly.
This is intentional - integration tests should fail fast if dependencies
aren't configured correctly.
"""


import pytest

from linkml_term_validator.models import ValidationConfig
from linkml_term_validator.validator import EnumValidator


@pytest.mark.integration
def test_validate_with_default_adapter(test_schema_path, cache_dir):
    """Test validation using the default sqlite:obo: adapter.

    This integration test verifies that we can:
    - Use the default sqlite:obo: adapter
    - Dynamically create per-prefix adapters (go, chebi)
    - Retrieve ontology labels from local databases
    - Validate schema against real ontology data

    This test WILL FAIL if OBO databases are not installed.
    That's intentional - set up your environment properly.
    """
    config = ValidationConfig(
        oak_adapter_string="sqlite:obo:",
        cache_labels=True,
        cache_dir=cache_dir,
    )

    validator = EnumValidator(config)
    result = validator.validate_schema(test_schema_path)

    # Should have checked the schema
    assert result.total_enums_checked == 3
    assert result.total_values_checked == 6
    assert result.total_meanings_checked == 4

    # The test schema should validate successfully
    assert isinstance(result.issues, list)


@pytest.mark.integration
@pytest.mark.parametrize(
    "curie,expected_label",
    [
        ("GO:0008150", "biological_process"),
        ("GO:0007049", "cell cycle"),
        ("CHEBI:15377", "water"),
        ("CHEBI:17234", "glucose"),
    ],
)
def test_get_ontology_label_real_terms(curie, expected_label, cache_dir):
    """Test retrieving real labels from local OBO databases.

    This test verifies that we can successfully fetch labels for
    well-known ontology terms using the default sqlite:obo: adapter.

    WILL FAIL if OBO databases (GO, CHEBI) are not installed locally.
    """
    config = ValidationConfig(
        oak_adapter_string="sqlite:obo:",
        cache_labels=True,
        cache_dir=cache_dir,
    )

    validator = EnumValidator(config)
    label = validator.get_ontology_label(curie)

    # Should get exact label back
    assert label is not None, f"Failed to get label for {curie} - is the OBO database installed?"
    # Label should match exactly (or with underscores)
    assert label == expected_label or label == expected_label.replace("_", " "), (
        f"Expected '{expected_label}' for {curie}, got '{label}'"
    )


@pytest.mark.integration
def test_caching_with_real_ontology(cache_dir):
    """Test that caching works correctly with real ontology queries.

    This test verifies:
    - Labels are cached to disk after first query
    - Subsequent queries use the cache
    - Cache files are created with correct structure

    WILL FAIL if GO database is not installed.
    """
    config = ValidationConfig(
        oak_adapter_string="sqlite:obo:",
        cache_labels=True,
        cache_dir=cache_dir,
    )

    validator = EnumValidator(config)

    # First query - should fetch from database and cache
    curie = "GO:0008150"
    label1 = validator.get_ontology_label(curie)
    assert label1 is not None

    # Check that cache file was created
    cache_file = cache_dir / "go" / "terms.csv"
    assert cache_file.exists(), "Cache file should be created"

    # Read cache file and verify it contains our term
    with open(cache_file) as f:
        cache_content = f.read()
        assert curie in cache_content
        assert label1 in cache_content

    # Second query - should use cache (same instance)
    label2 = validator.get_ontology_label(curie)
    assert label2 == label1

    # Create new validator instance - should load from cache
    validator2 = EnumValidator(config)
    label3 = validator2.get_ontology_label(curie)
    assert label3 == label1


@pytest.mark.integration
def test_validate_with_mismatched_label(cache_dir, tmp_path):
    """Test that validation correctly identifies label mismatches.

    Creates a schema with an intentionally wrong label and verifies
    that the validator catches it.

    WILL FAIL if GO database is not installed.
    """
    # Create a test schema with wrong label
    schema_content = """
id: https://example.org/test
name: test-bad-labels
prefixes:
  GO: http://purl.obolibrary.org/obo/GO_

enums:
  TestEnum:
    permissible_values:
      WRONG_LABEL:
        title: this is definitely wrong
        meaning: GO:0008150
"""
    schema_path = tmp_path / "bad_schema.yaml"
    schema_path.write_text(schema_content)

    config = ValidationConfig(
        oak_adapter_string="sqlite:obo:",
        cache_labels=True,
        cache_dir=cache_dir,
        strict_mode=True,
    )

    validator = EnumValidator(config)
    result = validator.validate_schema(schema_path)

    # Should have found an issue
    assert len(result.issues) > 0, "Should detect label mismatch"

    # The issue should be about label mismatch
    issue = result.issues[0]
    assert issue.meaning == "GO:0008150"
    assert "mismatch" in issue.message.lower() or "match" in issue.message.lower(), (
        f"Expected mismatch error, got: {issue.message}"
    )


@pytest.mark.integration
def test_validate_unknown_prefix_tracking(cache_dir, tmp_path):
    """Test tracking of unknown ontology prefixes.

    This test verifies that the validator properly tracks prefixes
    that aren't configured in oak_config.yaml.

    WILL FAIL if GO database is not installed.
    """
    # Create oak_config that explicitly configures GO but not NOTINCONFIG
    oak_config = tmp_path / "oak_config.yaml"
    oak_config.write_text("""ontology_adapters:
  GO: sqlite:obo:go
""")

    # Create a test schema with GO and a prefix not in config
    schema_content = """
id: https://example.org/test
name: test-unknown-prefixes
prefixes:
  GO: http://purl.obolibrary.org/obo/GO_
  NOTINCONFIG: http://example.org/notinconfig/

enums:
  TestEnum:
    permissible_values:
      VAL1:
        meaning: GO:0008150
      VAL2:
        meaning: NOTINCONFIG:12345
"""
    schema_path = tmp_path / "unknown_prefix_schema.yaml"
    schema_path.write_text(schema_content)

    config = ValidationConfig(
        oak_config_path=oak_config,
        cache_labels=True,
        cache_dir=cache_dir,
    )

    validator = EnumValidator(config)
    validator.validate_schema(schema_path)

    # Should track the unknown prefix (not in oak_config)
    unknown_prefixes = validator.get_unknown_prefixes()
    assert "NOTINCONFIG" in unknown_prefixes


@pytest.mark.integration
def test_validate_with_oak_config(cache_dir, tmp_path):
    """Test validation with a custom oak_config.yaml.

    This test creates a custom config file and verifies that
    the validator uses it correctly.

    WILL FAIL if GO database is not installed.
    """
    # Create oak_config.yaml
    oak_config = tmp_path / "oak_config.yaml"
    oak_config.write_text("""ontology_adapters:
  GO: sqlite:obo:go
  CHEBI: sqlite:obo:chebi
  SKIPPED: ""
""")

    # Create test schema
    schema_content = """
id: https://example.org/test
name: test-with-config
prefixes:
  GO: http://purl.obolibrary.org/obo/GO_
  SKIPPED: http://example.org/skipped/

enums:
  TestEnum:
    permissible_values:
      VAL1:
        meaning: GO:0008150
      VAL2:
        meaning: SKIPPED:12345
"""
    schema_path = tmp_path / "schema.yaml"
    schema_path.write_text(schema_content)

    config = ValidationConfig(
        oak_config_path=oak_config,
        cache_labels=True,
        cache_dir=cache_dir,
    )

    validator = EnumValidator(config)
    validator.validate_schema(schema_path)

    # Should validate GO term
    # SKIPPED prefix should be tracked as unknown since adapter returns None
    # (even though it's configured, empty config means "skip validation")
    unknown_prefixes = validator.get_unknown_prefixes()
    # GO should NOT be in unknown since it's properly configured
    assert "GO" not in unknown_prefixes
    # SKIPPED will be in unknown because get_adapter returns None for it
    assert "SKIPPED" in unknown_prefixes


@pytest.mark.integration
def test_validate_multiple_schemas(cache_dir, tmp_path):
    """Test validating multiple schemas in sequence.

    Verifies that the validator can process multiple schemas
    and that caching works across schema validations.

    WILL FAIL if GO database is not installed.
    """
    # Create two schemas
    schema1 = tmp_path / "schema1.yaml"
    schema1.write_text("""
id: https://example.org/test1
name: test1
prefixes:
  GO: http://purl.obolibrary.org/obo/GO_

enums:
  Enum1:
    permissible_values:
      VAL1:
        title: biological process
        meaning: GO:0008150
""")

    schema2 = tmp_path / "schema2.yaml"
    schema2.write_text("""
id: https://example.org/test2
name: test2
prefixes:
  GO: http://purl.obolibrary.org/obo/GO_

enums:
  Enum2:
    permissible_values:
      VAL2:
        title: cell cycle
        meaning: GO:0007049
""")

    config = ValidationConfig(
        oak_adapter_string="sqlite:obo:",
        cache_labels=True,
        cache_dir=cache_dir,
    )

    validator = EnumValidator(config)

    # Validate both schemas
    result1 = validator.validate_schema(schema1)
    result2 = validator.validate_schema(schema2)

    # Both should validate
    assert result1.total_enums_checked == 1
    assert result2.total_enums_checked == 1

    # Cache should have both terms
    cache_file = cache_dir / "go" / "terms.csv"
    assert cache_file.exists()

    with open(cache_file) as f:
        cache_content = f.read()
        assert "GO:0008150" in cache_content
        assert "GO:0007049" in cache_content


@pytest.mark.integration
def test_cli_integration(test_schema_path, cache_dir, tmp_path):
    """Test the CLI with real ontology validation.

    This test verifies that the CLI can successfully validate
    a schema using real ontology databases.

    WILL FAIL if GO/CHEBI databases are not installed.
    """
    from linkml_term_validator.cli import app
    from typer.testing import CliRunner

    runner = CliRunner()

    # Run the validate-schema command with default adapter (sqlite:obo:)
    result = runner.invoke(
        app,
        [
            "validate-schema",
            str(test_schema_path),
            "--cache-dir",
            str(cache_dir),
            "--verbose",
        ],
    )

    # Should succeed (exit code 0 or 1 depending on validation results)
    # We just want to make sure it doesn't crash
    assert result.exit_code in [0, 1]

    # Output should contain some validation info
    assert "checked" in result.stdout.lower() or "✅" in result.stdout
