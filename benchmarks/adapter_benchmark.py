#!/usr/bin/env python3
"""Benchmark OAK adapters for reachable_from-style is-a / part_of closures.

This is an **informational** tool, not a test: timing depends on download
caches, ``requests-cache`` state, network conditions, and the machine. To make
runs comparable rather than reproducible-to-the-millisecond, every backend cache
is redirected to a throwaway temp dir (``--isolate-caches``, on by default) and
both *cold* (first call) and *warm* (second call) closures are reported -- the
cold/warm gap is itself the interesting signal.

It regenerates the adapter comparison table published in
``linkml/linkml-term-validator#58``.

Examples::

    # full GO, all local file adapters + ubergraph (downloads ontologies)
    uv run python benchmarks/adapter_benchmark.py

    # a subset of adapters against your own ontology files
    uv run python benchmarks/adapter_benchmark.py \
        --adapters simpleobo,pronto --obo path/to/onto.obo --root FOO:0000001

Correctness (that the adapters *agree* on the closure) is guarded separately and
deterministically by ``tests/test_adapter_parity.py``; this script only measures
cost.
"""

from __future__ import annotations

import argparse
import gc
import os
import resource
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# GO cellular_component: a deep, real branch (~4k is-a descendants).
DEFAULT_ROOT = "GO:0005575"
GO_OBO_URL = "http://purl.obolibrary.org/obo/go.obo"
GO_OWL_URL = "http://purl.obolibrary.org/obo/go.owl"

IS_A = "rdfs:subClassOf"
PART_OF = "BFO:0000050"
PREDICATE_SETS: dict[str, list[str]] = {
    "is_a": [IS_A],
    "is_a+part_of": [IS_A, PART_OF],
}


def _max_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def _time(fn: Callable[[], Any]) -> tuple[Any, float]:
    gc.collect()
    start = time.perf_counter()
    out = fn()
    return out, time.perf_counter() - start


@dataclass
class AdapterResult:
    key: str
    scope: str
    load_s: float | None = None
    cold_s: dict[str, float] = field(default_factory=dict)
    warm_s: dict[str, float] = field(default_factory=dict)
    count: dict[str, int] = field(default_factory=dict)
    anc_per_s: float | None = None
    rss_mb: float | None = None
    error: str | None = None


@dataclass
class AdapterSpec:
    key: str
    scope: str  # "single" | "merged (remote)"
    build_shorthand: Callable[["BenchContext"], str]


class BenchContext:
    """Holds resolved ontology file paths / download cache for the run."""

    def __init__(self, args: argparse.Namespace, workdir: Path) -> None:
        self.args = args
        self.workdir = workdir
        self._obo: Path | None = Path(args.obo) if args.obo else None
        self._owl: Path | None = Path(args.owl) if args.owl else None

    def _download(self, url: str, dest: Path) -> Path:
        if dest.exists():
            return dest
        print(f"  downloading {url} -> {dest.name} ...", flush=True)
        urllib.request.urlretrieve(url, dest)  # noqa: S310 - trusted OBO PURL
        return dest

    def obo_path(self) -> str:
        if self._obo is None:
            self._obo = self._download(GO_OBO_URL, self.workdir / "go.obo")
        return str(self._obo)

    def owl_path(self) -> str:
        if self._owl is None:
            self._owl = self._download(GO_OWL_URL, self.workdir / "go.owl")
        return str(self._owl)


ADAPTER_SPECS: dict[str, AdapterSpec] = {
    "simpleobo": AdapterSpec("simpleobo", "single", lambda c: f"simpleobo:{c.obo_path()}"),
    "pronto": AdapterSpec("pronto", "single", lambda c: f"pronto:{c.obo_path()}"),
    "owl": AdapterSpec("owl", "single", lambda c: f"owl:{c.owl_path()}"),
    "ubergraph": AdapterSpec("ubergraph", "merged (remote)", lambda c: "ubergraph:"),
}


def _benchmark_adapter(spec: AdapterSpec, ctx: BenchContext, root: str, anc_sample: int) -> AdapterResult:
    from oaklib import get_adapter

    res = AdapterResult(key=spec.key, scope=spec.scope)
    try:
        shorthand = spec.build_shorthand(ctx)
        adapter, res.load_s = _time(lambda: get_adapter(shorthand))
    except Exception as e:  # pragma: no cover - environment/network dependent
        res.error = f"load: {type(e).__name__}: {e}"
        return res

    descendants: set[str] = set()
    for label, predicates in PREDICATE_SETS.items():
        try:
            cold, res.cold_s[label] = _time(
                lambda predicates=predicates: set(adapter.descendants(root, predicates=predicates))
            )
            _, res.warm_s[label] = _time(
                lambda predicates=predicates: set(adapter.descendants(root, predicates=predicates))
            )
            res.count[label] = len(cold)
            if label == "is_a":
                descendants = cold
        except Exception as e:  # pragma: no cover
            res.error = f"descendants[{label}]: {type(e).__name__}: {e}"
            return res

    # ancestor-closure throughput over a bounded sample (network adapters are
    # kept to a small sample so we don't hammer the public endpoint)
    sample = sorted(descendants)[: (min(anc_sample, 25) if "remote" in spec.scope else anc_sample)]
    if sample:
        try:
            _, dt = _time(
                lambda: sum(len(set(adapter.ancestors(c, predicates=[IS_A]))) for c in sample)
            )
            res.anc_per_s = len(sample) / dt if dt else None
        except Exception as e:  # pragma: no cover
            res.error = f"ancestors: {type(e).__name__}: {e}"
    res.rss_mb = _max_rss_mb()
    return res


