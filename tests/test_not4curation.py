"""Tests for the Not4Curation check (issue #70).

Some ontologies keep terms for hierarchy completeness that they explicitly do
not want used for annotation, and say so with a *synonym* rather than an
obsoletion axiom. RGD's XCO writes ``Not4Curation``; other OBO ontologies use
``not_recommended_for_annotation``. Such a term exists, has a matching label
and is reachable, so it passed every check this validator performed.

The local test ontology carries two such terms under ``TEST:0000005``
(biological_process), so the whole unit suite runs offline:

- ``TEST:0000008`` flagged process, synonym ``Not4Curation``
- ``TEST:0000009`` discouraged process, synonym ``not_recommended_for_annotation``

Design points pinned here, from the issue:

- fail by default, demotable through ``severity_overrides``;
- generic substring match after folding to lowercase alphanumerics;
- a term whose synonyms could not be read is reported as *unchecked*, never
  folded into a pass;
- the check runs on the accepted value, so a cached enum hit cannot hide it.
"""

from pathlib import Path

import pytest
from linkml.validator import Validator  # type: ignore[import-untyped]
from linkml.validator.loaders import YamlLoader  # type: ignore[import-untyped]
from linkml.validator.report import Severity  # type: ignore[import-untyped]
from linkml.validator.validation_context import (  # type: ignore[import-untyped]
    ValidationContext,
)
from linkml_runtime import SchemaView
from typer.testing import CliRunner

from linkml_term_validator.cli import app
from linkml_term_validator.models import ErrorMode, ValidationConfig
from linkml_term_validator.plugins import (
    BindingValidationPlugin,
    DynamicEnumPlugin,
    PermissibleValueMeaningPlugin,
)
from linkml_term_validator.utils import (
    DEFAULT_NOT4CURATION_MARKERS,
    OntologyAccess,
    normalize_marker_text,
    normalize_not4curation_markers,
    not4curation_message,
)
from linkml_term_validator.validator import EnumValidator

TEST_ONTOLOGY = "simpleobo:tests/data/test_ontology.obo"
OAK_CONFIG = Path("tests/data/test_oak_config.yaml")
DYNAMIC_ENUM_SCHEMA = Path("tests/data/dynamic_enum_schema.yaml")
BINDING_SCHEMA = Path("tests/data/binding_label_schema.yaml")

CLEAN_TERM = "TEST:0000006"  # cell_cycle
RGD_FLAGGED = "TEST:0000008"  # synonym: Not4Curation
OBO_FLAGGED = "TEST:0000009"  # synonym: not_recommended_for_annotation
FAKE_TERM = "TEST:0000999"


# ---------------------------------------------------------------------------
# Marker normalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "alias",
    ["Not4Curation", "not4curation", "NOT 4 CURATION", "not_4_curation", "not-4-curation"],
)
def test_rgd_marker_matches_case_and_separator_insensitively(alias):
    assert any(m in normalize_marker_text(alias) for m in DEFAULT_NOT4CURATION_MARKERS)


@pytest.mark.parametrize(
    "alias",
    ["not_recommended_for_annotation", "Not Recommended For Annotation", "NotForCuration"],
)
def test_obo_style_markers_match(alias):
    assert any(m in normalize_marker_text(alias) for m in DEFAULT_NOT4CURATION_MARKERS)


@pytest.mark.parametrize("alias", ["cell cycle", "curation", "annotation", "not a marker"])
def test_ordinary_synonyms_do_not_match(alias):
    assert not any(m in normalize_marker_text(alias) for m in DEFAULT_NOT4CURATION_MARKERS)


def test_custom_markers_replace_the_defaults():
    assert normalize_not4curation_markers(["Do_Not_Annotate"]) == ("donotannotate",)


def test_empty_marker_list_is_rejected():
    # Matching every alias would flag everything; the off switch is elsewhere.
    with pytest.raises(ValueError, match="check_not4curation=False"):
        normalize_not4curation_markers([""])


# ---------------------------------------------------------------------------
# OntologyAccess: alias retrieval, marker detection, unchecked bookkeeping
# ---------------------------------------------------------------------------


def _access(**kwargs) -> OntologyAccess:
    return OntologyAccess(oak_adapter_string=TEST_ONTOLOGY, cache_labels=False, **kwargs)


