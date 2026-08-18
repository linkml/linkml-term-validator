"""Tests for per-ErrorMode severity overrides.

The motivating case: ``linkml-validate`` exits non-zero only when a result has
``Severity.ERROR``, so a label mismatch reported at ``WARN`` is printed but the
process still succeeds. ``severity_overrides`` lets a schema owner promote a
specific problem to ``ERROR`` without turning every warning into a failure.
"""

from pathlib import Path

import pytest
from linkml.validator import Validator  # type: ignore[import-untyped]
from linkml.validator.report import Severity  # type: ignore[import-untyped]
from linkml.validator.loaders import default_loader_for_file  # type: ignore[import-untyped]

from linkml_term_validator.models import ErrorMode, SeverityLevel
from linkml_term_validator.plugins import (
    BindingValidationPlugin,
    DynamicEnumPlugin,
    PermissibleValueMeaningPlugin,
)

TEST_ADAPTER = "simpleobo:tests/data/test_ontology.obo"
DATA_DIR = Path(__file__).parent / "data"


@pytest.fixture
def binding_schema_path():
    """Schema whose `process` slot binds a term id and validates its label."""
    return DATA_DIR / "binding_label_schema.yaml"


@pytest.fixture
def label_mismatch_data_path():
    """Data with a deliberately wrong label for TEST:0000006."""
    return DATA_DIR / "binding_label_mismatch_data.yaml"


def _validate(schema_path, data_path, cache_dir, **plugin_kwargs):
    """Run BindingValidationPlugin over one data file and return its results."""
    plugin = BindingValidationPlugin(
        oak_adapter_string=TEST_ADAPTER,
        validate_labels=True,
        cache_labels=False,
        cache_enum_expansions=False,
        cache_dir=cache_dir,
        **plugin_kwargs,
    )
    validator = Validator(schema=str(schema_path), validation_plugins=[plugin])
    loader = default_loader_for_file(data_path)
    report = validator.validate_source(loader, target_class="Annotation")
    return report.results


# =============================================================================
# severity_for resolution
# =============================================================================


def test_severity_for_returns_default_when_unset(cache_dir):
    """An unconfigured mode keeps the severity the plugin passes in."""
    plugin = BindingValidationPlugin(cache_dir=cache_dir)
    assert plugin.severity_overrides == {}
    assert plugin.severity_for(ErrorMode.BINDING_LABEL_MISMATCH) is Severity.ERROR


def test_severity_for_returns_override(cache_dir):
    """A configured mode is remapped; unrelated modes are untouched."""
    plugin = BindingValidationPlugin(
        cache_dir=cache_dir,
        severity_overrides={"binding_label_mismatch": "WARN"},
    )
    assert plugin.severity_for(ErrorMode.BINDING_LABEL_MISMATCH) is Severity.WARN
    assert plugin.severity_for(ErrorMode.BINDING_VALIDATION) is Severity.ERROR


def test_severity_overrides_accepts_enum_members(cache_dir):
    """Keys and values may be ErrorMode/Severity members, not just strings."""
    plugin = BindingValidationPlugin(
        cache_dir=cache_dir,
        severity_overrides={ErrorMode.BINDING_LABEL_MISMATCH: Severity.ERROR},
    )
    assert plugin.severity_overrides == {"binding_label_mismatch": Severity.ERROR}


@pytest.mark.parametrize("spelling", ["ERROR", "error", " Error "])
def test_severity_values_are_case_insensitive(cache_dir, spelling):
    """YAML authors should not have to match the enum's exact casing."""
    plugin = BindingValidationPlugin(
        cache_dir=cache_dir,
        severity_overrides={"binding_label_mismatch": spelling},
    )
    assert plugin.severity_overrides["binding_label_mismatch"] is Severity.ERROR


def test_warning_is_accepted_as_alias_for_warn(cache_dir):
    """This project's own SeverityLevel spells it WARNING; accept both."""
    plugin = BindingValidationPlugin(
        cache_dir=cache_dir,
        severity_overrides={"binding_validation": "WARNING"},
    )
    assert plugin.severity_overrides["binding_validation"] is Severity.WARN


def test_unknown_error_mode_raises(cache_dir):
    """A typo'd key must fail loudly, not silently leave the default severity."""
    with pytest.raises(ValueError, match="Unknown severity_overrides key"):
        BindingValidationPlugin(
            cache_dir=cache_dir,
            severity_overrides={"binding_label_mismatchh": "ERROR"},
        )


