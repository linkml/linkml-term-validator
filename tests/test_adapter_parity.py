"""Behavioral parity + scope invariants for OAK adapters used in reachable_from.

These tests guard *behavior* that is independent of caches, network latency, and
timing, so they are safe to run in CI:

* **Local parity** (offline, default CI): the local file adapters ``simpleobo``,
  ``pronto`` and ``owl`` must agree on the ``is_a`` and ``is_a + part_of``
  closures computed over the *same* synthetic ontology. If they diverge, term
  validation would accept/reject different terms depending only on which backend
  the user happened to configure.
* **Remote scope** (``@integration``, network): OLS under-returns (its closure is
  a *subset* of the ground truth -- the truncation behind issue #55), and
  Ubergraph is a *merged multi-ontology* graph whose ``descendants`` span every
  loaded ontology, so it must be prefix-filtered for single-ontology validation
  (issue #58).

Performance is deliberately **not** asserted here -- it depends on download
caches, ``requests-cache`` state, and network conditions. The timing comparison
lives in ``benchmarks/`` and is regenerated on demand via ``just benchmark``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from oaklib import get_adapter
from oaklib.datamodels.vocabulary import IS_A, PART_OF

# --------------------------------------------------------------------------- #
# Synthetic ontology: a deterministic ternary is_a tree plus a couple of
# part_of cross-links, so that ``is_a + part_of`` closures are strictly larger
# than ``is_a``-only closures for at least one source node.
# --------------------------------------------------------------------------- #

# Single-source the part_of CURIE from OAK's vocabulary so the synthetic
# ontology and the query predicates can't drift apart.
PART_OF_CURIE = PART_OF
_N = 40


def _cid(i: int) -> str:
    return f"TEST:{i:07d}"


# child -> is_a parent (ternary tree rooted at node 0)
_ISA_PARENT: dict[int, int] = {i: (i - 1) // 3 for i in range(1, _N)}
# child -> part_of parent (deliberately into a different is_a branch)
_PARTOF_PARENT: dict[int, int] = {_N - 1: 2, _N - 2: 5}


def _write_obo(path: Path) -> None:
    lines = ["format-version: 1.2", "ontology: synthetic", ""]
    # pronto raises KeyError on an undeclared relationship type, so the part_of
    # typedef must be declared even though simpleobo/owl tolerate its absence.
    lines += ["[Typedef]", f"id: {PART_OF_CURIE}", "name: part of", ""]
    for i in range(_N):
        lines += ["[Term]", f"id: {_cid(i)}", f"name: term {i}"]
        if i in _ISA_PARENT:
            lines.append(f"is_a: {_cid(_ISA_PARENT[i])} ! term {_ISA_PARENT[i]}")
        if i in _PARTOF_PARENT:
            parent = _PARTOF_PARENT[i]
            lines.append(f"relationship: {PART_OF_CURIE} {_cid(parent)} ! term {parent}")
        lines.append("")
    path.write_text("\n".join(lines))


def _write_ofn(path: Path) -> None:
    lines = [
        "Prefix(:=<http://example.org/synthetic/>)",
        "Prefix(TEST:=<http://purl.obolibrary.org/obo/TEST_>)",
        "Prefix(BFO:=<http://purl.obolibrary.org/obo/BFO_>)",
        "Prefix(rdfs:=<http://www.w3.org/2000/01/rdf-schema#>)",
        "Ontology(<http://example.org/synthetic>",
        f"  Declaration(ObjectProperty({PART_OF_CURIE}))",
    ]
    for i in range(_N):
        lines.append(f"  Declaration(Class({_cid(i)}))")
        lines.append(f'  AnnotationAssertion(rdfs:label {_cid(i)} "term {i}")')
        if i in _ISA_PARENT:
            lines.append(f"  SubClassOf({_cid(i)} {_cid(_ISA_PARENT[i])})")
        if i in _PARTOF_PARENT:
            parent = _PARTOF_PARENT[i]
            lines.append(
                f"  SubClassOf({_cid(i)} ObjectSomeValuesFrom({PART_OF_CURIE} {_cid(parent)}))"
            )
    lines.append(")")
    path.write_text("\n".join(lines))


def _closure(adapter: Any, node: str, predicates: list[str], *, up: bool = False) -> set[str]:
    method = adapter.ancestors if up else adapter.descendants
    return set(method(node, predicates=predicates))


@pytest.fixture(scope="session")
def synthetic_paths(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    d = tmp_path_factory.mktemp("synthetic_ontology")
    obo, ofn = d / "synthetic.obo", d / "synthetic.ofn"
    _write_obo(obo)
    _write_ofn(ofn)
    return obo, ofn


@pytest.fixture(scope="session")
def local_adapters(synthetic_paths: tuple[Path, Path]) -> dict[str, Any]:
    """Parse the synthetic ontology once and expose the local file adapters."""
    obo, ofn = synthetic_paths
    adapters: dict[str, Any] = {
        "simpleobo": get_adapter(f"simpleobo:{obo}"),
        "pronto": get_adapter(f"pronto:{obo}"),
    }
    # The owl adapter needs a recent oaklib + py-horned-owl; skip only that one
    # if unavailable rather than failing the whole parity check.
    try:
        owl = get_adapter(f"owl:{ofn}")
        if all(hasattr(owl, m) for m in ("descendants", "ancestors")):
            adapters["owl"] = owl
    except Exception:  # pragma: no cover - environment dependent
        pass
    return adapters


# --------------------------------------------------------------------------- #
# Local parity (offline, runs in default CI)
# --------------------------------------------------------------------------- #

# Roots chosen so the closure is a non-trivial mix of branches, not the whole tree.
_SOURCE_NODES = [_cid(0), _cid(1), _cid(2)]
_PREDICATE_SETS = {
    "is_a": [IS_A],
    "is_a+part_of": [IS_A, PART_OF],
}


@pytest.mark.parametrize("source_node", _SOURCE_NODES)
@pytest.mark.parametrize("pred_label", list(_PREDICATE_SETS))
@pytest.mark.parametrize("direction", ["descendants", "ancestors"])
def test_local_adapters_agree(
    local_adapters: dict[str, Any],
    source_node: str,
    pred_label: str,
    direction: str,
) -> None:
    """simpleobo / pronto / owl must return identical closures for the same query."""
    adapters = local_adapters
    if len(adapters) < 2:
        pytest.skip("need >=2 local adapters to check parity")
    predicates = _PREDICATE_SETS[pred_label]
    up = direction == "ancestors"
    results = {name: _closure(ad, source_node, predicates, up=up) for name, ad in adapters.items()}

    reference_name, reference = next(iter(results.items()))
    for name, value in results.items():
        assert value == reference, (
            f"{direction}({source_node}, {pred_label}): "
            f"{name} disagrees with {reference_name}; "
            f"only in {name}={sorted(value - reference)}, "
            f"only in {reference_name}={sorted(reference - value)}"
        )


def test_part_of_broadens_closure(local_adapters: dict[str, Any]) -> None:
    """Sanity check that part_of is actually exercised (not a no-op predicate).

    Node 2 has a part_of child (node 39) that is not one of its is_a descendants,
    so ``is_a + part_of`` must be a strict superset of ``is_a`` here -- and every
    adapter must agree on the broadened set.
    """
    adapters = local_adapters
    node = _cid(2)
    for name, ad in adapters.items():
        isa = _closure(ad, node, [IS_A])
        both = _closure(ad, node, [IS_A, PART_OF])
        assert isa < both, f"{name}: expected part_of to broaden the closure of {node}"
        assert _cid(_N - 1) in both, f"{name}: part_of child missing from closure of {node}"


# --------------------------------------------------------------------------- #
# Remote scope invariants (network; opt-in via -m integration)
# --------------------------------------------------------------------------- #

# cellular_component: deep enough that OLS pagination truncation is observable.
_GO_ROOT = "GO:0005575"


def _go_scope(curies: set[str]) -> set[str]:
    return {c for c in curies if c.startswith("GO:")}


@pytest.mark.integration
def test_ols_descendants_are_a_subset_of_ground_truth() -> None:
    """OLS never returns *more* than the true closure (issue #55).

    Truncation makes OLS a strict subset in practice, but we assert only the
    robust ``subset`` invariant so the test keeps passing once OAK/OLS is fixed.
    Exact counts are reported by the benchmark, not asserted here.
    """
    ols = get_adapter("ols:go")
    ubergraph = get_adapter("ubergraph:")
    # Both services also surface imported cross-ontology terms (e.g. CL), so
    # compare like-for-like in GO scope -- the closure OLS is supposed to serve.
    ols_desc = _go_scope(_closure(ols, _GO_ROOT, [IS_A]))
    truth = _go_scope(_closure(ubergraph, _GO_ROOT, [IS_A]))
    assert ols_desc, "expected OLS to return at least one GO descendant"
    assert ols_desc <= truth, f"OLS returned terms outside ground truth: {sorted(ols_desc - truth)[:10]}"


@pytest.mark.integration
def test_ubergraph_is_a_merged_multi_ontology_graph() -> None:
    """Ubergraph descendants of a GO term span multiple ontologies (issue #58).

    Consequence for validation: a reachable_from enum meaning "GO descendants"
    must prefix-filter, or it will accept foreign-ontology terms (false accepts).
    """
    ubergraph = get_adapter("ubergraph:")
    raw = _closure(ubergraph, _GO_ROOT, [IS_A])
    prefixes = {c.split(":")[0] for c in raw}
    assert prefixes - {"GO"}, "expected cross-ontology descendants, got GO-only"
    assert _go_scope(raw) < raw, "expected the GO-scoped subset to be strictly smaller than the raw set"