def test_entity_aliases_include_label_and_synonyms():
    aliases = _access().entity_aliases(RGD_FLAGGED)
    assert aliases is not None
    assert set(aliases) == {"flagged process", "Not4Curation"}


def test_find_markers_rgd_convention():
    assert _access().find_not4curation_markers(RGD_FLAGGED) == ["Not4Curation"]


def test_find_markers_obo_convention():
    assert _access().find_not4curation_markers(OBO_FLAGGED) == ["not_recommended_for_annotation"]


def test_find_markers_clean_term_returns_empty_and_counts_as_checked():
    access = _access()
    assert access.find_not4curation_markers(CLEAN_TERM) == []
    assert CLEAN_TERM in access.get_not4curation_checked()
    assert CLEAN_TERM not in access.get_not4curation_unchecked()


def test_find_markers_fake_term_is_unchecked_not_clean():
    # No aliases at all (not even a label) means nothing was vetted.
    access = _access()
    assert access.find_not4curation_markers(FAKE_TERM) is None
    assert FAKE_TERM in access.get_not4curation_unchecked()


def test_find_markers_offline_is_unchecked():
    access = _access(offline=True)
    assert access.find_not4curation_markers(RGD_FLAGGED) is None
    assert access.get_not4curation_unchecked() == {RGD_FLAGGED}


def test_find_markers_custom_marker_list():
    access = _access(not4curation_markers=["discouraged"])
    # "discouraged process" is the label of OBO_FLAGGED; the custom marker hits
    # it and the default markers no longer apply.
    assert access.find_not4curation_markers(OBO_FLAGGED) == ["discouraged process"]
    assert access.find_not4curation_markers(RGD_FLAGGED) == []


class _AliasBoomAdapter:
    """Adapter whose label resolves but whose alias lookup raises."""

    def label(self, curie):
        return "some term"

    def entity_aliases(self, curie):
        raise RuntimeError("no synonyms for you")


def test_alias_lookup_failure_is_unchecked_not_crash():
    access = OntologyAccess(cache_labels=False)
    access._adapter_cache["TEST"] = _AliasBoomAdapter()
    assert access.find_not4curation_markers("TEST:0001") is None
    assert "TEST:0001" in access.get_not4curation_unchecked()


class _CountingOlsAdapter:
    """Minimal OLS-shaped adapter whose term payload carries synonyms."""

    def __init__(self, payload):
        self.focus_ontology = "xco"
        self.client = self
        self.payload = payload
        self.get_term_calls = 0

    def curie_to_uri(self, curie):
        return f"http://purl.obolibrary.org/obo/{curie.replace(':', '_')}"

    def get_term(self, ontology, iri):
        self.get_term_calls += 1
        return self.payload


def test_ols_markers_read_from_cached_term_payload():
    """OLS adds no round trip: label, obsolescence and synonyms share one fetch.

    The same synonym listed under both ``synonyms`` and ``obo_synonym`` is
    named once in the result.
    """
    access = OntologyAccess(cache_labels=False)
    adapter = _CountingOlsAdapter(
        {
            "label": "estrogen/estrogen analog",
            "is_obsolete": False,
            "synonyms": ["Not4Curation"],
            "obo_synonym": [{"name": "Not4Curation", "scope": "hasRelatedSynonym"}],
        }
    )
    access._adapter_cache["XCO"] = adapter
    assert access.is_obsolete("XCO:0000294") is False
    assert access.find_not4curation_markers("XCO:0000294") == ["Not4Curation"]
    assert adapter.get_term_calls == 1


def test_ols_real_payload_shape_is_flagged():
    """The shape OLS4 actually returns for XCO:0000294 (synonyms list, obo_synonym null)."""
    access = OntologyAccess(cache_labels=False)
    access._adapter_cache["XCO"] = _CountingOlsAdapter(
        {
            "label": "estrogen/estrogen analog",
            "synonyms": ["Not4Curation"],
            "obo_synonym": None,
            "is_obsolete": False,
        }
    )
    assert access.find_not4curation_markers("XCO:0000294") == ["Not4Curation"]


