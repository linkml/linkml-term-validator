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
from linkml_term_validator.utils import EmptyReachableClosureError

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


class _EmptyClosureAdapter:
    """Adapter stub: the source node resolves but reaches an empty closure.

    Simulates the OLS4 MONDO defect (dismech#7012) where MONDO:0000001 has a
    label but no descendants, without needing a network call.
    """

    def label(self, curie):
        # Every TEST term resolves (including the source root and the value under
        # test); only the *closure* is broken, mirroring the OLS4 MONDO defect.
        return f"term {curie}" if str(curie).startswith("TEST:") else None

    def descendants(self, curies, predicates=None):
        return iter(())  # resolves, but nothing below it

    def ancestors(self, curies, predicates=None):
        return iter(())


def _island_enum() -> EnumDefinition:
    """Enum whose (leaf) source node resolves but has no descendants."""
    return EnumDefinition(
        name="IslandEnum",
        reachable_from=ReachabilityQuery(
            source_nodes=["TEST:0000004"],  # grandchild leaf → zero descendants
            relationship_types=["rdfs:subClassOf"],
        ),
    )


def test_progressive_empty_source_closure_fails_loud(plugin):
    """dismech#7012: a resolving source node that reaches nothing must fail loud.

    A leaf source node resolves but has no descendants, so the default
    (traverse-down) enum matches nothing. Rejecting a same-ontology term must
    raise a distinct error rather than returning a silent wrong ``False`` that
    looks like an ordinary "not in enum" result.
    """
    with pytest.raises(EmptyReachableClosureError) as excinfo:
        plugin.is_value_in_enum("TEST:0000002", _island_enum())
    assert excinfo.value.source_node == "TEST:0000004"
    assert excinfo.value.traverse_up is False


def test_progressive_healthy_source_still_reports_true_negative(plugin):
    """A genuine out-of-enum term under a healthy source must stay a quiet False.

    The integrity guard must not fire when the source node has a real closure:
    TEST:0000005 is a separate root, correctly *not* reachable from TEST:0000001,
    and that verdict is a legitimate ``False`` — not an EmptyReachableClosureError.
    """
    healthy = EnumDefinition(
        name="HealthyEnum",
        reachable_from=ReachabilityQuery(
            source_nodes=["TEST:0000001"],  # root → has descendants
            relationship_types=["rdfs:subClassOf"],
        ),
    )
    assert plugin.is_value_in_enum("TEST:0000004", healthy) is True  # in-enum
    assert plugin.is_value_in_enum("TEST:0000005", healthy) is False  # true negative


def test_progressive_multi_source_union_leaf_sibling_is_safe(plugin):
    """A childless leaf source is safe when a sibling source is populated.

    The integrity guard requires *every* same-ontology source node to reach
    nothing, so a legitimate union (child_one has a descendant; child_two is a
    leaf) still reports ordinary results — True for a member, a quiet False for a
    genuine non-member — never EmptyReachableClosureError.
    """
    union = EnumDefinition(
        name="UnionEnum",
        reachable_from=ReachabilityQuery(
            source_nodes=["TEST:0000002", "TEST:0000003"],  # populated + leaf
            relationship_types=["rdfs:subClassOf"],
        ),
    )
    assert plugin.is_value_in_enum("TEST:0000004", union) is True  # under child_one
    assert plugin.is_value_in_enum("TEST:0000005", union) is False  # genuine negative


def test_progressive_empty_source_closure_via_stub_adapter(tmp_path):
    """The guard triggers on an adapter whose source node resolves but reaches nothing.

    Uses a stub standing in for the OLS4 MONDO graph (resolvable root, empty
    descendant closure) so the OLS-specific defect is covered offline.
    """
    plugin = DynamicEnumPlugin(
        oak_config_path=OAK_CONFIG,
        cache_labels=False,
        cache_enum_expansions=False,
        cache_dir=tmp_path / "cache",
    )
    plugin.ontology._adapter_cache["TEST"] = _EmptyClosureAdapter()

    enum_def = EnumDefinition(
        name="OlsLikeEnum",
        reachable_from=ReachabilityQuery(
            source_nodes=["TEST:0000001"],
            relationship_types=["rdfs:subClassOf"],
        ),
    )
    with pytest.raises(EmptyReachableClosureError):
        plugin.is_value_in_enum("TEST:0000002", enum_def)


