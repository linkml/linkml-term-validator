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
from linkml_term_validator.utils import (
    EmptyReachableClosureError,
    InconsistentReachabilityError,
)

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


class _InconsistentClosureAdapter:
    """Adapter stub reproducing the OLS4 MONDO defect (dismech#7012) offline.

    The defect is a CURIE/identifier merge, not a broken hierarchy: OLS4 returns
    the term at IRI ``.../MONDO_0000001`` under the wrong ``obo_id``
    ``AFO_O:0000001`` (a cross-ontology term also labelled "disease"). As a
    result, by CURIE the source root's own descendants never list it among their
    ancestors — ``MONDO:0000001`` → descendant ``MONDO:0004992``, whose ancestors
    report the root as ``AFO_O:0000001`` instead of ``MONDO:0000001``. This is
    exactly what oaklib's OLS adapter hands the validator.
    """

    def label(self, curie):
        return f"term {curie}" if str(curie).startswith("MONDO:") else None

    def descendants(self, curies, predicates=None):
        if "MONDO:0000001" in list(curies):
            return iter(["MONDO:0004992"])  # hierarchy intact by IRI
        return iter(())

    def ancestors(self, curies, predicates=None):
        # The root appears, but under its corrupted CURIE (AFO_O:0000001), so a
        # CURIE match for MONDO:0000001 misses it — the observed OLS4 behavior.
        if "MONDO:0004992" in list(curies):
            return iter(["AFO_O:0000001"])
        return iter(())


class _EmptyReverseAdapter:
    """Adapter whose reverse direction returns empty for every term.

    The source root has descendants, but ``ancestors()`` answers with nothing for
    everything — an adapter that does not really support the reverse direction
    (wrong predicate spelling, focus-ontology restriction, no-op). An empty reverse
    closure must NOT be read as proof of inconsistency (that would hard-abort a
    healthy setup), so the guard must stay quiet here.
    """

    def label(self, curie):
        return f"term {curie}" if str(curie).startswith("MONDO:") else None

    def descendants(self, curies, predicates=None):
        if "MONDO:0000001" in list(curies):
            return iter(["MONDO:0004992"])
        return iter(())

    def ancestors(self, curies, predicates=None):
        return iter(())  # reverse direction unsupported → answers empty for all


class _InconsistentTraverseUpAdapter:
    """traverse_up analogue of the CURIE-merge defect.

    Under ``traverse_up`` the forward closure is the source's *ancestors* and the
    reverse check is *descendants*. Here source ``MONDO:0005`` has ancestor
    ``MONDO:0001``, but ``descendants(MONDO:0001)`` is non-empty yet omits
    ``MONDO:0005`` — the round trip is broken in the traverse_up direction.
    """

    def label(self, curie):
        return f"term {curie}" if str(curie).startswith("MONDO:") else None

    def ancestors(self, curies, predicates=None):
        if "MONDO:0005" in list(curies):
            return iter(["MONDO:0001"])
        return iter(())

    def descendants(self, curies, predicates=None):
        # MONDO:0001's descendants are non-empty (reverse direction works) but do
        # not round-trip back to MONDO:0005.
        if "MONDO:0001" in list(curies):
            return iter(["MONDO:9999"])
        return iter(())


