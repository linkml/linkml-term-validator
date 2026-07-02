"""Tests for how LTV handles fake (non-existent) and obsolete term IDs.

Two failure modes surface when validating against a remote service like OLS:

- A *fake* ID (e.g. ``GO:9999999``) does not exist. OLS answers the label
  lookup with HTTP 404 rather than an empty result.
- An *obsolete* ID (e.g. ``GO:0000005``) still resolves to a label, so it slips
  past a plain existence check; OLS also answers its ancestors endpoint with a
  payload that omits ``_embedded``.

Both used to abort the whole validation run with an unhandled exception. These
tests pin the intended behaviour: neither case crashes, a fake ID is reported as
an invalid value, and an obsolete ID is reported explicitly as obsolete.

The unit tests run offline against the local ``simpleobo`` test ontology, which
defines the obsolete term ``TEST:0000007``. The integration tests exercise the
real OLS service and are skipped by default.
"""

from pathlib import Path

import pytest
from linkml.validator import Validator  # type: ignore[import-untyped]
from linkml.validator.loaders import YamlLoader  # type: ignore[import-untyped]
from linkml.validator.validation_context import (  # type: ignore[import-untyped]
    ValidationContext,
)
from linkml_runtime import SchemaView

from linkml_term_validator.models import ValidationConfig
from linkml_term_validator.plugins import (
    DynamicEnumPlugin,
    PermissibleValueMeaningPlugin,
)
from linkml_term_validator.utils import OntologyAccess
from linkml_term_validator.validator import EnumValidator

TEST_ONTOLOGY = "simpleobo:tests/data/test_ontology.obo"
OAK_CONFIG = Path("tests/data/test_oak_config.yaml")
DYNAMIC_ENUM_SCHEMA = Path("tests/data/dynamic_enum_schema.yaml")

# Term fixtures from tests/data/test_ontology.obo
OBSOLETE_TERM = "TEST:0000007"  # is_obsolete: true
VALID_TERM = "TEST:0000006"  # cell_cycle, descendant of TEST:0000005
FAKE_TERM = "TEST:0000999"  # does not exist


# ---------------------------------------------------------------------------
# OntologyAccess.is_obsolete
# ---------------------------------------------------------------------------


def _ontology_access() -> OntologyAccess:
    return OntologyAccess(oak_adapter_string=TEST_ONTOLOGY, cache_labels=False)


def test_is_obsolete_true_for_obsolete_term():
    assert _ontology_access().is_obsolete(OBSOLETE_TERM) is True


def test_is_obsolete_false_for_active_term():
    assert _ontology_access().is_obsolete(VALID_TERM) is False


def test_is_obsolete_false_for_fake_term():
    # A term absent from the ontology is simply not in the obsolete set.
    assert _ontology_access().is_obsolete(FAKE_TERM) is False


def test_is_obsolete_none_without_prefix():
    assert _ontology_access().is_obsolete("nocolon") is None


def test_is_obsolete_none_when_offline_without_adapter():
    # Offline never builds an adapter, so obsolescence cannot be determined.
    access = OntologyAccess(
        oak_adapter_string=TEST_ONTOLOGY, cache_labels=False, offline=True
    )
    assert access.is_obsolete(OBSOLETE_TERM) is None


# ---------------------------------------------------------------------------
# DynamicEnumPlugin (data validation)
# ---------------------------------------------------------------------------


def _validate_term(term: str, tmp_path: Path):
    """Validate a single Sample whose ``term`` is the given CURIE."""
    plugin = DynamicEnumPlugin(oak_config_path=OAK_CONFIG, cache_labels=False)
    data_path = tmp_path / "data.yaml"
    data_path.write_text(f"- id: s1\n  term: {term}\n")
    validator = Validator(
        schema=str(DYNAMIC_ENUM_SCHEMA), validation_plugins=[plugin]
    )
    return validator.validate_source(YamlLoader(data_path), target_class="Sample")


def test_dynamic_enum_flags_obsolete_value(tmp_path):
    report = _validate_term(OBSOLETE_TERM, tmp_path)
    assert len(report.results) == 1
    message = report.results[0].message
    assert "obsolete" in message.lower()
    assert OBSOLETE_TERM in message


def test_dynamic_enum_rejects_fake_value_without_crashing(tmp_path):
    report = _validate_term(FAKE_TERM, tmp_path)
    assert len(report.results) == 1
    assert FAKE_TERM in report.results[0].message
    # A fake term is not obsolete: it is reported as a plain non-member.
    assert "not in dynamic enum" in report.results[0].message


# ---------------------------------------------------------------------------
# EnumValidator / PermissibleValueMeaningPlugin (schema validation)
# ---------------------------------------------------------------------------