def test_greedy_empty_source_closure_is_not_cached_as_complete(tmp_path):
    """dismech#7012: an empty closure must not be persisted as a complete cache.

    Regenerating an enum whose source node reaches nothing (the OLS4 MONDO case)
    previously produced an empty-but-``complete`` cache that silently rejected
    every term forever. Expansion must now raise and leave no completion marker.
    """
    plugin = DynamicEnumPlugin(
        oak_config_path=OAK_CONFIG,
        cache_labels=False,
        cache_enum_expansions=True,  # caching on, so we can inspect the marker
        cache_dir=tmp_path / "cache",
    )
    enum_def = _island_enum()

    with pytest.raises(EmptyReachableClosureError):
        plugin.expand_enum(enum_def, use_cache=True)

    assert plugin._is_enum_cache_complete(enum_def) is False


def test_empty_source_closure_probe_is_cached(plugin):
    """The source-closure integrity probe is computed once and memoized."""
    adapter = plugin._get_adapter("TEST")
    # A leaf source node resolves but reaches nothing → flagged and cached.
    assert plugin._reachable_source_reaches_nothing(
        adapter, "TEST:0000004", ["rdfs:subClassOf"], False
    )
    assert plugin._source_closure_empty_cache[("TEST:0000004", ("rdfs:subClassOf",), False)] is True
    # A healthy root is recorded as non-empty (no false positive).
    assert not plugin._reachable_source_reaches_nothing(
        adapter, "TEST:0000001", ["rdfs:subClassOf"], False
    )
    assert (
        plugin._source_closure_empty_cache[("TEST:0000001", ("rdfs:subClassOf",), False)] is False
    )


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


def _legacy_key(enum_def) -> str:
    """Byte-for-byte reproduction of the pre-0.4.1 (v0.4.0) cache-key algorithm.

    Kept independent of the production code so a future refactor that silently
    changes the legacy hash fails this test instead of quietly re-churning every
    user's cache.
    """
    import hashlib

    key_parts = [enum_def.name or ""]
    if enum_def.reachable_from:
        q = enum_def.reachable_from
        key_parts.append(f"rf:{','.join(sorted(q.source_nodes or []))}")
        key_parts.append(f"rt:{','.join(sorted(q.relationship_types or []))}")
        key_parts.append(f"is:{q.include_self if hasattr(q, 'include_self') else True}")
        key_parts.append(f"tu:{q.traverse_up if hasattr(q, 'traverse_up') else False}")
    if enum_def.concepts:
        key_parts.append(f"c:{','.join(sorted(enum_def.concepts))}")
    return hashlib.md5("|".join(key_parts).encode()).hexdigest()[:12]


def test_cache_key_backward_compatible_for_legacy_fields(tmp_path):
    """Enums using only pre-0.4.1 fields must keep their v0.4.0 hash (no churn).

    Regression guard: changing the enum cache key renames every
    ``enums/<name>_<hash>.csv`` file, orphaning previously expanded caches and
    forcing a full re-fetch from ontology services on upgrade. reachable_from /
    concepts enums must therefore hash exactly as they did in 0.4.0.
    """
    plugin = DynamicEnumPlugin(
        cache_labels=False,
        cache_enum_expansions=False,
        cache_dir=tmp_path / "cache",
    )

    # Pinned hash of the v0.4.0 key string
    # "E|rf:TEST:0000001|rt:rdfs:subClassOf|is:None|tu:None".
    assert plugin._get_enum_cache_key(_base_enum()) == "35c34a815840"

    # And it must agree with the independent legacy reproduction across the
    # legacy-covered fields (reachable_from params, explicit flags, concepts).
    cases = [
        _base_enum(),
        EnumDefinition(name="E", concepts=["X:2", "X:1"]),
        EnumDefinition(
            name="E",
            concepts=["X:2", "X:1"],
            reachable_from=ReachabilityQuery(
                source_nodes=["A:2", "A:1"],
                relationship_types=["r2", "r1"],
                include_self=True,
                traverse_up=True,
            ),
        ),
    ]
    for enum_def in cases:
        assert plugin._get_enum_cache_key(enum_def) == _legacy_key(enum_def)


def test_new_constructs_do_not_perturb_legacy_hash(tmp_path):
    """Adding a 0.4.1 construct must not change the hash of a legacy-only sibling.

    The new key segments (matches/permissible_values/include/minus/inherits)
    only participate when present, so an enum that omits them keeps the exact
    hash it would have had before those segments existed.
    """
    plugin = DynamicEnumPlugin(
        cache_labels=False,
        cache_enum_expansions=False,
        cache_dir=tmp_path / "cache",
    )

    # A legacy-only enum matches its independent legacy hash...
    assert plugin._get_enum_cache_key(_base_enum()) == _legacy_key(_base_enum())
    # ...while an otherwise-identical enum that *adds* a matches clause diverges
    # (the new construct genuinely changes the expanded value set).
    with_matches = _base_enum()
    with_matches.matches = MatchQuery(identifier_pattern="TEST:.*")
    assert plugin._get_enum_cache_key(with_matches) != _legacy_key(_base_enum())