@pytest.mark.parametrize(
    "payload",
    [
        {"label": "experimental condition", "synonyms": []},
        {"label": "experimental condition", "synonyms": None},
        {"label": "experimental condition", "synonyms": None, "obo_synonym": None},
    ],
)
def test_ols_payload_with_empty_synonyms_is_clean(payload):
    # The synonym field is present and empty: the term really has none.
    access = OntologyAccess(cache_labels=False)
    access._adapter_cache["XCO"] = _CountingOlsAdapter(payload)
    assert access.find_not4curation_markers("XCO:0000000") == []
    assert "XCO:0000000" in access.get_not4curation_checked()


def test_ols_payload_without_synonym_fields_is_unchecked():
    """A payload shape with no synonym key at all is not understood.

    The label alone would make the alias list non-empty and the term look
    vetted, so a client or OLS version that drops the synonym fields would
    silently pass every term. Require positive evidence instead.
    """
    access = OntologyAccess(cache_labels=False)
    access._adapter_cache["XCO"] = _CountingOlsAdapter({"label": "estrogen/estrogen analog"})
    assert access.find_not4curation_markers("XCO:0000294") is None
    assert "XCO:0000294" in access.get_not4curation_unchecked()


def test_unchecked_accessor_returns_a_copy():
    access = _access(offline=True)
    access.find_not4curation_markers(RGD_FLAGGED)
    snapshot = access.get_not4curation_unchecked()
    snapshot.add("TEST:9999999")
    assert access.get_not4curation_unchecked() == {RGD_FLAGGED}


def test_label_counts_as_an_alias():
    """Aliases include rdfs:label, so a marker in the label itself hits.

    That is intended: an ontology writing the marker into the label is still
    saying "do not annotate". It also means a custom marker must be chosen
    with real labels in mind.
    """
    access = _access(not4curation_markers=["flagged"])
    assert access.find_not4curation_markers(RGD_FLAGGED) == ["flagged process"]
    assert access.find_not4curation_markers(CLEAN_TERM) == []


def test_message_quotes_the_ontology_wording():
    assert (
        not4curation_message("XCO:0000294", ["Not4Curation"])
        == "Ontology term XCO:0000294 is marked 'Not4Curation' by its ontology "
        "(not recommended for annotation)"
    )


# ---------------------------------------------------------------------------
# DynamicEnumPlugin (direct slot ranges)
# ---------------------------------------------------------------------------


def _validate_process(term: str, tmp_path: Path, **plugin_kwargs):
    plugin = DynamicEnumPlugin(oak_config_path=OAK_CONFIG, cache_labels=False, **plugin_kwargs)
    data_path = tmp_path / "data.yaml"
    data_path.write_text(f"- id: s1\n  process_type: {term}\n")
    validator = Validator(schema=str(DYNAMIC_ENUM_SCHEMA), validation_plugins=[plugin])
    report = validator.validate_source(YamlLoader(data_path), target_class="Sample")
    return plugin, report.results


@pytest.mark.parametrize("strategy", ["progressive", "greedy"])
@pytest.mark.parametrize("term", [RGD_FLAGGED, OBO_FLAGGED])
def test_dynamic_enum_flags_member_marked_not4curation(tmp_path, strategy, term):
    """The term IS in the closure; the flag is the only thing wrong with it."""
    _, results = _validate_process(term, tmp_path, cache_strategy=strategy, cache_enum_expansions=False)
    assert len(results) == 1
    result = results[0]
    assert result.type == "dynamic_enum_not4curation"
    assert result.severity is Severity.ERROR
    assert term in result.message
    assert "not recommended for annotation" in result.message
    assert "ProcessTypeEnum" in result.message


@pytest.mark.parametrize("strategy", ["progressive", "greedy"])
def test_dynamic_enum_clean_member_passes(tmp_path, strategy):
    _, results = _validate_process(CLEAN_TERM, tmp_path, cache_strategy=strategy, cache_enum_expansions=False)
    assert results == []


def test_dynamic_enum_non_member_not_double_reported(tmp_path):
    """A value rejected as out-of-enum gets one result, not a second Not4Curation one."""
    _, results = _validate_process("TEST:0000003", tmp_path, cache_enum_expansions=False)
    assert [r.type for r in results] == ["dynamic_enum_validation"]


