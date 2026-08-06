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

from linkml_term_validator.models import ErrorMode
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
    assert (
        plugin.severity_for(ErrorMode.BINDING_LABEL_MISMATCH, Severity.WARN) is Severity.WARN
    )


def test_severity_for_returns_override(cache_dir):
    """A configured mode is remapped; unrelated modes are untouched."""
    plugin = BindingValidationPlugin(
        cache_dir=cache_dir,
        severity_overrides={"binding_label_mismatch": "ERROR"},
    )
    assert plugin.severity_for(ErrorMode.BINDING_LABEL_MISMATCH, Severity.WARN) is Severity.ERROR
    assert plugin.severity_for(ErrorMode.BINDING_VALIDATION, Severity.ERROR) is Severity.ERROR


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


def test_label_mismatch_defaults_to_warn(
    binding_schema_path, label_mismatch_data_path, cache_dir
):
    """Default behavior is unchanged: a label mismatch is advisory."""
    results = _validate(binding_schema_path, label_mismatch_data_path, cache_dir)

    mismatches = [r for r in results if r.type == "binding_label_mismatch"]
    assert len(mismatches) == 1
    assert mismatches[0].severity is Severity.WARN
    # No ERROR result means `linkml-validate` would exit 0 here.
    assert not [r for r in results if r.severity is Severity.ERROR]


def test_label_mismatch_can_be_promoted_to_error(
    binding_schema_path, label_mismatch_data_path, cache_dir
):
    """With the override, the same mismatch becomes a hard failure."""
    results = _validate(
        binding_schema_path,
        label_mismatch_data_path,
        cache_dir,
        severity_overrides={"binding_label_mismatch": "ERROR"},
    )

    mismatches = [r for r in results if r.type == "binding_label_mismatch"]
    assert len(mismatches) == 1
    assert mismatches[0].severity is Severity.ERROR
    # An ERROR result is what makes `linkml-validate` exit 1.
    assert [r for r in results if r.severity is Severity.ERROR]


def test_promoting_one_mode_leaves_others_alone(
    binding_schema_path, label_mismatch_data_path, cache_dir
):
    """Overriding a mode must not disturb the severity of other modes."""
    results = _validate(
        binding_schema_path,
        label_mismatch_data_path,
        cache_dir,
        severity_overrides={"binding_label_invalid": "ERROR"},
    )

    mismatches = [r for r in results if r.type == "binding_label_mismatch"]
    assert len(mismatches) == 1
    assert mismatches[0].severity is Severity.WARN


def test_error_mode_can_be_demoted(binding_schema_path, label_mismatch_data_path, cache_dir):
    """The mapping works in both directions, not just toward ERROR."""
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
    assert plugin.severity_for(ErrorMode.DYNAMIC_ENUM_VALIDATION, Severity.ERROR) is Severity.WARN


def test_permissible_value_plugin_accepts_severity_overrides(cache_dir):
    """PermissibleValueMeaningPlugin shares the base-class mechanism."""
    plugin = PermissibleValueMeaningPlugin(
        cache_dir=cache_dir,
        severity_overrides={"permissible_value_obsolete": "WARN"},
    )
    assert plugin.severity_for(ErrorMode.PERMISSIBLE_VALUE_OBSOLETE, Severity.ERROR) is Severity.WARN


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
    assert plugin.severity_for(ErrorMode.BINDING_LABEL_MISMATCH, Severity.WARN) is Severity.ERROR


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
    assert plugin.severity_for(ErrorMode.BINDING_LABEL_MISMATCH, Severity.WARN) is Severity.INFO
