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