def test_dynamic_enum_check_can_be_switched_off(tmp_path):
    plugin, results = _validate_process(
        RGD_FLAGGED, tmp_path, check_not4curation=False, cache_enum_expansions=False
    )
    assert results == []
    # Off means off: nothing was looked up, so nothing is "unchecked" either.
    assert plugin.get_not4curation_unchecked() == set()


def test_dynamic_enum_severity_is_overridable(tmp_path):
    _, results = _validate_process(
        RGD_FLAGGED,
        tmp_path,
        severity_overrides={"dynamic_enum_not4curation": "WARN"},
        cache_enum_expansions=False,
    )
    assert len(results) == 1
    assert results[0].severity is Severity.WARN


def test_cached_enum_hit_does_not_hide_the_flag(tmp_path):
    """The cache is the offline positive-hit set; the flag must survive it.

    Once a flagged CURIE has validated it is written to the enum cache and from
    then on membership comes from the cache with no ontology call. The check
    runs on the accepted value, so the second run still reports it.
    """
    cache_dir = tmp_path / "cache"
    first_plugin, first = _validate_process(RGD_FLAGGED, tmp_path, cache_dir=cache_dir)
    assert [r.type for r in first] == ["dynamic_enum_not4curation"]
    enum_def = first_plugin.schema_view.get_enum("ProcessTypeEnum")
    assert RGD_FLAGGED in (first_plugin._load_enum_cache(enum_def) or set())

    _, second = _validate_process(RGD_FLAGGED, tmp_path, cache_dir=cache_dir)
    assert [r.type for r in second] == ["dynamic_enum_not4curation"]


def test_offline_complete_cache_accepts_but_reports_unchecked(tmp_path):
    """Offline there are no synonyms to read, so the term is accepted AND
    listed as unchecked. A degraded run must not look like a clean one."""
    cache_dir = tmp_path / "cache"
    online = DynamicEnumPlugin(oak_config_path=OAK_CONFIG, cache_dir=cache_dir, cache_strategy="greedy")
    schema_view = SchemaView(str(DYNAMIC_ENUM_SCHEMA))
    online.expand_enum(schema_view.get_enum("ProcessTypeEnum"), schema_view)

    plugin, results = _validate_process(RGD_FLAGGED, tmp_path, cache_dir=cache_dir, offline=True)
    assert results == []
    assert plugin.get_not4curation_unchecked() == {RGD_FLAGGED}


# ---------------------------------------------------------------------------
# BindingValidationPlugin (the dismech case: exposure_term bindings)
# ---------------------------------------------------------------------------


def _validate_binding(term: str, tmp_path: Path, label: str = "", **plugin_kwargs):
    plugin = BindingValidationPlugin(
        oak_adapter_string=TEST_ONTOLOGY,
        cache_labels=False,
        cache_enum_expansions=False,
        validate_labels=bool(label),
        **plugin_kwargs,
    )
    data_path = tmp_path / "data.yaml"
    label_line = f"  label: {label}\n" if label else ""
    data_path.write_text(f"annotation_id: ann:1\nprocess:\n  id: {term}\n{label_line}")
    validator = Validator(schema=str(BINDING_SCHEMA), validation_plugins=[plugin])
    report = validator.validate_source(YamlLoader(data_path), target_class="Annotation")
    return plugin, report.results


@pytest.mark.parametrize("strategy", ["progressive", "greedy"])
def test_binding_flags_term_marked_not4curation(tmp_path, strategy):
    _, results = _validate_binding(RGD_FLAGGED, tmp_path, cache_strategy=strategy)
    assert len(results) == 1
    result = results[0]
    assert result.type == "binding_not4curation"
    assert result.severity is Severity.ERROR
    assert RGD_FLAGGED in result.message
    assert "markers: Not4Curation" in result.context


def test_binding_with_matching_label_still_flags(tmp_path):
    """Exists, label matches, reachable: the exact term that slipped through."""
    _, results = _validate_binding(RGD_FLAGGED, tmp_path, label="flagged process")
    assert [r.type for r in results] == ["binding_not4curation"]


def test_binding_clean_term_passes(tmp_path):
    _, results = _validate_binding(CLEAN_TERM, tmp_path, label="cell_cycle")
    assert results == []