class _OlsPagedAdapter:
    """OLS-shaped adapter with a configurable REST descendants page.

    Mirrors OAK's OLS adapter on the versions where native ``descendants()``
    yields nothing (or raises) and the REST ``_ols_descendants`` fallback is
    required. ``rest_descendants`` is the paged descendant list the fallback
    returns; ``native`` selects whether native ``descendants()`` returns empty or
    raises; ``ancestors_map`` supplies the reverse direction. Lets one stub cover
    the fallback-wiring, raising-native, and truncation-boundary cases offline.
    """

    focus_ontology = "mondo"

    def __init__(self, rest_descendants, native="empty", ancestors_map=None, native_yield=None):
        self._rest = list(rest_descendants)
        self._native = native
        self._anc = ancestors_map or {}
        self._native_yield = list(native_yield or [])
        outer = self

        class _Client:
            def get_paged(self, path, key=None):
                if path.endswith("descendants"):
                    return [{"obo_id": d} for d in outer._rest]
                return []

        self.client = _Client()

    def curie_to_uri(self, curie):
        return "http://purl.obolibrary.org/obo/" + str(curie).replace(":", "_")

    def label(self, curie):
        return f"term {curie}" if str(curie).startswith("MONDO:") else None

    def descendants(self, curies, predicates=None):
        if self._native == "raise":
            raise RuntimeError("native descendants unavailable")
        if self._native == "yield_then_raise":

            def _gen():
                yield from self._native_yield
                raise RuntimeError("native descendants failed mid-stream")

            return _gen()
        return iter(())

    def ancestors(self, curies, predicates=None):
        out: list[str] = []
        for c in list(curies):
            out.extend(self._anc.get(c, []))
        return iter(out)


class _ManyDescAdapter:
    """Adapter yielding many same-prefix descendants in descending order.

    Lets the sample's sort + even-stride behavior be asserted deterministically:
    the returned sample must be ascending-sorted and evenly spaced regardless of
    the generator's (here reversed) order.
    """

    def label(self, curie):
        return f"term {curie}"

    def descendants(self, curies, predicates=None):
        return iter([f"MONDO:{i:07d}" for i in range(20, 0, -1)])

    def ancestors(self, curies, predicates=None):
        return iter(())


class _YieldsThenRaisesAdapter:
    """Adapter whose native ``ancestors()`` yields a couple terms, then raises.

    Simulates lazy paging that fails on a later page (or a malformed record
    mid-stream). The partial closure seen before the error is truncated evidence
    and must NOT be read as an exhaustive "without the target" answer.
    """

    def label(self, curie):
        return f"term {curie}" if str(curie).startswith("MONDO:") else None

    def ancestors(self, curies, predicates=None):
        def _gen():
            yield "MONDO:1"
            yield "MONDO:2"
            raise RuntimeError("paging failed on page 2")

        return _gen()

    def descendants(self, curies, predicates=None):
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


def test_progressive_inconsistent_directions_fail_loud(tmp_path):
    """dismech#7012: a CURIE-level reachability inconsistency must fail loud.

    This reproduces the *real* OLS4 MONDO defect: the root is present in the
    hierarchy (by IRI) but returned under a corrupted CURIE (``AFO_O:0000001``),
    so an ancestor-based membership check matching by CURIE silently returns a
    wrong ``False``. Rejecting a same-ontology term must raise
    InconsistentReachabilityError, naming the descendant that fails the round
    trip — not a quiet "not in enum" result.
    """
    plugin = DynamicEnumPlugin(
        oak_config_path=OAK_CONFIG,
        cache_labels=False,
        cache_enum_expansions=False,
        cache_dir=tmp_path / "cache",
    )
    plugin.ontology._adapter_cache["MONDO"] = _InconsistentClosureAdapter()

    enum_def = EnumDefinition(
        name="DiseaseEnum",
        reachable_from=ReachabilityQuery(
            source_nodes=["MONDO:0000001"],
            relationship_types=["rdfs:subClassOf"],
        ),
    )
    with pytest.raises(InconsistentReachabilityError) as excinfo:
        # MONDO:0004992 is genuinely a descendant of the root, but the severed
        # ancestor direction makes the check wrongly say "not reachable".
        plugin.is_value_in_enum("MONDO:0004992", enum_def)
    assert excinfo.value.source_node == "MONDO:0000001"
    assert excinfo.value.witness == "MONDO:0004992"