def _fmt(value: float | None, suffix: str = "s") -> str:
    return f"{value:.2f}{suffix}" if value is not None else "-"


def _render_markdown(results: list[AdapterResult], root: str, meta: dict[str, str]) -> str:
    lines = [
        f"# OAK adapter comparison for reachable_from closures (`descendants({root})`)",
        "",
        "> Regenerated by `just benchmark` / `benchmarks/adapter_benchmark.py`. "
        "Timing is informational (cache/network dependent); correctness is guarded by "
        "`tests/test_adapter_parity.py`.",
        "",
        "| " + " | ".join(f"{k}: {v}" for k, v in meta.items()) + " |",
        "",
        "| Adapter | Scope | Load | Cold (is_a) | Warm (is_a) | Cold (+part_of) | "
        "Ancestor closures/s | is_a count | RAM |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        if r.error:
            lines.append(f"| `{r.key}` | {r.scope} | ❌ {r.error} | | | | | | |")
            continue
        lines.append(
            "| `{key}` | {scope} | {load} | {cold_isa} | {warm_isa} | {cold_po} | "
            "{anc} | {count} | {rss} |".format(
                key=r.key,
                scope=r.scope,
                load=_fmt(r.load_s),
                cold_isa=_fmt(r.cold_s.get("is_a")),
                warm_isa=_fmt(r.warm_s.get("is_a")),
                cold_po=_fmt(r.cold_s.get("is_a+part_of")),
                anc=(f"{r.anc_per_s:.0f}/s" if r.anc_per_s is not None else "-"),
                count=r.count.get("is_a", "-"),
                rss=_fmt(r.rss_mb, " MB"),
            )
        )
    lines.append("")
    return "\n".join(lines)


def _isolate_caches(workdir: Path) -> None:
    """Point every backend cache at a throwaway dir so runs are comparable."""
    cache = workdir / "caches"
    cache.mkdir(parents=True, exist_ok=True)
    os.environ["PYSTOW_HOME"] = str(cache / "pystow")  # semsql / sqlite:obo downloads
    os.environ["XDG_CACHE_HOME"] = str(cache / "xdg")
    os.environ.setdefault("OAKLIB_CACHE", str(cache / "oaklib"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--adapters",
        default="simpleobo,pronto,owl,ubergraph",
        help="comma-separated adapter keys (default: all)",
    )
    parser.add_argument("--root", default=DEFAULT_ROOT, help="source node CURIE")
    parser.add_argument("--obo", help="local OBO file (default: download GO)")
    parser.add_argument("--owl", help="local OWL/OFN file (default: download GO)")
    parser.add_argument("--anc-sample", type=int, default=500, help="ancestor-closure sample size")
    parser.add_argument("--out", help="write markdown table to this path")
    parser.add_argument(
        "--no-isolate-caches",
        action="store_true",
        help="do NOT redirect backend caches to a temp dir (use real caches)",
    )
    args = parser.parse_args(argv)

    keys = [k.strip() for k in args.adapters.split(",") if k.strip()]
    unknown = [k for k in keys if k not in ADAPTER_SPECS]
    if unknown:
        parser.error(f"unknown adapters: {unknown}; choose from {sorted(ADAPTER_SPECS)}")

    with tempfile.TemporaryDirectory(prefix="oak-bench-") as tmp:
        workdir = Path(args.obo).parent if args.obo else Path(tmp)
        if not args.no_isolate_caches:
            _isolate_caches(Path(tmp))
        ctx = BenchContext(args, workdir)

        try:
            from importlib.metadata import version

            meta = {
                "root": args.root,
                "oaklib": version("oaklib"),
                "py-horned-owl": version("py-horned-owl"),
                "caches": "isolated" if not args.no_isolate_caches else "system",
            }
        except Exception:
            meta = {"root": args.root}

        results: list[AdapterResult] = []
        for key in keys:
            print(f"=== {key} ===", flush=True)
            r = _benchmark_adapter(ADAPTER_SPECS[key], ctx, args.root, args.anc_sample)
            results.append(r)
            print(f"    load={_fmt(r.load_s)} cold_isa={_fmt(r.cold_s.get('is_a'))} "
                  f"count={r.count.get('is_a', '-')} err={r.error}", flush=True)

    table = _render_markdown(results, args.root, meta)
    print("\n" + table)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(table)
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