def test_binding_non_member_not_double_reported(tmp_path):
    _, results = _validate_binding("TEST:0000003", tmp_path)
    assert [r.type for r in results] == ["binding_validation"]


def test_binding_severity_is_overridable_via_oak_config(tmp_path):
    config = tmp_path / "oak.yaml"
    config.write_text(
        "ontology_adapters:\n"
        f"  TEST: {TEST_ONTOLOGY}\n"
        "severity_overrides:\n"
        "  binding_not4curation: WARN\n"
    )
    _, results = _validate_binding(RGD_FLAGGED, tmp_path, oak_config_path=config)
    assert len(results) == 1
    assert results[0].severity is Severity.WARN


def test_binding_check_switched_off_via_oak_config(tmp_path):
    config = tmp_path / "oak.yaml"
    config.write_text(f"ontology_adapters:\n  TEST: {TEST_ONTOLOGY}\ncheck_not4curation: false\n")
    plugin, results = _validate_binding(RGD_FLAGGED, tmp_path, oak_config_path=config)
    assert results == []
    assert plugin.config.check_not4curation is False


def test_explicit_constructor_value_beats_oak_config(tmp_path):
    """The config file fills only what was left unset; an explicit argument wins."""
    config = tmp_path / "oak.yaml"
    config.write_text(
        "ontology_adapters:\n"
        f"  TEST: {TEST_ONTOLOGY}\n"
        "check_not4curation: false\n"
        "not4curation_markers:\n"
        "  - discouraged\n"
    )
    plugin, results = _validate_binding(
        RGD_FLAGGED, tmp_path, oak_config_path=config, check_not4curation=True
    )
    assert plugin.config.check_not4curation is True
    # The file's marker list still applies: it was not set explicitly, and it
    # does not match the RGD term.
    assert results == []
    assert plugin.ontology.not4curation_markers == ("discouraged",)

    plugin, results = _validate_binding(
        RGD_FLAGGED,
        tmp_path,
        oak_config_path=config,
        check_not4curation=True,
        not4curation_markers=["not4curation"],
    )
    assert [r.type for r in results] == ["binding_not4curation"]


def test_unset_constructor_value_defaults_on(tmp_path):
    plugin = BindingValidationPlugin(oak_adapter_string=TEST_ONTOLOGY, cache_labels=False)
    assert plugin.config.check_not4curation is True


def test_invalid_oak_config_values_raise(tmp_path):
    config = tmp_path / "oak.yaml"
    config.write_text(f"ontology_adapters:\n  TEST: {TEST_ONTOLOGY}\ncheck_not4curation: maybe\n")
    with pytest.raises(ValueError, match="check_not4curation"):
        BindingValidationPlugin(oak_config_path=config, cache_labels=False)
    config.write_text(f"ontology_adapters:\n  TEST: {TEST_ONTOLOGY}\nnot4curation_markers: 3\n")
    with pytest.raises(ValueError, match="not4curation_markers"):
        EnumValidator(ValidationConfig(oak_config_path=config, cache_labels=False))


def test_binding_custom_markers_via_oak_config(tmp_path):
    config = tmp_path / "oak.yaml"
    config.write_text(
        "ontology_adapters:\n"
        f"  TEST: {TEST_ONTOLOGY}\n"
        "not4curation_markers:\n"
        "  - discouraged\n"
    )
    # The custom list replaces the defaults: the RGD marker no longer hits.
    _, results = _validate_binding(RGD_FLAGGED, tmp_path, oak_config_path=config)
    assert results == []
    _, results = _validate_binding(OBO_FLAGGED, tmp_path, oak_config_path=config)
    assert [r.type for r in results] == ["binding_not4curation"]