def test_unknown_severity_raises(cache_dir):
    """An unusable severity value must fail loudly too."""
    with pytest.raises(ValueError, match="Unknown severity for 'binding_label_mismatch'"):
        BindingValidationPlugin(
            cache_dir=cache_dir,
            severity_overrides={"binding_label_mismatch": "CRITICAL"},
        )


def test_unknown_error_mode_message_lists_valid_keys(cache_dir):
    """The error should tell the author what they could have written."""
    with pytest.raises(ValueError) as exc_info:
        BindingValidationPlugin(cache_dir=cache_dir, severity_overrides={"nope": "ERROR"})
    assert "binding_label_mismatch" in str(exc_info.value)


# =============================================================================
# End-to-end: the reported severity drives linkml-validate's exit code
# =============================================================================


def test_label_mismatch_defaults_to_error(
    binding_schema_path, label_mismatch_data_path, cache_dir
):
    """A wrong label is a hard failure by default."""
    results = _validate(binding_schema_path, label_mismatch_data_path, cache_dir)

    mismatches = [r for r in results if r.type == "binding_label_mismatch"]
    assert len(mismatches) == 1
    assert mismatches[0].severity is Severity.ERROR
    # An ERROR result is what makes `linkml-validate` exit 1.
    assert [r for r in results if r.severity is Severity.ERROR]


def test_label_mismatch_can_be_demoted_to_warn(
    binding_schema_path, label_mismatch_data_path, cache_dir
):
    """Projects not ready to enforce labels can restore the advisory behavior."""
    results = _validate(
        binding_schema_path,
        label_mismatch_data_path,
        cache_dir,
        severity_overrides={"binding_label_mismatch": "WARN"},
    )

    mismatches = [r for r in results if r.type == "binding_label_mismatch"]
    assert len(mismatches) == 1
    assert mismatches[0].severity is Severity.WARN
    # No ERROR result means `linkml-validate` would exit 0 again.
    assert not [r for r in results if r.severity is Severity.ERROR]


def test_overriding_one_mode_leaves_others_alone(
    binding_schema_path, label_mismatch_data_path, cache_dir
):
    """Overriding a mode must not disturb the severity of other modes."""
    results = _validate(
        binding_schema_path,
        label_mismatch_data_path,
        cache_dir,
        severity_overrides={"binding_label_invalid": "WARN"},
    )

    mismatches = [r for r in results if r.type == "binding_label_mismatch"]
    assert len(mismatches) == 1
    assert mismatches[0].severity is Severity.ERROR


def test_error_mode_can_be_demoted(binding_schema_path, label_mismatch_data_path, cache_dir):
    """A mode can be dropped below WARN entirely."""
    results = _validate(
        binding_schema_path,
        label_mismatch_data_path,
        cache_dir,
        severity_overrides={"binding_label_mismatch": "INFO"},
    )

    mismatches = [r for r in results if r.type == "binding_label_mismatch"]
    assert mismatches[0].severity is Severity.INFO


# =============================================================================
# The other two plugins accept the same mapping
# =============================================================================


def test_dynamic_enum_plugin_accepts_severity_overrides(cache_dir):
    """DynamicEnumPlugin shares the base-class mechanism."""
    plugin = DynamicEnumPlugin(
        cache_dir=cache_dir,
        severity_overrides={"dynamic_enum_validation": "WARN"},
    )
    assert plugin.severity_for(ErrorMode.DYNAMIC_ENUM_VALIDATION) is Severity.WARN


def test_permissible_value_plugin_accepts_severity_overrides(cache_dir):
    """PermissibleValueMeaningPlugin shares the base-class mechanism."""
    plugin = PermissibleValueMeaningPlugin(
        cache_dir=cache_dir,
        severity_overrides={"permissible_value_obsolete": "WARN"},
    )
    assert plugin.severity_for(ErrorMode.PERMISSIBLE_VALUE_OBSOLETE) is Severity.WARN


