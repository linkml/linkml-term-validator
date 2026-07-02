"""Tests for offline validation mode (issue #51).

Offline mode guarantees no external ontology access: adapters are never
built and everything is resolved exclusively from the file cache. These
tests use the local simpleobo test ontology to populate a cache online,
then re-run validation offline against that cache.
"""

from pathlib import Path

from linkml_runtime.linkml_model.meta import EnumDefinition, ReachabilityQuery

from linkml_term_validator.models import ValidationConfig
from linkml_term_validator.plugins import DynamicEnumPlugin
from linkml_term_validator.validator import EnumValidator

OAK_CONFIG = Path("tests/data/test_oak_config.yaml")

TEST_SCHEMA = Path("tests/data/test_schema.yaml")


def _root_descendants_enum() -> EnumDefinition:
    """Dynamic enum: any descendant of the root term (TEST:0000001)."""
    return EnumDefinition(
        name="RootDescendantsEnum",
        reachable_from=ReachabilityQuery(
            source_nodes=["TEST:0000001"],
            relationship_types=["rdfs:subClassOf"],
        ),
    )


def test_offline_plugin_never_builds_adapter(tmp_path):
    """An offline plugin refuses to build an adapter for any prefix."""
    plugin = DynamicEnumPlugin(
        oak_config_path=OAK_CONFIG,
        cache_dir=tmp_path / "cache",
        offline=True,
    )
    assert plugin._get_adapter("TEST") is None
    assert plugin.get_ontology_label("TEST:0000001") is None


def test_offline_validates_dynamic_enum_from_complete_cache(tmp_path):
    """With a complete enum cache, offline mode validates without an adapter."""
    cache_dir = tmp_path / "cache"
    enum_def = _root_descendants_enum()

    # 1. Populate a complete enum cache online (greedy expansion marks it complete).
    online = DynamicEnumPlugin(
        oak_config_path=OAK_CONFIG,
        cache_dir=cache_dir,
        cache_strategy="greedy",
    )
    expanded = online.expand_enum(enum_def)
    assert "TEST:0000002" in expanded  # child term one
    assert online._is_enum_cache_complete(enum_def)

    # 2. Re-validate offline against the cache: valid descendants pass,
    #    non-members fail, all without building an adapter.
    offline = DynamicEnumPlugin(
        oak_config_path=OAK_CONFIG,
        cache_dir=cache_dir,
        offline=True,
    )
    assert offline.is_value_in_enum("TEST:0000002", enum_def) is True
    assert offline.is_value_in_enum("TEST:0000004", enum_def) is True
    assert offline.is_value_in_enum("TEST:9999999", enum_def) is False
    # Confirm the offline plugin never resorted to an adapter.
    assert offline._get_adapter("TEST") is None


def test_offline_without_cache_cannot_validate_dynamic_enum(tmp_path):
    """Offline mode with an empty cache cannot confirm dynamic-enum membership."""
    offline = DynamicEnumPlugin(
        oak_config_path=OAK_CONFIG,
        cache_dir=tmp_path / "cache",
        offline=True,
    )
    enum_def = _root_descendants_enum()
    # No adapter and no cache → the value cannot be confirmed as a member.
    assert offline.is_value_in_enum("TEST:0000002", enum_def) is False
    # The failure is a cache-incompleteness problem, not a data error.
    assert offline._offline_dynamic_enum_unmaterialized(enum_def) is True
    assert "not materialized" in offline._offline_unmaterialized_enum_message(
        "TEST:0000002", enum_def.name
    )


def test_offline_saturate_does_not_poison_cache(tmp_path):
    """Offline + saturate must not write an empty closure marked complete.

    Saturation offline would expand to an empty set (no adapter) and, without a
    guard, persist it with a `.complete` marker - poisoning the cache so every
    value looks invalid on later runs.
    """
    plugin = DynamicEnumPlugin(
        oak_config_path=OAK_CONFIG,
        cache_dir=tmp_path / "cache",
        offline=True,
        saturate_enum_caches=True,
    )
    enum_def = _root_descendants_enum()

    assert plugin.is_value_in_enum("TEST:0000002", enum_def) is False
    # No completion marker should have been written.
    assert plugin._is_enum_cache_complete(enum_def) is False
    assert plugin._get_enum_cache_marker_file(enum_def).exists() is False


def test_offline_expand_enum_does_not_persist_complete(tmp_path):
    """Offline expand_enum (the greedy code path) must not persist a bogus closure.

    Greedy pre_process calls expand_enum; offline that yields an empty set with no
    adapter. It must never be written to disk with a `.complete` marker.
    """
    plugin = DynamicEnumPlugin(
        oak_config_path=OAK_CONFIG,
        cache_dir=tmp_path / "cache",
        offline=True,
    )
    enum_def = _root_descendants_enum()

    plugin.expand_enum(enum_def, None)

    assert plugin._is_enum_cache_complete(enum_def) is False
    assert plugin._get_enum_cache_marker_file(enum_def).exists() is False