def test_progressive_inconsistency_uses_ols_descendants_fallback(tmp_path):
    """The forward sample must consult the OLS fallback when native yields nothing.

    Re-review finding: OAK's OLS ``descendants()`` can return nothing, so sampling
    only the native traversal would leave the guard a silent no-op on a live
    ``ols:`` adapter for the default direction — the exact dismech#7012 case. With
    the bounded ``_ols_descendants`` fallback the forward sample is non-empty and
    the inconsistency is still detected.
    """
    plugin = DynamicEnumPlugin(
        oak_config_path=OAK_CONFIG,
        cache_labels=False,
        cache_enum_expansions=False,
        cache_dir=tmp_path / "cache",
    )
    plugin.ontology._adapter_cache["MONDO"] = _OlsPagedAdapter(
        rest_descendants=["MONDO:0004992"],
        native="empty",
        ancestors_map={"MONDO:0004992": ["AFO_O:0000001"]},
    )
    enum_def = EnumDefinition(
        name="DiseaseEnum",
        reachable_from=ReachabilityQuery(
            source_nodes=["MONDO:0000001"], relationship_types=["rdfs:subClassOf"]
        ),
    )
    with pytest.raises(InconsistentReachabilityError) as excinfo:
        plugin.is_value_in_enum("MONDO:0004992", enum_def)
    assert excinfo.value.witness == "MONDO:0004992"


def test_progressive_inconsistency_when_native_descendants_raises(tmp_path):
    """The forward sample must fall back even when native descendants() *raises*.

    Re-review finding #2: `_reverse_reaches` recovered via the REST fallback on a
    native exception but `_sample_closure` returned early, so an OLS adapter whose
    `descendants()` raised left the forward sample empty → guard silently disabled.
    Both paths must now be symmetric.
    """
    plugin = DynamicEnumPlugin(
        oak_config_path=OAK_CONFIG,
        cache_labels=False,
        cache_enum_expansions=False,
        cache_dir=tmp_path / "cache",
    )
    plugin.ontology._adapter_cache["MONDO"] = _OlsPagedAdapter(
        rest_descendants=["MONDO:0004992"],
        native="raise",
        ancestors_map={"MONDO:0004992": ["AFO_O:0000001"]},
    )
    enum_def = EnumDefinition(
        name="DiseaseEnum",
        reachable_from=ReachabilityQuery(
            source_nodes=["MONDO:0000001"], relationship_types=["rdfs:subClassOf"]
        ),
    )
    with pytest.raises(InconsistentReachabilityError):
        plugin.is_value_in_enum("MONDO:0004992", enum_def)


def test_reverse_reaches_truncation_boundary(tmp_path, monkeypatch):
    """A fallback truncated at the cap must read as UNANSWERABLE, not WITHOUT.

    Re-review finding #1: `_ols_descendants` skips the source node during
    collection, so a set of exactly the cap reliably signals truncation (even when
    the REST page includes the start term). Truncated evidence must not be misread
    as "exhausted without the target", which would hard-abort a healthy ontology.
    """
    plugin = DynamicEnumPlugin(
        oak_config_path=OAK_CONFIG,
        cache_labels=False,
        cache_enum_expansions=False,
        cache_dir=tmp_path / "cache",
    )
    # Patch the class attribute (not the instance) so the test still exercises the
    # boundary if the production read is ever refactored to type(self)/class access.
    monkeypatch.setattr(DynamicEnumPlugin, "_REVERSE_FALLBACK_LIMIT", 3)
    # REST page includes the start term plus > cap distinct members, none the target.
    adapter = _OlsPagedAdapter(
        rest_descendants=["MONDO:0000001", "MONDO:1", "MONDO:2", "MONDO:3", "MONDO:4"]
    )
    assert (
        plugin._reverse_reaches(
            adapter, "descendants", "MONDO:0000001", ["rdfs:subClassOf"], "MONDO:9999"
        )
        == plugin._REVERSE_UNANSWERABLE
    )
    # Exhausted below the cap without the target → demonstrably WITHOUT.
    adapter2 = _OlsPagedAdapter(rest_descendants=["MONDO:0000001", "MONDO:1"])
    assert (
        plugin._reverse_reaches(
            adapter2, "descendants", "MONDO:0000001", ["rdfs:subClassOf"], "MONDO:9999"
        )
        == plugin._REVERSE_WITHOUT
    )
    # Target present past nothing special → FOUND via stop_at.
    adapter3 = _OlsPagedAdapter(rest_descendants=["MONDO:1", "MONDO:9999", "MONDO:2"])
    assert (
        plugin._reverse_reaches(
            adapter3, "descendants", "MONDO:0000001", ["rdfs:subClassOf"], "MONDO:9999"
        )
        == plugin._REVERSE_FOUND
    )
    # Exhausted at *exactly* the cap is ambiguous with truncation → conservatively
    # UNANSWERABLE (never a false WITHOUT).
    adapter4 = _OlsPagedAdapter(
        rest_descendants=["MONDO:0000001", "MONDO:1", "MONDO:2", "MONDO:3"]
    )
    assert (
        plugin._reverse_reaches(
            adapter4, "descendants", "MONDO:0000001", ["rdfs:subClassOf"], "MONDO:9999"
        )
        == plugin._REVERSE_UNANSWERABLE
    )


