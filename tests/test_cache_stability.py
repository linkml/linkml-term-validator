"""Tests for cache stability - verifying no spurious diffs."""

import csv
import multiprocessing as mp
from pathlib import Path
import time

import pytest

from linkml_term_validator.models import ValidationConfig
from linkml_term_validator.plugins import PermissibleValueMeaningPlugin
from linkml_term_validator.validator import EnumValidator

CONCURRENT_WRITE_DELAY = 0.2


class SlowPermissibleValueMeaningPlugin(PermissibleValueMeaningPlugin):
    """Delay cache reloads to widen the concurrent write window in tests."""

    def _load_cache_with_timestamps(self, prefix: str) -> dict[str, dict[str, str]]:
        cached = super()._load_cache_with_timestamps(prefix)
        time.sleep(CONCURRENT_WRITE_DELAY)
        return cached


class SlowEnumValidator(EnumValidator):
    """Delay cache reloads to widen the concurrent write window in tests."""

    def _load_cache_with_timestamps(self, prefix: str) -> dict[str, dict[str, str]]:
        cached = super()._load_cache_with_timestamps(prefix)
        time.sleep(CONCURRENT_WRITE_DELAY)
        return cached


def _plugin_cache_worker(cache_dir: str, curie: str, barrier) -> None:
    plugin = SlowPermissibleValueMeaningPlugin(cache_labels=True, cache_dir=Path(cache_dir))
    barrier.wait()
    plugin._save_to_cache("GO", curie, f"label-{curie}")


def _validator_cache_worker(cache_dir: str, curie: str, barrier) -> None:
    config = ValidationConfig(cache_labels=True, cache_dir=Path(cache_dir))
    validator = SlowEnumValidator(config)
    barrier.wait()
    validator._save_to_cache("GO", curie, f"label-{curie}")


def _run_concurrent_cache_writers(cache_dir: Path, worker) -> Path:
    ctx = mp.get_context("spawn")
    curies = [f"GO:{i:07d}" for i in range(1, 7)]
    barrier = ctx.Barrier(len(curies))
    processes = [
        ctx.Process(target=worker, args=(str(cache_dir), curie, barrier))
        for curie in curies
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)

    for process in processes:
        assert process.exitcode == 0, f"worker {process.pid} failed with exit code {process.exitcode}"

    cache_file = cache_dir / "go" / "terms.csv"
    assert cache_file.exists()
    rows = _read_terms_csv(cache_file)
    assert [row["curie"] for row in rows] == sorted(curies)
    assert b"\x00" not in cache_file.read_bytes()
    return cache_file


@pytest.fixture
def cache_dir(tmp_path):
    """Create a temporary cache directory."""
    d = tmp_path / "cache"
    d.mkdir()
    return d


def _write_terms_csv(cache_file: Path, entries: list[dict[str, str]]) -> None:
    """Helper to write a terms.csv file."""
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["curie", "label", "retrieved_at"], lineterminator="\n")
        writer.writeheader()
        for entry in entries:
            writer.writerow(entry)


def _read_terms_csv(cache_file: Path) -> list[dict[str, str]]:
    """Helper to read a terms.csv file."""
    rows = []
    with open(cache_file) as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(dict(row))
    return rows