def test_binding_greedy_offline_unmaterialized_reports_diagnostic(tmp_path):
    """Greedy binding validation offline surfaces the diagnostic, never silently passes.

    With greedy strategy, an un-materialized dynamic enum is not pre-expanded
    offline, so `_validate_against_enum` must fall back to the per-value path and
    emit the "not materialized" diagnostic rather than the static no-op (which
    would let the value pass unchecked).
    """
    from linkml_runtime.utils.schemaview import SchemaView

    from linkml_term_validator.plugins import BindingValidationPlugin

    plugin = BindingValidationPlugin(
        oak_config_path=OAK_CONFIG,
        cache_dir=tmp_path / "cache",
        offline=True,
        cache_strategy="greedy",
    )
    plugin.schema_view = SchemaView(str(Path("tests/data/dynamic_enum_schema.yaml")))
    # Greedy pre_process skips offline-unmaterialized enums, so expanded_enums is
    # empty here - exactly the state _validate_against_enum must handle.
    # TEST:0000002 is deliberately absent from the empty tmp_path cache: with no
    # adapter and no cached closure it can't be confirmed, so the diagnostic fires.

    results = list(
        plugin._validate_against_enum(
            field_value="TEST:0000002",
            enum_name="RootDescendantsEnum",
            field_path="id",
            slot_name="term",
            instance={},
            target_class="Sample",
        )
    )

    assert len(results) == 1
    assert "not materialized" in results[0].message


# =============================================================================
# Offline "miss = error": an uncached term must never pass silently (#51)
# =============================================================================


def test_offline_schema_uncached_term_is_error(tmp_path):
    """Offline schema validation errors on uncached terms even for unconfigured prefixes.

    Without --strict and without an oak_config, an online run would treat these
    as INFO (skipped). Offline must escalate to ERROR so an incomplete cache can
    never pass green.
    """
    config = ValidationConfig(cache_dir=tmp_path / "cache", offline=True)
    validator = EnumValidator(config)
    result = validator.validate_schema(TEST_SCHEMA)

    assert result.has_errors()
    # Every meaning in the schema is uncached, so all are reported.
    assert result.error_count() == result.total_meanings_checked
    assert any("offline cache" in issue.message for issue in result.issues)


def test_offline_never_connects_even_with_obsolete_check(tmp_path, monkeypatch):
    """Offline validation with a populated cache: no errors, no connection.

    Guards the whole offline contract against regressions in the obsolete-term
    handling: `get_adapter` is patched to raise, so any attempt to reach an
    ontology service - including the new `is_obsolete` path - fails the test
    loudly instead of silently going to the network.
    """
    from linkml.validator import Validator  # type: ignore[import-untyped]
    from linkml.validator.loaders import YamlLoader  # type: ignore[import-untyped]

    cache_dir = tmp_path / "cache"
    schema = Path("tests/data/dynamic_enum_schema.yaml")
    data = tmp_path / "data.yaml"
    # Both values are valid members of their dynamic enums:
    #   TEST:0000006 (cell_cycle) is a descendant of TEST:0000005
    #   TEST:0000004 (grandchild) is a descendant of TEST:0000001
    data.write_text("- id: s1\n  process_type: TEST:0000006\n  term: TEST:0000004\n")

    # Phase 1 (online): greedy expansion materializes complete enum closures and
    # warms the label cache from the local simpleobo ontology.
    online = DynamicEnumPlugin(
        oak_config_path=OAK_CONFIG,
        cache_dir=cache_dir,
        cache_strategy="greedy",
    )
    Validator(schema=str(schema), validation_plugins=[online]).validate_source(
        YamlLoader(data), target_class="Sample"
    )

    # Phase 2 (offline): forbid every external connection.
    def _boom(*args, **kwargs):
        raise AssertionError("external connection attempted in offline mode")

    monkeypatch.setattr("linkml_term_validator.utils.oak_utils.get_adapter", _boom)

    offline = DynamicEnumPlugin(
        oak_config_path=OAK_CONFIG,
        cache_dir=cache_dir,
        offline=True,
    )
    report = Validator(
        schema=str(schema), validation_plugins=[offline]
    ).validate_source(YamlLoader(data), target_class="Sample")

    # Valid, fully-cached data validates clean with no connection attempt.
    assert len(report.results) == 0, report.results
    # And the obsolete check itself stays offline-safe (returns None, no connect).
    assert offline.is_obsolete("TEST:0000007") is None


def test_offline_schema_passes_when_cache_populated(tmp_path):
    """Offline schema validation passes once the needed labels are cached."""
    cache_dir = tmp_path / "cache"

    # Populate the cache online (uses the local simpleobo TEST ontology).
    online = EnumValidator(
        ValidationConfig(cache_dir=cache_dir, oak_config_path=OAK_CONFIG)
    )
    online.get_ontology_label("TEST:0000001")  # warms cache/test/terms.csv

    offline = EnumValidator(ValidationConfig(cache_dir=cache_dir, offline=True))
    # A cached term resolves; an uncached one is reported as an offline error.
    assert offline.get_ontology_label("TEST:0000001") == "root term"
    issues = offline.validate_curie_label_pairs(
        [
            ("TEST:0000001", "root term", "line:1"),
            ("TEST:0000099", "missing", "line:2"),
        ]
    )
    messages = [i.message for i in issues]
    assert any("TEST:0000099" in m and "offline cache" in m for m in messages)
    assert all("TEST:0000001" not in m for m in messages)