def test_reverse_reaches_native_error_midstream_is_unanswerable(tmp_path):
    """A native traversal that yields then raises is truncated → UNANSWERABLE.

    Re-review finding #1: partial native evidence must not be reported as
    `_REVERSE_WITHOUT` (which would feed a false inconsistency verdict). The
    reverse direction here is `ancestors` (no descendants fallback), so the errored
    partial closure must resolve to UNANSWERABLE, not WITHOUT.
    """
    plugin = DynamicEnumPlugin(
        oak_config_path=OAK_CONFIG,
        cache_labels=False,
        cache_enum_expansions=False,
        cache_dir=tmp_path / "cache",
    )
    adapter = _YieldsThenRaisesAdapter()
    assert (
        plugin._reverse_reaches(
            adapter, "ancestors", "MONDO:0004992", ["rdfs:subClassOf"], "MONDO:0000001"
        )
        == plugin._REVERSE_UNANSWERABLE
    )


def test_reverse_reaches_fallback_overrides_truncated_native(tmp_path):
    """When native descendants() yields then raises, the fallback is authoritative.

    Re-review finding: exercises the `(not saw_any or errored)` gate with
    saw_any=True — a partial native view (two terms, then a RuntimeError) must not
    decide the verdict; the bounded REST fallback overrides it. FOUND when the REST
    page contains the target, WITHOUT when it exhausts without it.
    """
    plugin = DynamicEnumPlugin(
        oak_config_path=OAK_CONFIG,
        cache_labels=False,
        cache_enum_expansions=False,
        cache_dir=tmp_path / "cache",
    )
    found = _OlsPagedAdapter(
        rest_descendants=["MONDO:9999"],
        native="yield_then_raise",
        native_yield=["MONDO:1", "MONDO:2"],
    )
    assert (
        plugin._reverse_reaches(
            found, "descendants", "MONDO:start", ["rdfs:subClassOf"], "MONDO:9999"
        )
        == plugin._REVERSE_FOUND
    )
    without = _OlsPagedAdapter(
        rest_descendants=["MONDO:1", "MONDO:2"],
        native="yield_then_raise",
        native_yield=["MONDO:1", "MONDO:2"],
    )
    assert (
        plugin._reverse_reaches(
            without, "descendants", "MONDO:start", ["rdfs:subClassOf"], "MONDO:9999"
        )
        == plugin._REVERSE_WITHOUT
    )


