#!/usr/bin/env python3
"""Benchmark OAK adapters for reachable_from-style is-a / part_of closures.

This is an **informational** tool, not a test: timing depends on download
caches, ``requests-cache`` state, network conditions, and the machine. To make
runs comparable rather than reproducible-to-the-millisecond:

* each adapter is benchmarked in its **own subprocess** (``--worker``), so
  ``ru_maxrss`` is that adapter's true peak RAM rather than a process-wide
  cumulative peak, and one adapter's caches can't leak into another's;
* every backend cache is redirected to a throwaway temp dir
  (cache isolation is on by default; disable with ``--no-isolate-caches``);
* both *cold* (first call) and *warm* (second call) closures are reported --
  the cold/warm gap is itself the interesting signal.

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
import dataclasses
import gc
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

try:
    import resource  # POSIX only; absent on Windows
except ImportError:  # pragma: no cover - Windows
    resource = None  # type: ignore[assignment]

# Source the predicate CURIEs from OAK's vocabulary so they can't drift from the
# constants the adapters (and tests/test_adapter_parity.py) use.
from oaklib.datamodels.vocabulary import IS_A, PART_OF  # noqa: E402

# GO cellular_component: a deep, real branch (~4k is-a descendants).
DEFAULT_ROOT = "GO:0005575"
GO_OBO_URL = "https://purl.obolibrary.org/obo/go.obo"
GO_OWL_URL = "https://purl.obolibrary.org/obo/go.owl"

PREDICATE_SETS: dict[str, list[str]] = {
    # NB: "is_a" is intentionally first; the ancestor-throughput sample is drawn
    # from the is_a descendant set (see _benchmark_adapter).
    "is_a": [IS_A],
    "is_a+part_of": [IS_A, PART_OF],
}


def _max_rss_mb() -> float | None:
    if resource is None:  # pragma: no cover - Windows
        return None
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # ru_maxrss is KiB on Linux but bytes on macOS.
    divisor = 1024 * 1024 if sys.platform == "darwin" else 1024
    return peak / divisor


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
    """Resolves ontology file paths (downloading / converting once)."""

    def __init__(self, args: argparse.Namespace, workdir: Path) -> None:
        self.args = args
        self.workdir = workdir
        self._obo: Path | None = Path(args.obo) if args.obo else None
        self._owl: Path | None = Path(args.owl) if args.owl else None

    def _download(self, url: str, dest: Path) -> Path:
        if dest.exists():
            return dest
        print(f"  downloading {url} -> {dest.name} ...", flush=True)
        # A User-Agent is required: the default urllib UA is 403'd by the OBO
        # hosts / proxy. urlopen honors HTTPS_PROXY/HTTP_PROXY from the env.
        req = urllib.request.Request(url, headers={"User-Agent": "linkml-term-validator-benchmark"})
        with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as fh:  # noqa: S310 - trusted OBO PURL
            shutil.copyfileobj(resp, fh)
        return dest

    def obo_path(self) -> str:
        if self._obo is None:
            self._obo = self._download(GO_OBO_URL, self.workdir / "go.obo")
        return str(self._obo)

    def owl_path(self) -> str:
        # The owl adapter (py-horned-owl) reads functional syntax reliably; GO is
        # only published as RDF/XML, so download it once and convert to .ofn.
        if self._owl is not None:
            return str(self._owl)
        owl = self._download(GO_OWL_URL, self.workdir / "go.owl")
        ofn = self.workdir / "go.ofn"
        if not ofn.exists():
            import pyhornedowl

            print("  converting go.owl -> go.ofn (py-horned-owl) ...", flush=True)
            onto = pyhornedowl.open_ontology(owl.read_text(), "rdf")
            ofn.write_text(onto.save_to_string("ofn"))
        self._owl = ofn
        return str(ofn)


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

    isa_descendants: set[str] = set()
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
                isa_descendants = cold
        except Exception as e:  # pragma: no cover
            res.error = f"descendants[{label}]: {type(e).__name__}: {e}"
            return res

    # ancestor-closure throughput over a bounded sample of the is_a descendants
    # (network adapters are capped so we don't hammer the public endpoint).
    cap = min(anc_sample, 25) if "remote" in spec.scope else anc_sample
    sample = sorted(isa_descendants)[:cap]
    if sample:
        try:
            _, dt = _time(
                lambda: sum(len(set(adapter.ancestors(c, predicates=[IS_A]))) for c in sample)
            )
            res.anc_per_s = len(sample) / dt if dt else None
        except Exception as e:  # pragma: no cover
            res.error = f"ancestors: {type(e).__name__}: {e}"
    # Own-process peak RSS: meaningful only because each adapter runs in its own
    # worker subprocess (see main()).
    res.rss_mb = _max_rss_mb()
    return res


def _fmt(value: float | None, suffix: str = "s") -> str:
    return f"{value:.2f}{suffix}" if value is not None else "-"


def _render_markdown(results: list[AdapterResult], root: str, meta: dict[str, str]) -> str:
    caption = "; ".join(f"{k}: {v}" for k, v in meta.items())
    lines = [
        f"# OAK adapter comparison for reachable_from closures (`descendants({root})`)",
        "",
        "> Regenerated by `just benchmark` / `benchmarks/adapter_benchmark.py`. "
        "Timing is informational (cache/network dependent) and each adapter is measured "
        "in its own subprocess; correctness is guarded by `tests/test_adapter_parity.py`.",
        "",
        f"> Run: {caption}",
        "",
        "| Adapter | Scope | Load | Cold (is_a) | Warm (is_a) | Cold (+part_of) | "
        "Ancestor closures/s | is_a count | Peak RAM |",
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
    """Point every backend cache at a throwaway dir so runs are comparable.

    All three are set unconditionally (no ``setdefault``): an inherited value
    from the environment would silently defeat the isolation this promises.
    """
    cache = workdir / "caches"
    cache.mkdir(parents=True, exist_ok=True)
    os.environ["PYSTOW_HOME"] = str(cache / "pystow")  # semsql / sqlite:obo downloads
    os.environ["XDG_CACHE_HOME"] = str(cache / "xdg")
    os.environ["OAKLIB_CACHE"] = str(cache / "oaklib")


def _run_worker(key: str, args: argparse.Namespace) -> AdapterResult:
    """Benchmark a single adapter in a subprocess and return its result."""
    cmd = [
        sys.executable, __file__, "--worker", key,
        "--root", args.root,
        "--anc-sample", str(args.anc_sample),
        "--obo", args.obo,
        "--owl", args.owl,
    ]
    if args.no_isolate_caches:
        cmd.append("--no-isolate-caches")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    for line in proc.stdout.splitlines():
        if line.startswith("__RESULT__"):
            return AdapterResult(**json.loads(line[len("__RESULT__"):]))
    return AdapterResult(
        key=key,
        scope=ADAPTER_SPECS[key].scope,
        error=f"worker failed (rc={proc.returncode}): {proc.stderr.strip()[-300:] or proc.stdout.strip()[-300:]}",
    )


def _resolve_inputs(args: argparse.Namespace, workdir: Path) -> None:
    """Download/convert ontology files once so workers reuse them."""
    ctx = BenchContext(args, workdir)
    needs_local = any(k in {"simpleobo", "pronto", "owl"} for k in args.keys)
    if needs_local and not args.obo:
        args.obo = ctx.obo_path()
    if "owl" in args.keys and not args.owl:
        args.owl = ctx.owl_path()
    args.obo = args.obo or ""
    args.owl = args.owl or ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--adapters", default="simpleobo,pronto,owl,ubergraph",
                        help="comma-separated adapter keys (default: all)")
    parser.add_argument("--root", default=DEFAULT_ROOT, help="source node CURIE")
    parser.add_argument("--obo", default="", help="local OBO file (default: download GO)")
    parser.add_argument("--owl", default="", help="local OWL/OFN file (default: download+convert GO)")
    parser.add_argument("--anc-sample", type=int, default=500, help="ancestor-closure sample size")
    parser.add_argument("--out", help="write markdown table to this path")
    parser.add_argument("--no-isolate-caches", action="store_true",
                        help="do NOT redirect backend caches to a temp dir (use real caches)")
    parser.add_argument("--worker", help=argparse.SUPPRESS)  # internal: benchmark one adapter, emit JSON
    args = parser.parse_args(argv)

    # -- worker mode: benchmark exactly one adapter, print JSON, exit ---------
    if args.worker:
        with tempfile.TemporaryDirectory(prefix="oak-bench-w-") as tmp:
            if not args.no_isolate_caches:
                _isolate_caches(Path(tmp))
            workdir = Path(args.obo).parent if args.obo else Path(tmp)
            ctx = BenchContext(args, workdir)
            result = _benchmark_adapter(ADAPTER_SPECS[args.worker], ctx, args.root, args.anc_sample)
        print("__RESULT__" + json.dumps(dataclasses.asdict(result)), flush=True)
        return 0

    # -- parent mode: resolve inputs once, fan out one subprocess per adapter -
    keys = [k.strip() for k in args.adapters.split(",") if k.strip()]
    unknown = [k for k in keys if k not in ADAPTER_SPECS]
    if unknown:
        parser.error(f"unknown adapters: {unknown}; choose from {sorted(ADAPTER_SPECS)}")
    args.keys = keys

    with tempfile.TemporaryDirectory(prefix="oak-bench-") as tmp:
        _resolve_inputs(args, Path(args.obo).parent if args.obo else Path(tmp))

        try:
            from importlib.metadata import version

            meta = {
                "root": args.root,
                "oaklib": version("oaklib"),
                "py-horned-owl": version("py-horned-owl"),
                "caches": "system" if args.no_isolate_caches else "isolated, per-adapter subprocess",
            }
        except Exception:
            meta = {"root": args.root}

        results: list[AdapterResult] = []
        for key in keys:
            print(f"=== {key} ===", flush=True)
            r = _run_worker(key, args)
            results.append(r)
            print(f"    load={_fmt(r.load_s)} cold_isa={_fmt(r.cold_s.get('is_a'))} "
                  f"count={r.count.get('is_a', '-')} rss={_fmt(r.rss_mb, ' MB')} err={r.error}", flush=True)

    table = _render_markdown(results, args.root, meta)
    print("\n" + table)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(table)
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
