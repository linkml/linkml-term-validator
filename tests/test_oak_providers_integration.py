"""Provider compatibility tests for non-sqlite OAK adapters."""

import json
from importlib.metadata import version
from pathlib import Path

import pytest
from linkml.validator import Validator  # type: ignore[import-untyped]
from linkml.validator.loaders import YamlLoader  # type: ignore[import-untyped]
from packaging.version import Version

from linkml_term_validator.plugins import DynamicEnumPlugin


def _write_dynamic_enum_case(
    tmp_path: Path,
    *,
    prefix: str,
    source_ontology: str,
    source_node: str,
    valid_term: str,
) -> tuple[Path, Path]:
    schema_path = tmp_path / "schema.yaml"
    data_path = tmp_path / "data.yaml"
    source_ontology_yaml = json.dumps(source_ontology)

    schema_path.write_text(f"""
id: https://example.org/provider-test
name: provider-test

prefixes:
  {prefix}: http://purl.obolibrary.org/obo/{prefix}_
  linkml: https://w3id.org/linkml/

default_prefix: provider-test
default_range: string

classes:
  Sample:
    attributes:
      id:
        identifier: true
      term:
        range: ProviderEnum

enums:
  ProviderEnum:
    reachable_from:
      source_ontology: {source_ontology_yaml}
      source_nodes:
        - {source_node}
      relationship_types:
        - rdfs:subClassOf
""")
    data_path.write_text(f"""
- id: sample1
  term: {valid_term}
""")
    return schema_path, data_path


def _validate_provider_case(schema_path: Path, data_path: Path, plugin: DynamicEnumPlugin) -> None:
    validator = Validator(schema=str(schema_path), validation_plugins=[plugin])
    report = validator.validate_source(YamlLoader(data_path), target_class="Sample")
    assert len(report.results) == 0, report.results


@pytest.mark.integration
def test_dynamic_enum_with_ols_provider_progressive(tmp_path):
    """OLS works for progressive reachable_from validation."""
    schema_path, data_path = _write_dynamic_enum_case(
        tmp_path,
        prefix="GO",
        source_ontology="ols:go",
        source_node="GO:0008150",
        valid_term="GO:0009987",
    )
    config_path = tmp_path / "oak_config.yaml"
    config_path.write_text('ontology_adapters:\n  GO: "ols:"\n')

    plugin = DynamicEnumPlugin(
        oak_config_path=config_path,
        cache_labels=False,
        cache_enum_expansions=False,
    )

    _validate_provider_case(schema_path, data_path, plugin)


@pytest.mark.integration
def test_ols_mondo_ancestor_reachability_regression(tmp_path):
    """OLS MONDO ancestor reachability resolves the disease root by CURIE.

    Regression guard for dismech#7012 / EBISPOT/ols4#1334: OLS4 briefly returned
    the term at IRI .../MONDO_0000001 under the wrong obo_id 'AFO_O:0000001' (a
    cross-ontology "disease" merge), so ``MONDO:0000001`` was unmatchable by CURIE
    and every MONDO term silently failed ancestor-based reachability. The bug was
    fixed upstream (EBISPOT/ols4#1335).

    This validates ``MONDO:0975866`` (TCF3-HLF B-lymphoblastic leukemia, one of
    the exact subtypes that failed in dismech#7012) as reachable from
    ``MONDO:0000001`` through the live ``ols:mondo`` adapter. It confirms two
    things at once against the real service: the closure resolves correctly, and
    the round-trip integrity guard does not false-positive on a healthy OLS graph
    (which would surface as InconsistentReachabilityError rather than a clean
    validation). Should OLS ever regress the identifier merge, this test fails
    loudly instead of silently mislabeling MONDO terms.
    """
    schema_path, data_path = _write_dynamic_enum_case(
        tmp_path,
        prefix="MONDO",
        source_ontology="ols:mondo",
        source_node="MONDO:0000001",
        valid_term="MONDO:0975866",
    )
    config_path = tmp_path / "oak_config.yaml"
    config_path.write_text('ontology_adapters:\n  MONDO: "ols:"\n')

    plugin = DynamicEnumPlugin(
        oak_config_path=config_path,
        cache_labels=False,
        cache_enum_expansions=False,
    )

    _validate_provider_case(schema_path, data_path, plugin)

    # Positive assertion that the integrity guard is actually wired against the
    # live adapter (not silently disabled): the reverse direction must resolve the
    # disease root by CURIE from a real descendant. Without this, the validation
    # above would pass equally if the guard were a no-op. Uses a small ancestor
    # closure (fast) rather than crawling the ~31k-descendant root.
    adapter = plugin._get_adapter("MONDO")
    assert (
        plugin._reverse_reaches(
            adapter, "ancestors", "MONDO:0004992", ["rdfs:subClassOf"], "MONDO:0000001"
        )
        == plugin._REVERSE_FOUND
    )
    # And the *forward* sample — the path that actually makes the guard fire on a
    # live ols: adapter — returns real same-prefix members (native or via the
    # bounded fallback), so the probe is not silently disabled against the real API.
    assert plugin._sample_closure(
        adapter, "descendants", "MONDO:0000001", ["rdfs:subClassOf"], "MONDO"
    )


@pytest.mark.integration
def test_dynamic_enum_with_ubergraph_provider_progressive(tmp_path):
    """Ubergraph works for progressive reachable_from validation."""
    schema_path, data_path = _write_dynamic_enum_case(
        tmp_path,
        prefix="GO",
        source_ontology="ubergraph:",
        source_node="GO:0008150",
        valid_term="GO:0009987",
    )
    config_path = tmp_path / "oak_config.yaml"
    config_path.write_text('ontology_adapters:\n  GO: "ubergraph:"\n')

    plugin = DynamicEnumPlugin(
        oak_config_path=config_path,
        cache_labels=False,
        cache_enum_expansions=False,
    )

    _validate_provider_case(schema_path, data_path, plugin)


def _require_owl_graph_support() -> None:
    if Version(version("oaklib")) < Version("0.7.0rc7"):
        pytest.skip("owl graph traversal requires oaklib >=0.7.0rc7")
    try:
        from oaklib import get_adapter

        adapter = get_adapter("owl:tests/data/test_ontology.ofn")
    except Exception as e:
        pytest.skip(f"owl adapter is unavailable: {e}")
    if not all(hasattr(adapter, method) for method in ("label", "ancestors", "descendants")):
        pytest.skip("owl adapter does not expose label, ancestors, and descendants")


def test_dynamic_enum_with_owl_provider(tmp_path):
    """Functional OWL files work with OAK's owl adapter on oaklib >=0.7.0rc7."""
    _require_owl_graph_support()
    # OAK resolves TEST using the OWL file's prefix map; the schema prefix map
    # generated by this helper is not used for adapter lookups.
    schema_path, data_path = _write_dynamic_enum_case(
        tmp_path,
        prefix="TEST",
        source_ontology="owl:tests/data/test_ontology.ofn",
        source_node="TEST:0000001",
        valid_term="TEST:0000004",
    )

    plugin = DynamicEnumPlugin(
        oak_adapter_string="owl:tests/data/test_ontology.ofn",
        cache_labels=False,
        cache_enum_expansions=False,
    )

    _validate_provider_case(schema_path, data_path, plugin)