_OBSOLETE_MEANING_SCHEMA = """
id: https://example.org/obsolete-meaning
name: obsolete-meaning
prefixes:
  TEST: http://example.org/TEST_
  linkml: https://w3id.org/linkml/
default_prefix: obsolete-meaning
classes:
  Sample:
    attributes:
      process:
        range: ProcessEnum
enums:
  ProcessEnum:
    permissible_values:
      OBSOLETE_PV:
        title: obsolete test process
        meaning: TEST:0000007
"""


def test_enum_validator_flags_obsolete_meaning(tmp_path):
    schema_path = tmp_path / "schema.yaml"
    schema_path.write_text(_OBSOLETE_MEANING_SCHEMA)

    config = ValidationConfig(
        oak_adapter_string=TEST_ONTOLOGY, cache_labels=False
    )
    validator = EnumValidator(config)
    result = validator.validate_schema(schema_path)

    obsolete_issues = [i for i in result.issues if "obsolete" in i.message.lower()]
    assert len(obsolete_issues) == 1
    assert obsolete_issues[0].meaning == OBSOLETE_TERM


def test_permissible_value_plugin_flags_obsolete_meaning(tmp_path):
    schema_path = tmp_path / "schema.yaml"
    schema_path.write_text(_OBSOLETE_MEANING_SCHEMA)

    plugin = PermissibleValueMeaningPlugin(
        oak_adapter_string=TEST_ONTOLOGY, cache_labels=False
    )
    # The plugin validates the schema itself, so drive process() directly with a
    # context rather than loading instances of a (non-existent) target class.
    schema_view = SchemaView(str(schema_path))
    context = ValidationContext(schema=schema_view.schema, target_class="Sample")
    plugin.pre_process(context)
    results = list(plugin.process({}, context))

    obsolete = [r for r in results if r.type == "permissible_value_obsolete"]
    assert len(obsolete) == 1
    assert "obsolete" in obsolete[0].message.lower()
    assert OBSOLETE_TERM in obsolete[0].message


# ---------------------------------------------------------------------------
# Integration tests against the real OLS service
# ---------------------------------------------------------------------------

OLS_CONFIG = Path("examples/oak_providers/ols_oak_config.yaml")
OLS_SCHEMA = Path("examples/oak_providers/ols_schema.yaml")

# Real GO terms as of writing.
OLS_VALID = "GO:0009987"  # cellular process, descendant of GO:0008150
OLS_OBSOLETE = "GO:0000005"  # obsolete ribosomal chaperone activity
OLS_FAKE = "GO:9999999"  # does not exist


def _validate_ols_term(term: str, tmp_path: Path):
    plugin = DynamicEnumPlugin(
        oak_config_path=OLS_CONFIG,
        cache_labels=False,
        cache_enum_expansions=False,
    )
    data_path = tmp_path / "data.yaml"
    data_path.write_text(f"- id: s1\n  term: {term}\n")
    validator = Validator(schema=str(OLS_SCHEMA), validation_plugins=[plugin])
    return validator.validate_source(YamlLoader(data_path), target_class="Sample")


@pytest.mark.integration
def test_ols_valid_term_passes(tmp_path):
    report = _validate_ols_term(OLS_VALID, tmp_path)
    assert len(report.results) == 0, report.results


@pytest.mark.integration
def test_ols_fake_term_reported_not_crashed(tmp_path):
    # Regression: a 404 from OLS's label lookup used to abort the whole run.
    report = _validate_ols_term(OLS_FAKE, tmp_path)
    assert len(report.results) == 1
    assert OLS_FAKE in report.results[0].message
    assert "not in dynamic enum" in report.results[0].message


@pytest.mark.integration
def test_ols_obsolete_term_flagged_not_crashed(tmp_path):
    # Regression: OLS's ancestors endpoint omits _embedded for an obsolete
    # term, which used to raise KeyError and abort the whole run.
    report = _validate_ols_term(OLS_OBSOLETE, tmp_path)
    assert len(report.results) == 1
    assert "obsolete" in report.results[0].message.lower()
    assert OLS_OBSOLETE in report.results[0].message


@pytest.mark.integration
def test_ols_obsolete_meaning_flagged_in_schema(tmp_path):
    schema_path = tmp_path / "schema.yaml"
    schema_path.write_text(
        """
id: https://example.org/ols-obsolete-meaning
name: ols-obsolete-meaning
prefixes:
  GO: http://purl.obolibrary.org/obo/GO_
  linkml: https://w3id.org/linkml/
default_prefix: ols-obsolete-meaning
enums:
  ProcessEnum:
    permissible_values:
      OBSOLETE_PV:
        title: obsolete ribosomal chaperone activity
        meaning: GO:0000005
"""
    )
    config = ValidationConfig(
        oak_config_path=OLS_CONFIG, cache_labels=False
    )
    validator = EnumValidator(config)
    result = validator.validate_schema(schema_path)

    obsolete_issues = [i for i in result.issues if "obsolete" in i.message.lower()]
    assert len(obsolete_issues) == 1
    assert obsolete_issues[0].meaning == OLS_OBSOLETE
