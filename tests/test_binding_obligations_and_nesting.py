"""Tests for binding obligation levels and multi-level binds_value_of paths."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from linkml.validator import Validator  # type: ignore[import-untyped]
from linkml.validator.loaders import YamlLoader  # type: ignore[import-untyped]
from linkml.validator.report import Severity  # type: ignore[import-untyped]
from linkml.validator.validation_context import ValidationContext  # type: ignore[import-untyped]
from linkml_runtime import SchemaView
from typer.testing import CliRunner

from linkml_term_validator.cli import app
from linkml_term_validator.plugins import BindingValidationPlugin

TEST_ONTOLOGY = "simpleobo:tests/data/test_ontology.obo"
OAK_CONFIG = Path("tests/data/test_oak_config.yaml")


def _run_plugin(schema_yaml: str, instance: dict, target_class: str, tmp_path: Path, **kwargs: Any):
    schema_path = tmp_path / "schema.yaml"
    schema_path.write_text(schema_yaml)
    plugin_kwargs: dict[str, Any] = {
        "oak_adapter_string": TEST_ONTOLOGY,
        "oak_config_path": OAK_CONFIG,
        "cache_labels": False,
        "cache_enum_expansions": False,
        "cache_dir": tmp_path / "cache",
    }
    plugin_kwargs.update(kwargs)
    plugin = BindingValidationPlugin(**plugin_kwargs)
    sv = SchemaView(str(schema_path))
    ctx = ValidationContext(schema=sv.schema, target_class=target_class)
    plugin.pre_process(ctx)
    return plugin, list(plugin.process(instance, ctx))


# =============================================================================
# Implicit '.' default & scalar / multivalued scalar bindings
# =============================================================================


@pytest.mark.parametrize("binds_line", ["", "binds_value_of: .", 'binds_value_of: ""'])
def test_scalar_binding_defaults_binds_value_of_to_dot(tmp_path, binds_line):
    """Omitting binds_value_of (or setting '.') binds the scalar slot value itself."""
    schema = f"""
id: https://example.org/scalar-binding
name: scalar-binding
prefixes:
  linkml: https://w3id.org/linkml/
  TEST: http://example.org/TEST_
default_range: string
classes:
  Sample:
    attributes:
      id:
        identifier: true
      process:
        range: string
        bindings:
          - range: BioProcessEnum
            {binds_line}
      tags:
        range: string
        multivalued: true
        bindings:
          - range: BioProcessEnum
            {binds_line}
enums:
  BioProcessEnum:
    reachable_from:
      source_ontology: {TEST_ONTOLOGY}
      source_nodes:
        - TEST:0000005
"""
    # Valid descendant of TEST:0000005 -> TEST:0000006 (cell_cycle)
    _, ok_results = _run_plugin(
        schema,
        {"id": "s1", "process": "TEST:0000006", "tags": ["TEST:0000006"]},
        "Sample",
        tmp_path,
    )
    assert ok_results == []

    # Invalid values on both single and multivalued scalar slots
    _, bad_results = _run_plugin(
        schema,
        {"id": "s2", "process": "TEST:0000002", "tags": ["TEST:0000006", "TEST:0000003"]},
        "Sample",
        tmp_path,
    )
    assert len(bad_results) == 2
    assert all(r.type == "binding_validation" and r.severity is Severity.ERROR for r in bad_results)
    contexts = [tuple(r.context) for r in bad_results]
    assert any("path: process" in c and "field: ." in c for c in contexts)
    assert any("path: tags[1]" in c and "field: ." in c for c in contexts)


def test_dot_binding_on_dict_reports_scalar_error_instead_of_type_error(tmp_path):
    """If binds_value_of is omitted on a complex object, report a clean error rather than crashing."""
    schema = """
id: https://example.org/bad-dot
name: bad-dot
prefixes:
  linkml: https://w3id.org/linkml/
default_range: string
classes:
  Holder:
    attributes:
      term:
        range: Term
        inlined: true
        bindings:
          - range: StaticEnum
  Term:
    attributes:
      id:
enums:
  StaticEnum:
    permissible_values:
      A:
"""
    _, results = _run_plugin(
        schema,
        {"term": {"id": "A"}},
        "Holder",
        tmp_path,
        validate_labels=False,
    )
    assert len(results) == 1
    assert results[0].type == "binding_validation"
    assert results[0].severity is Severity.ERROR
    assert "must be a scalar" in results[0].message


# =============================================================================
# Multi-level binds_value_of paths (nested dicts, list fan-out, integer indices)
# =============================================================================


def test_multilevel_dict_path_validates_enum_and_nested_label(tmp_path):
    """binds_value_of: 'wrapper.term.id' resolves 3 levels and checks label on Term."""
    schema = f"""
