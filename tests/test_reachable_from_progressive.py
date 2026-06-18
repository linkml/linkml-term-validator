"""Progressive-validation tests for reachable_from semantics and cache safety.

Covers regression tests for:
- #34 traverse_up must include ancestors, not behave like traverse_down
- #35 a failed expansion must not be cached as a complete closure
- #36 the enum cache key must change when include/minus/inherits change
- #37 include_self defaults and explicit values must be consistent

Uses the local simpleobo test ontology (offline). Hierarchy:

    TEST:0000001 root
      TEST:0000002 child one
        TEST:0000004 grandchild
      TEST:0000003 child two
    TEST:0000005 biological_process
      TEST:0000006 cell_cycle
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from linkml_runtime.linkml_model.meta import (
    AnonymousEnumExpression,
    EnumDefinition,
    MatchQuery,
    PermissibleValue,
    ReachabilityQuery,
)

from linkml_term_validator.plugins import DynamicEnumPlugin

OAK_CONFIG = Path("tests/data/test_oak_config.yaml")


@pytest.fixture
def plugin(tmp_path):
    """A progressive-mode plugin wired to the local test ontology."""
    return DynamicEnumPlugin(
        oak_config_path=OAK_CONFIG,
        cache_labels=False,
        cache_enum_expansions=False,
        cache_dir=tmp_path / "cache",
    )


def test_traverse_up_includes_ancestors_not_descendants(plugin):
    """#34: traverse_up should accept ancestors of the source node, reject siblings.

    Source node is child-one (TEST:0000002). Going up, its ancestor is the
    root (TEST:0000001), which must be valid. Child-two (TEST:0000003) is a
    sibling, not an ancestor, so it must be rejected.
    """
    enum_def = EnumDefinition(
        name="AncestorsOfChildOne",
        reachable_from=ReachabilityQuery(
            source_nodes=["TEST:0000002"],
            relationship_types=["rdfs:subClassOf"],
            traverse_up=True,
        ),
    )

    # Root is an ancestor of child-one → valid under traverse_up.
    assert plugin.is_value_in_enum("TEST:0000001", enum_def) is True
    # Child-two is a sibling, not an ancestor → invalid.
    assert plugin.is_value_in_enum("TEST:0000003", enum_def) is False


@pytest.mark.parametrize(
    ("traverse_up", "source_node", "reachable_node"),
    [
        (False, "TEST:0000002", "TEST:0000004"),
        (True, "TEST:0000002", "TEST:0000001"),
    ],
    ids=["descendants", "ancestors"],
)
def test_missing_include_self_defaults_to_excluding_source_node(
    plugin, traverse_up, source_node, reachable_node
):
    """#37: missing include_self should consistently exclude the source node."""
    query = SimpleNamespace(
        source_nodes=[source_node],
        relationship_types=["rdfs:subClassOf"],
        traverse_up=traverse_up,
    )

    assert plugin._is_value_in_reachable_from(source_node, query) is False
    assert plugin._is_value_in_reachable_from(reachable_node, query) is True

    expanded = plugin._expand_reachable_from(query)
    assert source_node not in expanded
    assert reachable_node in expanded


@pytest.mark.parametrize(
    ("traverse_up", "source_node", "reachable_node"),
    [
        (False, "TEST:0000002", "TEST:0000004"),
        (True, "TEST:0000002", "TEST:0000001"),
    ],
    ids=["descendants", "ancestors"],
)
@pytest.mark.parametrize(
    ("include_self", "source_is_reachable"),
    [(False, False), (True, True)],
    ids=["include-self-false", "include-self-true"],
)
def test_include_self_is_honored_in_progressive_and_expanded_paths(
    plugin, traverse_up, source_node, reachable_node, include_self, source_is_reachable
):
    """#37: include_self should behave the same in per-value and expanded paths."""
    query = ReachabilityQuery(
        source_nodes=[source_node],
        relationship_types=["rdfs:subClassOf"],
        traverse_up=traverse_up,
        include_self=include_self,
    )

    assert plugin._is_value_in_reachable_from(source_node, query) is source_is_reachable
    assert plugin._is_value_in_reachable_from(reachable_node, query) is True

    expanded = plugin._expand_reachable_from(query)
    assert (source_node in expanded) is source_is_reachable
    assert reachable_node in expanded


class _BoomAdapter:
    """Adapter stub whose graph queries fail, simulating a backend outage."""

    def descendants(self, *args, **kwargs):
        raise RuntimeError("ontology backend unavailable")

    def ancestors(self, *args, **kwargs):
        raise RuntimeError("ontology backend unavailable")