def test_binding_static_enum_unconfigured_prefix_is_out_of_scope(tmp_path):
    """A static-enum binding under a prefix nobody configured must not make the
    check build an adapter (which could download an ontology), exactly as the
    existence check leaves such terms alone."""
    schema = tmp_path / "schema.yaml"
    schema.write_text(
        """
id: https://example.org/static
name: static
prefixes:
  linkml: https://w3id.org/linkml/
  FOO: http://example.org/FOO_
default_prefix: static
default_range: string
classes:
  Annotation:
    tree_root: true
    attributes:
      annotation_id:
        identifier: true
      thing:
        range: Term
        inlined: true
        bindings:
          - binds_value_of: id
            range: ThingEnum
  Term:
    attributes:
      id:
enums:
  ThingEnum:
    permissible_values:
      A:
        meaning: FOO:0001
"""
    )
    plugin = BindingValidationPlugin(validate_labels=False, cache_labels=False, cache_dir=tmp_path)
    schema_view = SchemaView(str(schema))
    context = ValidationContext(schema=schema_view.schema, target_class="Annotation")
    plugin.pre_process(context)
    results = list(plugin.process({"annotation_id": "x", "thing": {"id": "FOO:0001"}}, context))
    assert results == []
    assert plugin.ontology._adapter_cache == {}
    assert plugin.get_not4curation_unchecked() == set()


# ---------------------------------------------------------------------------
# Schema side: PermissibleValueMeaningPlugin and EnumValidator
# ---------------------------------------------------------------------------

_FLAGGED_MEANING_SCHEMA = f"""
id: https://example.org/flagged-meaning
name: flagged-meaning
prefixes:
  TEST: http://example.org/TEST_
  linkml: https://w3id.org/linkml/
default_prefix: flagged-meaning
classes:
  Sample:
    attributes:
      process:
        range: ProcessEnum
enums:
  ProcessEnum:
    permissible_values:
      FLAGGED:
        title: flagged process
        meaning: {RGD_FLAGGED}
      CLEAN:
        title: cell_cycle
        meaning: {CLEAN_TERM}
"""


def test_permissible_value_plugin_flags_meaning(tmp_path):
    schema_path = tmp_path / "schema.yaml"
    schema_path.write_text(_FLAGGED_MEANING_SCHEMA)
    plugin = PermissibleValueMeaningPlugin(oak_adapter_string=TEST_ONTOLOGY, cache_labels=False)
    schema_view = SchemaView(str(schema_path))
    context = ValidationContext(schema=schema_view.schema, target_class="Sample")
    plugin.pre_process(context)
    results = list(plugin.process({}, context))

    assert [r.type for r in results] == ["permissible_value_not4curation"]
    assert results[0].severity is Severity.ERROR
    assert results[0].instance["value"] == "FLAGGED"
    assert results[0].instance["markers"] == ["Not4Curation"]


def test_permissible_value_plugin_respects_override_and_switch(tmp_path):
    schema_path = tmp_path / "schema.yaml"
    schema_path.write_text(_FLAGGED_MEANING_SCHEMA)
    schema_view = SchemaView(str(schema_path))
    context = ValidationContext(schema=schema_view.schema, target_class="Sample")

    demoted = PermissibleValueMeaningPlugin(
        oak_adapter_string=TEST_ONTOLOGY,
        cache_labels=False,
        severity_overrides={ErrorMode.PERMISSIBLE_VALUE_NOT4CURATION: Severity.INFO},
    )
    demoted.pre_process(context)
    assert [r.severity for r in demoted.process({}, context)] == [Severity.INFO]

    off = PermissibleValueMeaningPlugin(
        oak_adapter_string=TEST_ONTOLOGY, cache_labels=False, check_not4curation=False
    )
    off.pre_process(context)
    assert list(off.process({}, context)) == []


def test_enum_validator_flags_meaning_and_reports_unchecked(tmp_path):
    schema_path = tmp_path / "schema.yaml"
    schema_path.write_text(_FLAGGED_MEANING_SCHEMA)
    validator = EnumValidator(ValidationConfig(oak_adapter_string=TEST_ONTOLOGY, cache_labels=False))
    result = validator.validate_schema(schema_path)

    flagged = [i for i in result.issues if "not recommended for annotation" in i.message]
    assert len(flagged) == 1
    assert flagged[0].meaning == RGD_FLAGGED
    assert flagged[0].is_error()
    assert result.not4curation_unchecked == []


def test_enum_validator_switch_off(tmp_path):
    schema_path = tmp_path / "schema.yaml"
    schema_path.write_text(_FLAGGED_MEANING_SCHEMA)
    validator = EnumValidator(
        ValidationConfig(oak_adapter_string=TEST_ONTOLOGY, cache_labels=False, check_not4curation=False)
    )
    assert validator.validate_schema(schema_path).issues == []