id: https://example.org/deep-path
name: deep-path
prefixes:
  linkml: https://w3id.org/linkml/
  rdfs: http://www.w3.org/2000/01/rdf-schema#
  TEST: http://example.org/TEST_
default_range: string
classes:
  Record:
    attributes:
      descriptor:
        range: OuterDescriptor
        inlined: true
        bindings:
          - binds_value_of: wrapper.term.id
            range: RootChildEnum
  OuterDescriptor:
    attributes:
      name:
      wrapper:
        range: InnerWrapper
        inlined: true
  InnerWrapper:
    attributes:
      term:
        range: OntologyTerm
        inlined: true
  OntologyTerm:
    attributes:
      id:
      display_name:
        implements:
          - rdfs:label
enums:
  RootChildEnum:
    reachable_from:
      source_ontology: {TEST_ONTOLOGY}
      source_nodes:
        - TEST:0000001
"""
    # Valid ID and matching OntologyTerm.display_name ("child term one")
    # Note OuterDescriptor.name has an unrelated string that must NOT be treated as the term label
    _, ok_results = _run_plugin(
        schema,
        {
            "descriptor": {
                "name": "Unrelated outer name",
                "wrapper": {"term": {"id": "TEST:0000002", "display_name": "child term one"}},
            }
        },
        "Record",
        tmp_path,
        validate_labels=True,
    )
    assert ok_results == []

    # Absent intermediate optional object ('wrapper' omitted) passes cleanly
    _, absent_results = _run_plugin(
        schema,
        {"descriptor": {"name": "Unmapped concept"}},
        "Record",
        tmp_path,
        validate_labels=True,
    )
    assert absent_results == []

    # Wrong label on OntologyTerm.display_name reports path 'descriptor.wrapper.term'
    _, bad_label = _run_plugin(
        schema,
        {
            "descriptor": {
                "name": "child term one",
                "wrapper": {"term": {"id": "TEST:0000002", "display_name": "wrong label"}},
            }
        },
        "Record",
        tmp_path,
        validate_labels=True,
    )
    assert len(bad_label) == 1
    assert bad_label[0].type == "binding_label_mismatch"
    assert "path: descriptor.wrapper.term" in bad_label[0].context
    assert "label_field: display_name" in bad_label[0].context


@pytest.mark.parametrize("path_expr", ["coding.code", "coding[*].code"])
def test_multilevel_list_autofanout_validates_all_items_and_labels(tmp_path, path_expr):
    """FHIR CodeableConcept-style 'coding.code' fans out across all list items."""
    schema = f"""
id: https://example.org/codeable
name: codeable
prefixes:
  linkml: https://w3id.org/linkml/
  rdfs: http://www.w3.org/2000/01/rdf-schema#
  TEST: http://example.org/TEST_
default_range: string
classes:
  Observation:
    attributes:
      concept:
        range: CodeableConcept
        inlined: true
        bindings:
          - binds_value_of: "{path_expr}"
            range: RootChildEnum
  CodeableConcept:
    attributes:
      text:
      coding:
        range: Coding
        multivalued: true
        inlined_as_list: true
  Coding:
    attributes:
      code:
      display:
        implements:
          - rdfs:label
enums:
  RootChildEnum:
    reachable_from:
      source_ontology: {TEST_ONTOLOGY}
      source_nodes:
        - TEST:0000001
"""
    instance = {
        "concept": {
            "text": "Summary text",
            "coding": [
                {"code": "TEST:0000002", "display": "child term one"},  # valid
                {"code": "TEST:0000006", "display": "cell_cycle"},  # out of RootChildEnum
                {"code": "TEST:0000003", "display": "wrong display"},  # valid ID, bad label
            ],
        }
    }
    _, results = _run_plugin(schema, instance, "Observation", tmp_path, validate_labels=True)
    assert len(results) == 2

    enum_err = next(r for r in results if r.type == "binding_validation")
    assert "field: coding[1].code" in enum_err.context
    assert "path: concept" in enum_err.context

    label_err = next(r for r in results if r.type == "binding_label_mismatch")
    assert "path: concept.coding[2]" in label_err.context
    assert "curie: TEST:0000003" in label_err.context


@pytest.mark.parametrize("path_expr", ["extensions.0.value", "extensions[0].value"])
def test_multilevel_explicit_integer_index(tmp_path, path_expr):
    """Explicit integer index constrains only that element in the list."""
    schema = """
id: https://example.org/indexed
name: indexed
prefixes:
  linkml: https://w3id.org/linkml/