class TestBasePluginCacheStability:
    """Tests for BaseOntologyPlugin cache stability."""

    def test_save_preserves_existing_timestamps(self, cache_dir):
        """Adding a new term should NOT change timestamps of existing terms."""
        prefix_dir = cache_dir / "go"
        prefix_dir.mkdir()
        cache_file = prefix_dir / "terms.csv"

        # Pre-populate cache with known timestamps
        original_ts = "2025-01-15T10:00:00.000000"
        _write_terms_csv(cache_file, [
            {"curie": "GO:0008150", "label": "biological_process", "retrieved_at": original_ts},
            {"curie": "GO:0003674", "label": "molecular_function", "retrieved_at": original_ts},
        ])

        # Create plugin and save a new entry
        plugin = PermissibleValueMeaningPlugin(cache_labels=True, cache_dir=cache_dir)
        plugin._save_to_cache("GO", "GO:0005575", "cellular_component")

        # Read back and verify
        rows = _read_terms_csv(cache_file)
        by_curie = {r["curie"]: r for r in rows}

        # Existing entries should keep their original timestamps
        assert by_curie["GO:0008150"]["retrieved_at"] == original_ts
        assert by_curie["GO:0003674"]["retrieved_at"] == original_ts
        # New entry should have a fresh timestamp
        assert by_curie["GO:0005575"]["retrieved_at"] != original_ts
        assert by_curie["GO:0005575"]["label"] == "cellular_component"

    def test_save_sorts_by_curie(self, cache_dir):
        """Cache entries should be sorted by CURIE for deterministic output."""
        prefix_dir = cache_dir / "go"
        prefix_dir.mkdir()
        cache_file = prefix_dir / "terms.csv"

        # Pre-populate in unsorted order
        _write_terms_csv(cache_file, [
            {"curie": "GO:0005575", "label": "cellular_component", "retrieved_at": "2025-01-01"},
            {"curie": "GO:0003674", "label": "molecular_function", "retrieved_at": "2025-01-01"},
            {"curie": "GO:0008150", "label": "biological_process", "retrieved_at": "2025-01-01"},
        ])

        # Save triggers a rewrite
        plugin = PermissibleValueMeaningPlugin(cache_labels=True, cache_dir=cache_dir)
        plugin._save_to_cache("GO", "GO:0009987", "cellular process")

        rows = _read_terms_csv(cache_file)
        curies = [r["curie"] for r in rows]
        assert curies == sorted(curies)

    def test_save_same_label_no_timestamp_change(self, cache_dir):
        """Re-saving the same curie+label should not change the timestamp."""
        prefix_dir = cache_dir / "go"
        prefix_dir.mkdir()
        cache_file = prefix_dir / "terms.csv"

        original_ts = "2025-01-15T10:00:00.000000"
        _write_terms_csv(cache_file, [
            {"curie": "GO:0008150", "label": "biological_process", "retrieved_at": original_ts},
        ])

        plugin = PermissibleValueMeaningPlugin(cache_labels=True, cache_dir=cache_dir)
        plugin._save_to_cache("GO", "GO:0008150", "biological_process")

        rows = _read_terms_csv(cache_file)
        assert rows[0]["retrieved_at"] == original_ts

    def test_save_changed_label_updates_timestamp(self, cache_dir):
        """Saving with a different label should update the timestamp."""
        prefix_dir = cache_dir / "go"
        prefix_dir.mkdir()
        cache_file = prefix_dir / "terms.csv"

        original_ts = "2025-01-15T10:00:00.000000"
        _write_terms_csv(cache_file, [
            {"curie": "GO:0008150", "label": "biological_process", "retrieved_at": original_ts},
        ])

        plugin = PermissibleValueMeaningPlugin(cache_labels=True, cache_dir=cache_dir)
        plugin._save_to_cache("GO", "GO:0008150", "biological process")

        rows = _read_terms_csv(cache_file)
        assert rows[0]["label"] == "biological process"
        assert rows[0]["retrieved_at"] != original_ts

    def test_idempotent_save(self, cache_dir):
        """Multiple saves of the same data should produce identical files."""
        prefix_dir = cache_dir / "go"
        prefix_dir.mkdir()
        cache_file = prefix_dir / "terms.csv"

        original_ts = "2025-01-15T10:00:00.000000"
        entries = [
            {"curie": "GO:0003674", "label": "molecular_function", "retrieved_at": original_ts},
            {"curie": "GO:0005575", "label": "cellular_component", "retrieved_at": original_ts},
            {"curie": "GO:0008150", "label": "biological_process", "retrieved_at": original_ts},
        ]
        _write_terms_csv(cache_file, entries)

        content_before = cache_file.read_text()

        # Save an existing entry with the same label
        plugin = PermissibleValueMeaningPlugin(cache_labels=True, cache_dir=cache_dir)
        plugin._save_to_cache("GO", "GO:0008150", "biological_process")

        content_after = cache_file.read_text()
        assert content_before == content_after

    def test_concurrent_saves_preserve_all_entries(self, cache_dir):
        """Concurrent plugin writers should serialize cache updates safely."""
        _run_concurrent_cache_writers(cache_dir, _plugin_cache_worker)