def test_failed_expansion_is_not_cached_as_complete(tmp_path):
    """#35: a failed reachable_from expansion must not be persisted as complete.

    Previously the OAK query exception was swallowed, yielding an empty set
    that was written to disk and marked complete — poisoning every later run.
    """
    plugin = DynamicEnumPlugin(
        oak_config_path=OAK_CONFIG,
        cache_labels=False,
        cache_enum_expansions=True,  # caching on, so we can inspect the marker
        cache_dir=tmp_path / "cache",
    )
    # Force the adapter for the TEST prefix to fail on graph queries.
    plugin.ontology._adapter_cache["TEST"] = _BoomAdapter()

    enum_def = EnumDefinition(
        name="BoomEnum",
        reachable_from=ReachabilityQuery(
            source_nodes=["TEST:0000005"],
            relationship_types=["rdfs:subClassOf"],
        ),
    )

    # The failure must surface, not be silently swallowed.
    with pytest.raises(RuntimeError):
        plugin.expand_enum(enum_def, use_cache=True)

    # And the cache must not have been marked complete.
    assert plugin._is_enum_cache_complete(enum_def) is False


def _base_enum() -> EnumDefinition:
    return EnumDefinition(
        name="E",
        reachable_from=ReachabilityQuery(
            source_nodes=["TEST:0000001"],
            relationship_types=["rdfs:subClassOf"],
        ),
    )


def _with_inherits() -> EnumDefinition:
    e = _base_enum()
    e.inherits = ["ParentEnum"]
    return e


def _with_include() -> EnumDefinition:
    e = _base_enum()
    e.include = [AnonymousEnumExpression(concepts=["TEST:0000006"])]
    return e


def _with_minus() -> EnumDefinition:
    e = _base_enum()
    e.minus = [AnonymousEnumExpression(concepts=["TEST:0000006"])]
    return e


def _with_top_level_permissible_value(meaning: str) -> EnumDefinition:
    e = _base_enum()
    e.permissible_values = {"EXTRA": PermissibleValue(text="EXTRA", meaning=meaning)}
    return e


def _with_include_match(pattern: str) -> EnumDefinition:
    e = _base_enum()
    e.include = [AnonymousEnumExpression(matches=MatchQuery(identifier_pattern=pattern))]
    return e


def _with_include_permissible_value(meaning: str) -> EnumDefinition:
    e = _base_enum()
    e.include = [
        AnonymousEnumExpression(
            permissible_values={"EXTRA": PermissibleValue(text="EXTRA", meaning=meaning)}
        )
    ]
    return e


@pytest.mark.parametrize(
    "variant",
    [_with_inherits, _with_include, _with_minus],
    ids=["inherits", "include", "minus"],
)
def test_cache_key_changes_with_set_operation_clause(tmp_path, variant):
    """#36: include / minus / inherits must participate in the enum cache key.

    Two enums identical except for one of these clauses must not collide on
    the same cache file, otherwise editing the clause returns stale results.
    """
    plugin = DynamicEnumPlugin(
        cache_labels=False,
        cache_enum_expansions=False,
        cache_dir=tmp_path / "cache",
    )
    base_key = plugin._get_enum_cache_key(_base_enum())
    variant_key = plugin._get_enum_cache_key(variant())
    assert base_key != variant_key


def test_cache_key_changes_with_top_level_matches_clause(tmp_path):
    """#36: matches clauses must participate in the enum cache key."""
    plugin = DynamicEnumPlugin(
        cache_labels=False,
        cache_enum_expansions=False,
        cache_dir=tmp_path / "cache",
    )
    enum1 = EnumDefinition(name="E", matches=MatchQuery(identifier_pattern="TEST:000000[12]"))
    enum2 = EnumDefinition(name="E", matches=MatchQuery(identifier_pattern="TEST:000000[34]"))

    assert plugin._get_enum_cache_key(enum1) != plugin._get_enum_cache_key(enum2)


def test_cache_key_changes_with_top_level_permissible_value_meaning(tmp_path):
    """#36: top-level permissible value meanings affect expanded enum values."""
    plugin = DynamicEnumPlugin(
        cache_labels=False,
        cache_enum_expansions=False,
        cache_dir=tmp_path / "cache",
    )

    key1 = plugin._get_enum_cache_key(_with_top_level_permissible_value("TEST:0000002"))
    key2 = plugin._get_enum_cache_key(_with_top_level_permissible_value("TEST:0000003"))
    assert key1 != key2


def test_cache_key_changes_with_include_match_clause(tmp_path):
    """#36: include/minus expression matches clauses must affect the cache key."""
    plugin = DynamicEnumPlugin(
        cache_labels=False,
        cache_enum_expansions=False,
        cache_dir=tmp_path / "cache",
    )

    key1 = plugin._get_enum_cache_key(_with_include_match("TEST:000000[12]"))
    key2 = plugin._get_enum_cache_key(_with_include_match("TEST:000000[34]"))
    assert key1 != key2


def test_cache_key_changes_with_include_permissible_value_meaning(tmp_path):
    """#36: include/minus expression permissible value meanings affect the cache key."""
    plugin = DynamicEnumPlugin(
        cache_labels=False,
        cache_enum_expansions=False,
        cache_dir=tmp_path / "cache",
    )

    key1 = plugin._get_enum_cache_key(_with_include_permissible_value("TEST:0000002"))
    key2 = plugin._get_enum_cache_key(_with_include_permissible_value("TEST:0000003"))
    assert key1 != key2
