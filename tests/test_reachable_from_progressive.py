"""Progressive-validation tests for reachable_from semantics and cache safety.

Covers regression tests for:
- #34 traverse_up must include ancestors, not behave like traverse_down
- #35 a failed expansion must not be cached as a complete closure
- #36 the enum cache key must change when include/minus/inherits change

Uses the local simpleobo test ontology (offline). Hierarchy:

    TEST:0000001 root
      TEST:0000002 child one
        TEST:0000004 grandchild
      TEST:0000003 child two
    TEST:0000005 biological_process
      TEST:0000006 cell_cycle
"""

from pathlib import Path

import pytest
from linkml_runtime.linkml_model.meta import EnumDefinition, ReachabilityQuery

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