class TestValidatorCacheStability:
    """Tests for EnumValidator cache stability."""

    def test_save_preserves_existing_timestamps(self, cache_dir):
        """Adding a new term should NOT change timestamps of existing terms."""
        prefix_dir = cache_dir / "go"
        prefix_dir.mkdir()
        cache_file = prefix_dir / "terms.csv"

        original_ts = "2025-01-15T10:00:00.000000"
        _write_terms_csv(cache_file, [
            {"curie": "GO:0008150", "label": "biological_process", "retrieved_at": original_ts},
        ])

        config = ValidationConfig(cache_labels=True, cache_dir=cache_dir)
        validator = EnumValidator(config)
        validator._save_to_cache("GO", "GO:0003674", "molecular_function")

        rows = _read_terms_csv(cache_file)
        by_curie = {r["curie"]: r for r in rows}

        assert by_curie["GO:0008150"]["retrieved_at"] == original_ts
        assert by_curie["GO:0003674"]["retrieved_at"] != original_ts

    def test_save_sorts_by_curie(self, cache_dir):
        """Cache entries should be sorted by CURIE."""
        prefix_dir = cache_dir / "go"
        prefix_dir.mkdir()
        cache_file = prefix_dir / "terms.csv"

        _write_terms_csv(cache_file, [
            {"curie": "GO:0008150", "label": "biological_process", "retrieved_at": "2025-01-01"},
            {"curie": "GO:0003674", "label": "molecular_function", "retrieved_at": "2025-01-01"},
        ])

        config = ValidationConfig(cache_labels=True, cache_dir=cache_dir)
        validator = EnumValidator(config)
        validator._save_to_cache("GO", "GO:0005575", "cellular_component")

        rows = _read_terms_csv(cache_file)
        curies = [r["curie"] for r in rows]
        assert curies == sorted(curies)

    def test_idempotent_save(self, cache_dir):
        """Re-saving same label should produce identical file."""
        prefix_dir = cache_dir / "go"
        prefix_dir.mkdir()
        cache_file = prefix_dir / "terms.csv"

        original_ts = "2025-01-15T10:00:00.000000"
        _write_terms_csv(cache_file, [
            {"curie": "GO:0008150", "label": "biological_process", "retrieved_at": original_ts},
        ])

        content_before = cache_file.read_text()

        config = ValidationConfig(cache_labels=True, cache_dir=cache_dir)
        validator = EnumValidator(config)
        validator._save_to_cache("GO", "GO:0008150", "biological_process")

        content_after = cache_file.read_text()
        assert content_before == content_after

    def test_concurrent_saves_preserve_all_entries(self, cache_dir):
        """Concurrent validator writers should serialize cache updates safely."""
        _run_concurrent_cache_writers(cache_dir, _validator_cache_worker)


class TestCacheLFLineEndings:
    """Tests that cache CSV files use LF (not CRLF) line endings. See #20."""

    def test_terms_cache_uses_lf(self, cache_dir):
        """PermissibleValueMeaningPlugin cache should use LF line endings."""
        prefix_dir = cache_dir / "go"
        prefix_dir.mkdir()

        plugin = PermissibleValueMeaningPlugin(cache_labels=True, cache_dir=cache_dir)
        plugin._save_to_cache("GO", "GO:0008150", "biological_process")

        raw = (prefix_dir / "terms.csv").read_bytes()
        assert b"\r\n" not in raw, "Cache file contains CRLF line endings"
        assert b"\n" in raw, "Cache file has no newlines at all"

    def test_validator_cache_uses_lf(self, cache_dir):
        """EnumValidator cache should use LF line endings."""
        prefix_dir = cache_dir / "go"
        prefix_dir.mkdir()

        config = ValidationConfig(cache_labels=True, cache_dir=cache_dir)
        validator = EnumValidator(config)
        validator._save_to_cache("GO", "GO:0008150", "biological_process")

        raw = (prefix_dir / "terms.csv").read_bytes()
        assert b"\r\n" not in raw, "Cache file contains CRLF line endings"
        assert b"\n" in raw, "Cache file has no newlines at all"