def test_override_takes_precedence_over_strict_mode(cache_dir):
    """An explicit override wins over the coarser strict_mode flag."""
    plugin = PermissibleValueMeaningPlugin(
        cache_dir=cache_dir,
        strict_mode=True,
        severity_overrides={"permissible_value_label_mismatch": "WARN"},
    )
    resolved = plugin.severity_for(
        ErrorMode.PERMISSIBLE_VALUE_LABEL_MISMATCH,
        Severity.ERROR if plugin.strict_mode else Severity.WARN,
    )
    assert resolved is Severity.WARN


def test_strict_mode_still_applies_without_override(cache_dir):
    """strict_mode keeps working for modes the override map does not name."""
    plugin = PermissibleValueMeaningPlugin(cache_dir=cache_dir, strict_mode=True)
    resolved = plugin.severity_for(
        ErrorMode.PERMISSIBLE_VALUE_LABEL_MISMATCH,
        Severity.ERROR if plugin.strict_mode else Severity.WARN,
    )
    assert resolved is Severity.ERROR


# =============================================================================
# oak_config.yaml plumbing
# =============================================================================


def test_severity_overrides_can_come_from_oak_config(tmp_path, cache_dir):
    """The mapping may live in oak_config.yaml alongside the adapter map."""
    config_path = tmp_path / "oak_config.yaml"
    config_path.write_text(
        "ontology_adapters:\n"
        f"  TEST: {TEST_ADAPTER}\n"
        "severity_overrides:\n"
        "  binding_label_mismatch: ERROR\n"
    )
    plugin = BindingValidationPlugin(cache_dir=cache_dir, oak_config_path=config_path)
    assert plugin.severity_for(ErrorMode.BINDING_LABEL_MISMATCH) is Severity.ERROR
    assert plugin.severity_overrides["binding_label_mismatch"] is Severity.ERROR


def test_constructor_overrides_win_over_oak_config(tmp_path, cache_dir):
    """An explicit constructor argument beats the shared config file."""
    config_path = tmp_path / "oak_config.yaml"
    config_path.write_text(
        "ontology_adapters:\n"
        f"  TEST: {TEST_ADAPTER}\n"
        "severity_overrides:\n"
        "  binding_label_mismatch: ERROR\n"
    )
    plugin = BindingValidationPlugin(
        cache_dir=cache_dir,
        oak_config_path=config_path,
        severity_overrides={"binding_label_mismatch": "INFO"},
    )
    assert plugin.severity_for(ErrorMode.BINDING_LABEL_MISMATCH) is Severity.INFO

# =============================================================================
# Every ErrorMode is real, and its default is declared in one place
# =============================================================================


PLUGIN_SOURCE_DIR = Path(__file__).parent.parent / "src" / "linkml_term_validator" / "plugins"


@pytest.mark.parametrize("mode", list(ErrorMode), ids=lambda m: m.value)
def test_every_error_mode_is_actually_emitted(mode):
    """An ErrorMode with no emission site is dead config that silently does nothing."""
    sources = "".join(path.read_text() for path in PLUGIN_SOURCE_DIR.glob("*.py"))
    assert f'type="{mode.value}"' in sources, (
        f"ErrorMode.{mode.name} is documented as configurable but nothing emits it"
    )


@pytest.mark.parametrize("mode", list(ErrorMode), ids=lambda m: m.value)
def test_every_error_mode_declares_a_default_severity(mode):
    """severity_for() falls back to this, so it must be a real Severity."""
    assert mode.default_severity in set(Severity)


def test_severity_overrides_rejects_non_mapping(cache_dir):
    """A YAML list where a mapping was meant must fail loudly, not crash."""
    with pytest.raises(ValueError, match="must be a mapping"):
        BindingValidationPlugin(
            cache_dir=cache_dir,
            severity_overrides=["binding_label_mismatch"],  # type: ignore[arg-type]
        )


def test_severity_overrides_keys_tolerate_case_and_whitespace(cache_dir):
    """Keys are normalized like values are, so YAML sloppiness does not raise."""
    plugin = BindingValidationPlugin(
        cache_dir=cache_dir,
        severity_overrides={" Binding_Label_Mismatch ": "warn"},
    )
    assert plugin.severity_for(ErrorMode.BINDING_LABEL_MISMATCH) is Severity.WARN


def test_project_severity_level_members_are_accepted(cache_dir):
    """SeverityLevel.WARNING is why the WARNING alias exists; it must work."""
    plugin = BindingValidationPlugin(
        cache_dir=cache_dir,
        severity_overrides={ErrorMode.BINDING_LABEL_MISMATCH: SeverityLevel.WARNING},
    )
    assert plugin.severity_for(ErrorMode.BINDING_LABEL_MISMATCH) is Severity.WARN


