"""Tests for offline validation mode (issue #51).

Offline mode guarantees no external ontology access: adapters are never
built and everything is resolved exclusively from the file cache. These
tests use the local simpleobo test ontology to populate a cache online,
then re-run validation offline against that cache.
"""

from pathlib import Path

from linkml_runtime.linkml_model.meta import EnumDefinition, ReachabilityQuery

from linkml_term_validator.plugins import DynamicEnumPlugin

OAK_CONFIG = Path("tests/data/test_oak_config.yaml")


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
    # No adapter and no cache → the value cannot be confirmed as a member.
    assert offline.is_value_in_enum("TEST:0000002", _root_descendants_enum()) is False