def test_progressive_empty_reverse_closure_does_not_flag(tmp_path):
    """An adapter whose reverse direction answers empty for all must not be flagged.

    Guards against the false positive of treating an empty-but-answered reverse
    closure as proof of inconsistency: the reverse direction must demonstrably
    work (a non-empty closure that omits the source) before the guard fires. Here
    ``ancestors()`` is empty for everything, so rejecting a non-member is a quiet
    ``False``, never InconsistentReachabilityError.
    """
    plugin = DynamicEnumPlugin(
        oak_config_path=OAK_CONFIG,
        cache_labels=False,
        cache_enum_expansions=False,
        cache_dir=tmp_path / "cache",
    )
    plugin.ontology._adapter_cache["MONDO"] = _EmptyReverseAdapter()
    enum_def = EnumDefinition(
        name="DiseaseEnum",
        reachable_from=ReachabilityQuery(
            source_nodes=["MONDO:0000001"], relationship_types=["rdfs:subClassOf"]
        ),
    )
    # A resolvable non-member: reachable check fails, but the empty reverse
    # direction means we cannot (and must not) confirm an inconsistency.
    assert plugin.is_value_in_enum("MONDO:0700000", enum_def) is False


def test_progressive_traverse_up_inconsistency_fails_loud(tmp_path):
    """The round-trip guard also covers the traverse_up direction.

    Locks the forward/reverse direction mapping (forward=ancestors,
    reverse=descendants under traverse_up); a swap would go unnoticed otherwise.
    """
    plugin = DynamicEnumPlugin(
        oak_config_path=OAK_CONFIG,
        cache_labels=False,
        cache_enum_expansions=False,
        cache_dir=tmp_path / "cache",
    )
    plugin.ontology._adapter_cache["MONDO"] = _InconsistentTraverseUpAdapter()
    enum_def = EnumDefinition(
        name="AncestorsEnum",
        reachable_from=ReachabilityQuery(
            source_nodes=["MONDO:0005"],
            relationship_types=["rdfs:subClassOf"],
            traverse_up=True,
        ),
    )
    with pytest.raises(InconsistentReachabilityError) as excinfo:
        plugin.is_value_in_enum("MONDO:7777", enum_def)
    assert excinfo.value.source_node == "MONDO:0005"
    assert excinfo.value.witness == "MONDO:0001"
    assert excinfo.value.traverse_up is True


def test_progressive_traverse_up_healthy_does_not_flag(plugin):
    """A round-trip-consistent traverse_up enum reports ordinary negatives.

    Source TEST:0000004 (a leaf) has ancestors {root, child_one}; each of those
    lists the leaf among its descendants, so the round trip holds and a genuine
    non-ancestor stays a quiet ``False``.
    """
    enum_def = EnumDefinition(
        name="TUHealthy",
        reachable_from=ReachabilityQuery(
            source_nodes=["TEST:0000004"],
            relationship_types=["rdfs:subClassOf"],
            traverse_up=True,
        ),
    )
    assert plugin.is_value_in_enum("TEST:0000001", enum_def) is True  # ancestor of leaf
    assert plugin.is_value_in_enum("TEST:0000003", enum_def) is False  # not an ancestor


def test_progressive_healthy_source_still_reports_true_negative(plugin):
    """A genuine out-of-enum term under a healthy source must stay a quiet False.

    The integrity guard must not fire when the adapter is round-trip consistent:
    TEST:0000005 is a separate root, correctly *not* reachable from TEST:0000001,
    and that verdict is a legitimate ``False`` — not an error.
    """
    healthy = EnumDefinition(
        name="HealthyEnum",
        reachable_from=ReachabilityQuery(
            source_nodes=["TEST:0000001"],  # root → has descendants that round-trip
            relationship_types=["rdfs:subClassOf"],
        ),
    )
    assert plugin.is_value_in_enum("TEST:0000004", healthy) is True  # in-enum
    assert plugin.is_value_in_enum("TEST:0000005", healthy) is False  # true negative


def test_progressive_leaf_source_negative_does_not_flag(plugin):
    """A childless leaf source producing a correct negative must not be flagged.

    A leaf source node has no descendants, so nothing round-trips and there is no
    inconsistency to detect: rejecting a non-member is a legitimate quiet
    ``False``, never an error. (This is the false positive an earlier
    empty-closure heuristic would have raised.)
    """
    leaf_enum = EnumDefinition(
        name="LeafEnum",
        reachable_from=ReachabilityQuery(
            source_nodes=["TEST:0000004"],  # grandchild leaf, zero descendants
            relationship_types=["rdfs:subClassOf"],
        ),
    )
    assert plugin.is_value_in_enum("TEST:0000002", leaf_enum) is False