def test_invalid_key_from_oak_config_raises(tmp_path, cache_dir):
    """A typo is most likely in the config file, so that path must validate too."""
    config_path = tmp_path / "oak_config.yaml"
    config_path.write_text(
        "ontology_adapters:\n"
        f"  TEST: {TEST_ADAPTER}\n"
        "severity_overrides:\n"
        "  binding_label_mismatchh: ERROR\n"
    )
    with pytest.raises(ValueError, match="Unknown severity_overrides key"):
        BindingValidationPlugin(cache_dir=cache_dir, oak_config_path=config_path)


# =============================================================================
# A null label is an absent label, not a malformed one
# =============================================================================


def test_explicit_null_label_is_not_a_validation_failure(
    binding_schema_path, tmp_path, cache_dir
):
    """YAML round-trips materialize optional slots as null; that is not a defect."""
    data_path = tmp_path / "null_label.yaml"
    data_path.write_text("annotation_id: ann:1\nprocess:\n  id: TEST:0000006\n  label: null\n")

    results = _validate(binding_schema_path, data_path, cache_dir)

    assert results == []


def test_non_string_label_is_still_a_failure(binding_schema_path, tmp_path, cache_dir):
    """A genuinely malformed label is still caught -- only null is exempted."""
    data_path = tmp_path / "int_label.yaml"
    data_path.write_text("annotation_id: ann:1\nprocess:\n  id: TEST:0000006\n  label: 42\n")

    results = _validate(binding_schema_path, data_path, cache_dir)

    invalid = [r for r in results if r.type == "binding_label_invalid"]
    assert len(invalid) == 1
    assert invalid[0].severity is Severity.ERROR


# =============================================================================
# End-to-end for the schema-side plugin
#
# These go through Validator rather than severity_for() directly, so a missed
# severity_for() call inside permissible_value_plugin.py would be caught here.
# =============================================================================


@pytest.fixture
def pv_schema_path():
    """Schema whose permissible value title disagrees with the ontology."""
    return DATA_DIR / "permissible_value_label_schema.yaml"


def _validate_schema_plugin(schema_path, cache_dir, **plugin_kwargs):
    """Run PermissibleValueMeaningPlugin over a schema and return its results."""
    plugin = PermissibleValueMeaningPlugin(
        oak_adapter_string=TEST_ADAPTER,
        cache_labels=False,
        cache_dir=cache_dir,
        **plugin_kwargs,
    )
    validator = Validator(schema=str(schema_path), validation_plugins=[plugin])
    return validator.validate({"sample_id": "s1"}, target_class="Sample").results


def test_pv_label_mismatch_defaults_to_warn(pv_schema_path, cache_dir):
    """Unchanged: the schema-side label check stays advisory by default."""
    results = _validate_schema_plugin(pv_schema_path, cache_dir)

    mismatches = [r for r in results if r.type == "permissible_value_label_mismatch"]
    assert len(mismatches) == 1
    assert mismatches[0].severity is Severity.WARN


def test_pv_label_mismatch_promoted_by_strict_mode(pv_schema_path, cache_dir):
    """strict_mode still promotes it, since no override names the mode."""
    results = _validate_schema_plugin(pv_schema_path, cache_dir, strict_mode=True)

    mismatches = [r for r in results if r.type == "permissible_value_label_mismatch"]
    assert mismatches[0].severity is Severity.ERROR


def test_pv_label_mismatch_promoted_by_override(pv_schema_path, cache_dir):
    """An override reaches an actually-emitted result, not just severity_for()."""
    results = _validate_schema_plugin(
        pv_schema_path,
        cache_dir,
        severity_overrides={"permissible_value_label_mismatch": "ERROR"},
    )

    mismatches = [r for r in results if r.type == "permissible_value_label_mismatch"]
    assert mismatches[0].severity is Severity.ERROR


def test_pv_override_beats_strict_mode_end_to_end(pv_schema_path, cache_dir):
    """The precedence documented for severity_for holds on a real result."""
    results = _validate_schema_plugin(
        pv_schema_path,
        cache_dir,
        strict_mode=True,
        severity_overrides={"permissible_value_label_mismatch": "WARN"},
    )

    mismatches = [r for r in results if r.type == "permissible_value_label_mismatch"]
    assert mismatches[0].severity is Severity.WARN