default_range: string
classes:
  Record:
    attributes:
      item:
        range: Holder
        inlined: true
        bindings:
          - binds_value_of: "{path_expr}"
            range: AllowedTagEnum
  Holder:
    attributes:
      extensions:
        range: Extension
        multivalued: true
        inlined_as_list: true
  Extension:
    attributes:
      tag:
      value:
enums:
  AllowedTagEnum:
    permissible_values:
      PRIMARY:
      SECONDARY:
""".replace("{path_expr}", path_expr)

    # Index 0 is PRIMARY (valid); index 1 is free text (ignored)
    _, ok_results = _run_plugin(
        schema,
        {
            "item": {
                "extensions": [
                    {"tag": "role", "value": "PRIMARY"},
                    {"tag": "free_note", "value": "arbitrary text"},
                ]
            }
        },
        "Record",
        tmp_path,
        validate_labels=False,
        strict=False,
    )
    assert ok_results == []

    # Empty list -> index 0 out of range -> skipped cleanly
    _, empty_results = _run_plugin(
        schema,
        {"item": {"extensions": []}},
        "Record",
        tmp_path,
        validate_labels=False,
        strict=False,
    )
    assert empty_results == []

    # Index 0 invalid -> reported at extensions[0].value
    _, bad_results = _run_plugin(
        schema,
        {"item": {"extensions": [{"tag": "role", "value": "BOGUS"}]}},
        "Record",
        tmp_path,
        validate_labels=False,
        strict=False,
    )
    assert len(bad_results) == 1
    assert "field: extensions[0].value" in bad_results[0].context


def test_multilevel_leaf_list_autofanout(tmp_path):
    """When the leaf field itself is a list of scalars, each item is validated."""
    schema = """
id: https://example.org/leaf-list
name: leaf-list
prefixes:
  linkml: https://w3id.org/linkml/
default_range: string
classes:
  Record:
    attributes:
      meta:
        range: MetaBox
        inlined: true
        bindings:
          - binds_value_of: keywords
            range: KeywordEnum
  MetaBox:
    attributes:
      keywords:
        range: string
        multivalued: true
enums:
  KeywordEnum:
    permissible_values:
      ALPHA:
      BETA:
"""
    _, results = _run_plugin(
        schema,
        {"meta": {"keywords": ["ALPHA", "GAMMA", "BETA"]}},
        "Record",
        tmp_path,
        validate_labels=False,
        strict=False,
    )
    assert len(results) == 1
    assert "GAMMA" in results[0].message
    assert "field: keywords[1]" in results[0].context


# =============================================================================
# Obligation levels: REQUIRED, RECOMMENDED, OPTIONAL, EXAMPLE, DISCOURAGED
# =============================================================================


def _obligation_schema(obligation_level: str | None, dynamic: bool = False) -> str:
    ol_line = f"obligation_level: {obligation_level}" if obligation_level else ""
    if dynamic:
        enum_block = f"""
  TargetEnum:
    reachable_from:
      source_ontology: {TEST_ONTOLOGY}
      source_nodes:
        - TEST:0000005
"""
    else:
        enum_block = """
  TargetEnum:
    permissible_values:
      CELL_CYCLE:
        meaning: TEST:0000006
"""
    return f"""
id: https://example.org/obligation
name: obligation
prefixes:
  linkml: https://w3id.org/linkml/
  TEST: http://example.org/TEST_
default_range: string
classes:
  Annotation:
    tree_root: true
    attributes:
      id:
        identifier: true
      term:
        range: Term
        inlined: true
        bindings:
          - binds_value_of: id
            range: TargetEnum
            {ol_line}
  Term:
    attributes:
      id:
      label:
enums:
{enum_block}
"""


@pytest.mark.parametrize("dynamic", [False, True])
@pytest.mark.parametrize("strategy", ["progressive", "greedy"])
@pytest.mark.parametrize("level", [None, "REQUIRED"])
def test_obligation_required_emits_error_on_non_member(tmp_path, dynamic, strategy, level):
    """REQUIRED (and omitted default) emits ERROR when value is outside enum."""
    schema = _obligation_schema(level, dynamic=dynamic)
    _, ok = _run_plugin(
        schema,
        {"id": "a1", "term": {"id": "TEST:0000006", "label": "cell_cycle"}},
        "Annotation",
        tmp_path,
        cache_strategy=strategy,
    )
    assert ok == []

    _, bad = _run_plugin(
        schema,
        {"id": "a2", "term": {"id": "TEST:0000002", "label": "child term one"}},
        "Annotation",
        tmp_path,
        cache_strategy=strategy,
    )
    assert len(bad) == 1
    assert bad[0].type == "binding_validation"
    assert bad[0].severity is Severity.ERROR


@pytest.mark.parametrize("dynamic", [False, True])
@pytest.mark.parametrize("strategy", ["progressive", "greedy"])
def test_obligation_recommended_emits_warn_on_non_member(tmp_path, dynamic, strategy):
    """RECOMMENDED emits WARN when value is outside enum and nothing when inside."""
    schema = _obligation_schema("RECOMMENDED", dynamic=dynamic)
    _, ok = _run_plugin(
        schema,
        {"id": "a1", "term": {"id": "TEST:0000006", "label": "cell_cycle"}},
        "Annotation",
        tmp_path,
        cache_strategy=strategy,
    )
    assert ok == []

    _, warn_results = _run_plugin(
        schema,
        {"id": "a2", "term": {"id": "TEST:0000002", "label": "child term one"}},
        "Annotation",
        tmp_path,
        cache_strategy=strategy,
    )
    assert len(warn_results) == 1
    assert warn_results[0].type == "binding_validation"
    assert warn_results[0].severity is Severity.WARN
    assert "obligation_level: RECOMMENDED" in warn_results[0].context


@pytest.mark.parametrize("level", ["OPTIONAL", "EXAMPLE"])
@pytest.mark.parametrize("dynamic", [False, True])
def test_obligation_optional_and_example_skip_enum_membership(tmp_path, level, dynamic):
    """OPTIONAL and EXAMPLE skip enum membership checks (and greedy pre-expansion)."""
    schema = _obligation_schema(level, dynamic=dynamic)
    plugin, results = _run_plugin(
        schema,
        {"id": "a1", "term": {"id": "TEST:0000002", "label": "child term one"}},
        "Annotation",
        tmp_path,
        cache_strategy="greedy",
    )
    assert results == []
    # Greedy pre_process should not materialize enums only used by OPTIONAL/EXAMPLE
    assert plugin.expanded_enums == {}

    # Ontology hygiene checks (label mismatch, nonexistent term) still run when a CURIE is supplied
    _, bad_label = _run_plugin(
        schema,
        {"id": "a2", "term": {"id": "TEST:0000002", "label": "wrong label"}},
        "Annotation",
        tmp_path,
    )
    assert [r.type for r in bad_label] == ["binding_label_mismatch"]


@pytest.mark.parametrize("dynamic", [False, True])
@pytest.mark.parametrize("strategy", ["progressive", "greedy"])
def test_obligation_discouraged_warns_only_when_value_in_enum(tmp_path, dynamic, strategy):
    """DISCOURAGED emits WARN when value IS in the enum, and passes when outside."""
    schema = _obligation_schema("DISCOURAGED", dynamic=dynamic)

    # Outside discouraged enum -> clean
    _, outside = _run_plugin(
        schema,
        {"id": "a1", "term": {"id": "TEST:0000002", "label": "child term one"}},
        "Annotation",
        tmp_path,
        cache_strategy=strategy,
    )
    assert outside == []

    # Inside discouraged enum -> WARN
    _, inside = _run_plugin(
        schema,
        {"id": "a2", "term": {"id": "TEST:0000006", "label": "cell_cycle"}},
        "Annotation",
        tmp_path,
        cache_strategy=strategy,
    )
    assert len(inside) == 1
    assert inside[0].type == "binding_validation"
    assert inside[0].severity is Severity.WARN
    assert "discouraged" in inside[0].message
    assert "obligation_level: DISCOURAGED" in inside[0].context


def test_recommended_binding_does_not_suppress_not4curation_error(tmp_path):
    """A WARN from RECOMMENDED must not suppress an ERROR from Not4Curation (#70)."""
    # TargetEnum only contains TEST:0000006; TEST:0000008 is a real Not4Curation term outside TargetEnum
    schema = _obligation_schema("RECOMMENDED", dynamic=False)
    _, results = _run_plugin(
        schema,
        {"id": "a1", "term": {"id": "TEST:0000008", "label": "flagged process"}},
        "Annotation",
        tmp_path,
    )
    types_and_severities = {(r.type, r.severity) for r in results}
    assert ("binding_validation", Severity.WARN) in types_and_severities
    assert ("binding_not4curation", Severity.ERROR) in types_and_severities


def test_multiple_bindings_compose_without_duplicate_label_errors(tmp_path):
    """Broad REQUIRED + narrow RECOMMENDED + DISCOURAGED bindings compose cleanly."""
    schema = f"""
id: https://example.org/multi-binding
name: multi-binding
prefixes:
  linkml: https://w3id.org/linkml/
  TEST: http://example.org/TEST_
default_range: string
classes:
  Annotation:
    attributes:
      term:
        range: Term
        inlined: true
        bindings:
          - binds_value_of: id
            range: AllRootDescendantsEnum
            obligation_level: REQUIRED
          - binds_value_of: id
            range: PreferredSlimEnum
            obligation_level: RECOMMENDED
          - binds_value_of: id
            range: DiscouragedSlimEnum
            obligation_level: DISCOURAGED
  Term:
    attributes:
      id:
      label:
enums:
  AllRootDescendantsEnum:
    reachable_from:
      source_ontology: {TEST_ONTOLOGY}
      source_nodes:
        - TEST:0000001
  PreferredSlimEnum:
    permissible_values:
      CHILD_ONE:
        meaning: TEST:0000002
      GRANDCHILD:
        meaning: TEST:0000004
  DiscouragedSlimEnum:
    permissible_values:
      GRANDCHILD:
        meaning: TEST:0000004
"""
    # TEST:0000002: in REQUIRED, in RECOMMENDED, not in DISCOURAGED -> clean
    _, clean = _run_plugin(
        schema,
        {"term": {"id": "TEST:0000002", "label": "child term one"}},
        "Annotation",
        tmp_path,
    )
    assert clean == []

    # TEST:0000003: in REQUIRED, NOT in RECOMMENDED -> 1 WARN
    _, non_preferred = _run_plugin(
        schema,
        {"term": {"id": "TEST:0000003", "label": "child term two"}},
        "Annotation",
        tmp_path,
    )
    assert [(r.type, r.severity) for r in non_preferred] == [("binding_validation", Severity.WARN)]

    # TEST:0000004 with wrong label: in REQUIRED & RECOMMENDED, in DISCOURAGED -> 1 WARN + 1 label ERROR (not 3!)
    _, discouraged_bad_label = _run_plugin(
        schema,
        {"term": {"id": "TEST:0000004", "label": "wrong label"}},
        "Annotation",
        tmp_path,
    )
    assert sorted((r.type, r.severity.value) for r in discouraged_bad_label) == [
        ("binding_label_mismatch", "ERROR"),
        ("binding_validation", "WARN"),
    ]


def test_encouraged_alias_and_invalid_obligation_level():
    """ENCOURAGED normalizes to RECOMMENDED; unknown levels raise ValueError."""
    assert BindingValidationPlugin._normalize_obligation_level("ENCOURAGED") == "RECOMMENDED"
    with pytest.raises(ValueError, match="Unknown obligation_level"):
        BindingValidationPlugin._normalize_obligation_level("MANDATORY")

    plugin = BindingValidationPlugin(cache_labels=False)
    bad_binding = SimpleNamespace(binds_value_of=".", range="SomeEnum", obligation_level="BOGUS")
    with pytest.raises(ValueError, match="Unknown obligation_level"):
        list(plugin._validate_binding("X", bad_binding, "s", {}, "Cls"))


def test_cli_recommended_binding_exits_zero_with_fail_on_error(tmp_path):
    """CLI end-to-end: RECOMMENDED violation exits 0 under --fail-on error and 1 under --strict."""
    schema_path = tmp_path / "schema.yaml"
    schema_path.write_text(_obligation_schema("RECOMMENDED", dynamic=False))
    data_path = tmp_path / "data.yaml"
    data_path.write_text("id: a1\nterm:\n  id: TEST:0000002\n  label: child term one\n")

    runner = CliRunner()
    res_ok = runner.invoke(
        app,
        [
            "validate-data",
            str(data_path),
            "-s",
            str(schema_path),
            "-t",
            "Annotation",
            "-c",
            str(OAK_CONFIG),
            "--no-cache",
            "--fail-on",
            "error",
        ],
    )
    assert res_ok.exit_code == 0, res_ok.output
    assert "WARN" in res_ok.output

    res_strict = runner.invoke(
        app,
        [
            "validate-data",
            str(data_path),
            "-s",
            str(schema_path),
            "-t",
            "Annotation",
            "-c",
            str(OAK_CONFIG),
            "--no-cache",
            "--fail-on",
            "error",
            "--strict",
        ],
    )
    assert res_strict.exit_code == 1

    # Also verify standard LinkML Validator integration
    plugin = BindingValidationPlugin(
        oak_config_path=OAK_CONFIG,
        cache_labels=False,
        cache_enum_expansions=False,
    )
    report = Validator(schema=str(schema_path), validation_plugins=[plugin]).validate_source(
        YamlLoader(str(data_path)), target_class="Annotation"
    )
    assert len(report.results) == 1
    assert report.results[0].severity is Severity.WARN