def test_progressive_multi_source_union_leaf_sibling_is_safe(plugin):
    """A childless leaf source is safe alongside a populated, consistent sibling.

    A legitimate union (child_one has a round-tripping descendant; child_two is a
    leaf) reports ordinary results — True for a member, a quiet False for a
    genuine non-member — never an error.
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


def test_greedy_empty_source_closure_is_not_cached_as_complete(tmp_path):
    """dismech#7012: an empty expansion must not be persisted as a complete cache.

    A resolvable source node that expands to nothing (e.g. the only source is a
    childless leaf) would otherwise produce an empty-but-``complete`` cache that
    silently rejects every term forever. Expansion must raise instead and leave
    no completion marker.
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


def test_greedy_empty_reachable_from_plus_permissible_values_not_flagged(tmp_path):
    """An empty reachable_from is fine when other clauses populate the enum.

    Re-review finding: EmptyReachableClosureError must fire per-enum, not per
    reachable_from clause. A leaf source contributes nothing, but the enum also
    carries permissible_values, so the whole enum is non-empty and must not abort.
    """
    plugin = DynamicEnumPlugin(
        oak_config_path=OAK_CONFIG,
        cache_labels=False,
        cache_enum_expansions=False,
        cache_dir=tmp_path / "cache",
    )
    enum_def = EnumDefinition(
        name="Composed",
        reachable_from=ReachabilityQuery(
            source_nodes=["TEST:0000004"],  # leaf → empty
            relationship_types=["rdfs:subClassOf"],
        ),
        permissible_values={"EXTRA": PermissibleValue(text="EXTRA", meaning="TEST:0000006")},
    )
    values = plugin.expand_enum(enum_def, use_cache=False)
    assert "EXTRA" in values and "TEST:0000006" in values


def test_greedy_minus_reachable_from_leaf_not_flagged(tmp_path):
    """A ``minus`` clause rooted at a childless leaf subtracts nothing — legitimate.

    Re-review finding: the old per-clause raise aborted this legitimate config
    (``minus`` of an empty subtree). The base reachable_from is populated, so the
    enum expands non-empty and must not raise.
    """
    plugin = DynamicEnumPlugin(
        oak_config_path=OAK_CONFIG,
        cache_labels=False,
        cache_enum_expansions=False,
        cache_dir=tmp_path / "cache",
    )
    enum_def = EnumDefinition(
        name="MinusLeaf",
        reachable_from=ReachabilityQuery(
            source_nodes=["TEST:0000001"],  # root → populated
            relationship_types=["rdfs:subClassOf"],
        ),
        minus=[
            AnonymousEnumExpression(
                reachable_from=ReachabilityQuery(
                    source_nodes=["TEST:0000004"],  # leaf → subtracts nothing
                    relationship_types=["rdfs:subClassOf"],
                )
            )
        ],
    )
    values = plugin.expand_enum(enum_def, use_cache=False)
    assert "TEST:0000002" in values  # base survived; no false EmptyReachableClosureError


def test_greedy_reachable_from_cancelled_by_minus_reports_set_arithmetic(tmp_path):
    """A non-empty closure fully removed by ``minus:`` blames set arithmetic, not the adapter.

    Re-review finding: failing loud is still right (the enum matches nothing), but
    the message must not claim the descendant closure is empty / the adapter is
    misconfigured when the real cause is the user's own ``minus:`` clause.
    """
    plugin = DynamicEnumPlugin(
        oak_config_path=OAK_CONFIG,
        cache_labels=False,
        cache_enum_expansions=False,
        cache_dir=tmp_path / "cache",
    )
    enum_def = EnumDefinition(
        name="Cancelled",
        reachable_from=ReachabilityQuery(
            source_nodes=["TEST:0000001"],  # {0002, 0003, 0004}
            relationship_types=["rdfs:subClassOf"],
        ),
        minus=[
            AnonymousEnumExpression(
                reachable_from=ReachabilityQuery(
                    source_nodes=["TEST:0000001"],  # subtract the same set → empty
                    relationship_types=["rdfs:subClassOf"],
                )
            )
        ],
    )
    with pytest.raises(EmptyReachableClosureError) as excinfo:
        plugin.expand_enum(enum_def, use_cache=False)
    # The top-level closure was non-empty, so it is attributed to set arithmetic.
    assert excinfo.value.source_closure_empty is False
    assert "minus" in str(excinfo.value)