def test_docs_table_matches_the_error_mode_defaults():
    """The reference table is hand-written; keep it from drifting from the enum."""
    docs = (Path(__file__).parent.parent / "docs" / "plugin-reference.md").read_text()
    for mode in ErrorMode:
        row = f"| `{mode.value}` | `{mode.default_severity.value}`"
        assert row in docs, (
            f"docs/plugin-reference.md does not list {mode.value} as "
            f"{mode.default_severity.value}; update the Severity Overrides table"
        )


def test_no_emission_site_bypasses_severity_for():
    """A hardcoded severity would silently ignore the override map.

    The complement of test_every_error_mode_is_actually_emitted: that one
    catches a dead ErrorMode, this one catches a live result that no
    configuration can reach. Checked per line rather than as a substring so
    it also catches SeverityLevel (this project's other severity enum) and a
    severity bound to a local a few lines earlier.
    """
    offenders = []
    for path in sorted(PLUGIN_SOURCE_DIR.glob("*.py")):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if not stripped.startswith("severity="):
                continue
            # `severity=severity` is allowed: the local it reads is assigned
            # from severity_for() (permissible_value_plugin's strict_mode path).
            if "severity_for" in stripped or stripped.rstrip(",") == "severity=severity":
                continue
            offenders.append(f"{path.name}:{lineno}: {stripped}")

    assert not offenders, (
        "emission sites must route through severity_for() so severity_overrides "
        "can reach them:\n" + "\n".join(offenders)
    )


@pytest.mark.parametrize(
    "label_yaml",
    ["null", "[]", "[null]", "[null, null]"],
    ids=["null", "empty-list", "list-of-one-null", "list-of-nulls"],
)
def test_absent_labels_are_not_failures(binding_schema_path, tmp_path, label_yaml):
    """Every mechanical spelling of "no label supplied" must pass.

    An optional multivalued slot round-trips to [] or [null], exactly as an
    optional scalar round-trips to null.
    """
    data_path = tmp_path / "data.yaml"
    data_path.write_text(
        f"annotation_id: ann:1\nprocess:\n  id: TEST:0000006\n  label: {label_yaml}\n"
    )

    results = _validate(binding_schema_path, data_path, tmp_path / "cache")

    assert results == []


@pytest.mark.parametrize(
    ("label_yaml", "expected_type"),
    [
        ('""', "binding_label_mismatch"),
        ("42", "binding_label_invalid"),
        ("[42]", "binding_label_invalid"),
        ("{k: v}", "binding_label_invalid"),
    ],
    ids=["empty-string", "int", "list-of-int", "mapping"],
)
def test_present_but_wrong_labels_are_still_failures(
    binding_schema_path, tmp_path, label_yaml, expected_type
):
    """Exempting absent labels must not exempt genuinely bad ones.

    An empty string is deliberately included: nothing produces it
    mechanically, so it reads as a real defect rather than a missing value.
    """
    data_path = tmp_path / "data.yaml"
    data_path.write_text(
        f"annotation_id: ann:1\nprocess:\n  id: TEST:0000006\n  label: {label_yaml}\n"
    )

    results = _validate(binding_schema_path, data_path, tmp_path / "cache")

    assert [r.type for r in results] == [expected_type]
    assert results[0].severity is Severity.ERROR


def test_malformed_label_is_caught_without_ontology_resolution(
    binding_schema_path, tmp_path
):
    """A malformed label is a defect in the data, independent of the ontology.

    The type check must not sit behind a successful term lookup: otherwise the
    same file passes or fails depending on which prefixes happen to be
    configured, and on cache state.
    """
    data_path = tmp_path / "data.yaml"
    # OTHER: has no configured adapter, so the label cannot be resolved. The
    # value is also outside the bound enum, which is reported separately.
    data_path.write_text("annotation_id: ann:1\nprocess:\n  id: OTHER:0000001\n  label: 42\n")

    results = _validate(binding_schema_path, data_path, tmp_path / "cache")

    invalid = [r for r in results if r.type == "binding_label_invalid"]
    assert len(invalid) == 1
    assert invalid[0].severity is Severity.ERROR
    assert "must be a string" in invalid[0].message