def test_enum_validator_reads_switch_from_oak_config(tmp_path):
    schema_path = tmp_path / "schema.yaml"
    schema_path.write_text(_FLAGGED_MEANING_SCHEMA)
    config = tmp_path / "oak.yaml"
    config.write_text(f"ontology_adapters:\n  TEST: {TEST_ONTOLOGY}\ncheck_not4curation: false\n")
    validator = EnumValidator(ValidationConfig(oak_config_path=config, cache_labels=False))
    assert validator.config.check_not4curation is False
    assert validator.validate_schema(schema_path).issues == []

    # An explicit config value beats the file.
    validator = EnumValidator(
        ValidationConfig(oak_config_path=config, cache_labels=False, check_not4curation=True)
    )
    assert validator.config.check_not4curation is True
    assert len(validator.validate_schema(schema_path).issues) == 1


def test_curie_label_pairs_flag_marked_term():
    validator = EnumValidator(ValidationConfig(oak_adapter_string=TEST_ONTOLOGY, cache_labels=False))
    issues = validator.validate_curie_label_pairs(
        [(RGD_FLAGGED, "flagged process", "line:1"), (CLEAN_TERM, "cell_cycle", "line:2")]
    )
    assert len(issues) == 1
    assert issues[0].value_name == RGD_FLAGGED
    assert "not recommended for annotation" in issues[0].message


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@pytest.fixture
def runner():
    return CliRunner()


@pytest.mark.parametrize("command", ["validate-data", "validate-schema", "validate", "validate-text-file"])
def test_cli_help_shows_switch(runner, command):
    result = runner.invoke(app, [command, "--help"])
    assert result.exit_code == 0
    assert "--check-not4curation" in result.output


def test_cli_validate_data_fails_on_flagged_term(runner, tmp_path):
    data = tmp_path / "data.yaml"
    data.write_text(f"- id: s1\n  process_type: {RGD_FLAGGED}\n")
    args = [
        "validate-data", str(data), "-s", str(DYNAMIC_ENUM_SCHEMA), "-t", "Sample",
        "-c", str(OAK_CONFIG), "--no-cache",
    ]
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert "Not4Curation" in result.output
    assert "ERROR" in result.output

    result = runner.invoke(app, [*args, "--no-check-not4curation"])
    assert result.exit_code == 0, result.output


def test_cli_explicit_flag_beats_oak_config(runner, tmp_path):
    """--no-check-not4curation must not be silently ignored by a config file."""
    config = tmp_path / "oak.yaml"
    config.write_text(f"ontology_adapters:\n  TEST: {TEST_ONTOLOGY}\ncheck_not4curation: true\n")
    data = tmp_path / "data.yaml"
    data.write_text(f"- id: s1\n  process_type: {RGD_FLAGGED}\n")
    args = ["validate-data", str(data), "-s", str(DYNAMIC_ENUM_SCHEMA), "-t", "Sample",
            "-c", str(config), "--no-cache"]
    assert runner.invoke(app, args).exit_code == 1
    assert runner.invoke(app, [*args, "--no-check-not4curation"]).exit_code == 0

    # And the other way: the file turns it off, the flag turns it back on.
    config.write_text(f"ontology_adapters:\n  TEST: {TEST_ONTOLOGY}\ncheck_not4curation: false\n")
    assert runner.invoke(app, args).exit_code == 0
    assert runner.invoke(app, [*args, "--check-not4curation"]).exit_code == 1


def test_cli_validate_data_offline_reports_unchecked(runner, tmp_path):
    cache_dir = tmp_path / "cache"
    data = tmp_path / "data.yaml"
    data.write_text(f"- id: s1\n  process_type: {RGD_FLAGGED}\n")
    common = [str(data), "-s", str(DYNAMIC_ENUM_SCHEMA), "-t", "Sample", "-c", str(OAK_CONFIG),
              "--cache-dir", str(cache_dir), "--no-bindings"]

    # Materialize the closure online (this run also reports the flag).
    online = runner.invoke(app, ["validate-data", *common, "--cache-strategy", "greedy"])
    assert online.exit_code == 1
    assert "Not4Curation check skipped" not in online.output

    offline = runner.invoke(app, ["validate-data", *common, "--offline"])
    assert offline.exit_code == 0, offline.output
    assert "Not4Curation check skipped for 1 term(s)" in offline.output
    assert RGD_FLAGGED in offline.output
    assert "Run once online" in offline.output