def test_greedy_include_self_over_childless_source_still_flagged(tmp_path):
    """include_self:true must not mask an empty closure.

    Re-review finding: with include_self the reflexive source keeps `values`
    non-empty, so a childless/broken source used to slip past the guard and cache a
    one-term empty-but-complete closure. An enum whose only member is its own
    reachable_from source node is treated as empty and flagged.
    """
    plugin = DynamicEnumPlugin(
        oak_config_path=OAK_CONFIG,
        cache_labels=False,
        cache_enum_expansions=True,  # so we can confirm no complete marker is written
        cache_dir=tmp_path / "cache",
    )
    enum_def = EnumDefinition(
        name="IncludeSelfLeaf",
        reachable_from=ReachabilityQuery(
            source_nodes=["TEST:0000004"],  # childless leaf
            relationship_types=["rdfs:subClassOf"],
            include_self=True,
        ),
    )
    with pytest.raises(EmptyReachableClosureError) as excinfo:
        plugin.expand_enum(enum_def, use_cache=True)
    assert excinfo.value.source_closure_empty is True  # the closure itself was empty
    assert plugin._is_enum_cache_complete(enum_def) is False


def test_greedy_multi_source_parent_and_child_union_not_flagged(tmp_path):
    """A union of a branch root and a specific sub-branch must not be flagged.

    Re-review finding: subtracting the source nodes from the merged set false-aborts
    `source_nodes: [parent, child]` (the child is the parent's only descendant, so
    the closure equals {child} ⊆ the source set). The guard now keys on whether any
    source actually reached a term, so this legitimate union expands normally.
    """
    plugin = DynamicEnumPlugin(
        oak_config_path=OAK_CONFIG,
        cache_labels=False,
        cache_enum_expansions=False,
        cache_dir=tmp_path / "cache",
    )
    enum_def = EnumDefinition(
        name="ParentChildUnion",
        reachable_from=ReachabilityQuery(
            source_nodes=["TEST:0000002", "TEST:0000004"],  # parent + its only child
            relationship_types=["rdfs:subClassOf"],
        ),
    )
    values = plugin.expand_enum(enum_def, use_cache=False)  # must not raise
    assert "TEST:0000004" in values  # the child, reached from the parent


def test_greedy_include_self_over_populated_source_expands_normally(tmp_path):
    """Positive control: include_self:true over a populated source is not flagged.

    Guards against the emptiness check over-firing — the reflexive source plus real
    descendants must expand and cache like any healthy enum.
    """
    plugin = DynamicEnumPlugin(
        oak_config_path=OAK_CONFIG,
        cache_labels=False,
        cache_enum_expansions=True,
        cache_dir=tmp_path / "cache",
    )
    enum_def = EnumDefinition(
        name="IncludeSelfPopulated",
        reachable_from=ReachabilityQuery(
            source_nodes=["TEST:0000001"],  # root, has descendants
            relationship_types=["rdfs:subClassOf"],
            include_self=True,
        ),
    )
    values = plugin.expand_enum(enum_def, use_cache=True)  # must not raise
    assert "TEST:0000001" in values  # reflexive source kept
    assert "TEST:0000002" in values  # a real descendant
    assert plugin._is_enum_cache_complete(enum_def) is True


