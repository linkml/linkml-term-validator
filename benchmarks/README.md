# OAK adapter benchmarks & selection guide

`reachable_from` validation resolves ontology graph closures through an OAK
adapter. **Which adapter you pick changes both correctness and performance** —
the wrong choice silently produces incorrect validation or unusable speed. This
directory documents the tradeoffs and provides a repeatable way to re-measure
them.

- **Correctness is guarded, deterministically, in CI** by
  [`tests/test_adapter_parity.py`](../tests/test_adapter_parity.py): local file
  adapters must agree on the closure; OLS must not over-return; Ubergraph is a
  merged multi-ontology graph that must be prefix-filtered.
- **Performance is measured on demand** (never asserted — it depends on caches
  and network) by [`adapter_benchmark.py`](./adapter_benchmark.py), run via
  `just benchmark`. Latest captured run:
  [`results/go_adapter_comparison.md`](./results/go_adapter_comparison.md).

## Adapter selection guide

| Adapter | Closure cost | Per-term throughput | Scope | Recommended for |
|---|---|---|---|---|
| `sqlite:obo:` | ~ms (indexed `entailed_edge`) | fast | single ontology | **Production default** |
| `pronto:` / bare `.obo` | ~3.6s cold, cached after | high | single ontology | Local file; heavy/repeated traversal |
| `simpleobo:` | ~8–10s cold | moderate | single ontology | Low RAM / fast startup; few closures |
| `ubergraph:` | ~2s warm (one big query) | low (network) | **all ontologies merged** | One-shot cross-ontology closure; **must prefix-filter** for single-ontology validation |
| `owl:` (`.owl`/`.ofn`) | ~145s, no closure cache ❌ | very low | single ontology | **Avoid** for closures (issue #57) |
| `ols:` | truncated to first page ❌ | remote | single ontology | **Avoid** for closures (issue #55); labels only |

Because `reachable_from` expansion computes one closure and then caches it, the
metric that matters is **load + one cold closure**: `pronto` ≈ 8s, `simpleobo`
≈ 12s (half the RAM), `ubergraph` ≈ 2s warm (but prefix-filter!), `owl` ≈ 160s.

### Two failure modes to know

- **`ols:` under-returns (false rejects).** OAK's OLS adapter truncates
  descendant/ancestor closures to the first page (~500), so deep terms are
  silently dropped and valid data is rejected. See issue #55 (upstream:
  `INCATools/ontology-access-kit#893`).
- **`ubergraph:` over-returns (false accepts).** Ubergraph merges many
  ontologies, so `descendants(GO:…)` also returns MRO/WBbt/PR/CL/… terms. A
  reachable_from enum meaning "GO descendants" must filter by `GO:` prefix or it
  will accept foreign-ontology terms. See issue #58.

## Running the benchmark

```bash
# full GO, all local file adapters + ubergraph (downloads go.obo / go.owl)
just benchmark
# or directly, with options:
uv run python benchmarks/adapter_benchmark.py --help

# a subset of adapters against your own ontology, writing the table to a file
uv run python benchmarks/adapter_benchmark.py \
    --adapters simpleobo,pronto --obo path/to/onto.obo --root FOO:0000001 \
    --out benchmarks/results/my_comparison.md
```

By default every backend cache (semsql downloads, HTTP cache) is redirected to a
throwaway temp dir so cold/warm numbers are comparable across runs; pass
`--no-isolate-caches` to measure against your real caches instead.