def test_cli_validate_schema_fails_on_flagged_meaning(runner, tmp_path):
    schema_path = tmp_path / "schema.yaml"
    schema_path.write_text(_FLAGGED_MEANING_SCHEMA)
    args = ["validate-schema", str(schema_path), "-c", str(OAK_CONFIG), "--no-cache"]
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert "Not4Curation" in result.output

    result = runner.invoke(app, [*args, "--no-check-not4curation"])
    assert result.exit_code == 0, result.output


def test_cli_validate_text_file_fails_on_flagged_term(runner, tmp_path):
    doc = tmp_path / "doc.md"
    doc.write_text(f'@term {RGD_FLAGGED} "flagged process"\n')
    args = ["validate-text-file", str(doc), "-c", str(OAK_CONFIG), "--no-cache"]
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert "Not4Curation" in result.output

    result = runner.invoke(app, [*args, "--no-check-not4curation"])
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# Integration: the real XCO terms from the issue, through a local SQLite
# adapter and through OLS. Skipped by default (network / ontology download).
# ---------------------------------------------------------------------------

XCO_FLAGGED = ["XCO:0000294", "XCO:0000950", "XCO:0000561"]
XCO_ROOT = "XCO:0000000"  # experimental condition, not flagged


def _xco_binding_case(tmp_path: Path, adapter_string: str):
    schema = tmp_path / "schema.yaml"
    schema.write_text(
        f"""
id: https://example.org/xco
name: xco-check
prefixes:
  XCO: http://purl.obolibrary.org/obo/XCO_
  linkml: https://w3id.org/linkml/
default_prefix: xco-check
default_range: string
classes:
  Exposure:
    tree_root: true
    attributes:
      id:
        identifier: true
      term:
        range: Term
        inlined: true
        bindings:
          - binds_value_of: id
            range: ExposureEnum
  Term:
    attributes:
      id:
enums:
  ExposureEnum:
    reachable_from:
      source_ontology: {adapter_string}
      source_nodes:
        - {XCO_ROOT}
      relationship_types:
        - rdfs:subClassOf
"""
    )
    config = tmp_path / "oak.yaml"
    config.write_text(f"ontology_adapters:\n  XCO: {adapter_string}\n")
    return schema, config


def _run_xco(tmp_path: Path, adapter_string: str, term: str):
    schema, config = _xco_binding_case(tmp_path, adapter_string)
    plugin = BindingValidationPlugin(
        oak_config_path=config, cache_labels=False, cache_enum_expansions=False, validate_labels=False
    )
    data = tmp_path / "data.yaml"
    data.write_text(f"id: e1\nterm:\n  id: {term}\n")
    validator = Validator(schema=str(schema), validation_plugins=[plugin])
    report = validator.validate_source(YamlLoader(data), target_class="Exposure")
    return plugin, report.results


@pytest.mark.integration
@pytest.mark.parametrize("term", XCO_FLAGGED)
def test_xco_flagged_terms_via_sqlite(tmp_path, term):
    plugin, results = _run_xco(tmp_path, "sqlite:obo:xco", term)
    assert [r.type for r in results] == ["binding_not4curation"], results
    assert "Not4Curation" in results[0].message
    assert plugin.get_not4curation_unchecked() == set()


@pytest.mark.integration
def test_xco_clean_term_via_sqlite(tmp_path):
    # The root is excluded from the closure, so bind a real child instead.
    plugin, results = _run_xco(tmp_path, "sqlite:obo:xco", "XCO:0000004")
    assert results == [], results
    assert plugin.get_not4curation_unchecked() == set()


@pytest.mark.integration
def test_xco_flagged_term_via_ols(tmp_path):
    """Pins the OLS4 payload shape (``synonyms`` list, ``obo_synonym`` null)."""
    plugin, results = _run_xco(tmp_path, "ols:xco", "XCO:0000294")
    assert [r.type for r in results] == ["binding_not4curation"], results
    assert results[0].message.startswith("Ontology term XCO:0000294 is marked 'Not4Curation'")
    assert plugin.get_not4curation_unchecked() == set()