def test_greedy_empty_enum_names_a_stable_source_node(tmp_path):
    """The flagged source node is deterministic across runs (declared-list order)."""
    plugin = DynamicEnumPlugin(
        oak_config_path=OAK_CONFIG,
        cache_labels=False,
        cache_enum_expansions=False,
        cache_dir=tmp_path / "cache",
    )
    enum_def = EnumDefinition(
        name="TwoLeafSources",
        reachable_from=ReachabilityQuery(
            source_nodes=["TEST:0000004", "TEST:0000006"],  # two childless leaves
            relationship_types=["rdfs:subClassOf"],
        ),
    )
    with pytest.raises(EmptyReachableClosureError) as excinfo:
        plugin.expand_enum(enum_def, use_cache=False)
    # First resolvable node in the declared order, stable across runs.
    assert excinfo.value.source_node == "TEST:0000004"


def test_sample_closure_is_sorted_and_evenly_spaced(tmp_path):
    """The forward sample is deterministic: ascending-sorted, even-stride subset.

    Re-review finding: locks the round-5/6 pool→sort→stride behavior so a
    regression back to "first N in adapter stream order" is caught.
    """
    plugin = DynamicEnumPlugin(
        oak_config_path=OAK_CONFIG,
        cache_labels=False,
        cache_enum_expansions=False,
        cache_dir=tmp_path / "cache",
    )
    sample = plugin._sample_closure(
        _ManyDescAdapter(), "descendants", "MONDO:0000000", ["rdfs:subClassOf"], "MONDO"
    )
    # 20 members, sample size 8 → evenly-spaced indices {0,3,5,8,11,14,16,19}
    # spanning the sorted range (both endpoints included). Literal so a wrong stride
    # formula (e.g. the degenerate "lowest 8") would be caught.
    expected = [
        "MONDO:0000001",
        "MONDO:0000004",
        "MONDO:0000006",
        "MONDO:0000009",
        "MONDO:0000012",
        "MONDO:0000015",
        "MONDO:0000017",
        "MONDO:0000020",
    ]
    assert sample == expected
    assert sample[0] == "MONDO:0000001" and sample[-1] == "MONDO:0000020"  # spans the range
    # Deterministic across invocations (independent of set/generator ordering).
    again = plugin._sample_closure(
        _ManyDescAdapter(), "descendants", "MONDO:0000000", ["rdfs:subClassOf"], "MONDO"
    )
    assert again == sample


def test_inconsistency_probe_is_cached(tmp_path):
    """The round-trip inconsistency probe is computed once and memoized."""
    plugin = DynamicEnumPlugin(
        oak_config_path=OAK_CONFIG,
        cache_labels=False,
        cache_enum_expansions=False,
        cache_dir=tmp_path / "cache",
    )
    adapter = _InconsistentClosureAdapter()
    plugin.ontology._adapter_cache["MONDO"] = adapter  # so the source node resolves
    # A severed ancestor direction is flagged, with the offending descendant cached.
    witness = plugin._reachability_inconsistency_witness(
        adapter, "MONDO:0000001", ["rdfs:subClassOf"], False
    )
    assert witness == "MONDO:0004992"
    assert (
        plugin._source_inconsistency_cache[("MONDO:0000001", ("rdfs:subClassOf",), False)]
        == "MONDO:0004992"
    )

    # A healthy, round-trip-consistent adapter is recorded as consistent (None).
    healthy_adapter = plugin._get_adapter("TEST")
    assert (
        plugin._reachability_inconsistency_witness(
            healthy_adapter, "TEST:0000001", ["rdfs:subClassOf"], False
        )
        is None
    )
    assert (
        plugin._source_inconsistency_cache[("TEST:0000001", ("rdfs:subClassOf",), False)] is None
    )
    # A childless leaf reaches nothing → nothing to round-trip → consistent (None).
    assert (
        plugin._reachability_inconsistency_witness(
            healthy_adapter, "TEST:0000004", ["rdfs:subClassOf"], False
        )
        is None
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
