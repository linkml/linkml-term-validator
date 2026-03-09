"""Tests for the migrate-cache CLI command."""

import csv
from pathlib import Path

from typer.testing import CliRunner

from linkml_term_validator.cli import app

runner = CliRunner()


def _write_terms_csv(cache_file: Path, entries: list[dict[str, str]]) -> None:
    """Helper to write a terms.csv file."""
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["curie", "label", "retrieved_at"])
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


def test_migrate_sorts_entries(tmp_path):
    """migrate-cache should sort entries by CURIE."""
    cache_dir = tmp_path / "cache"
    terms_file = cache_dir / "go" / "terms.csv"
    _write_terms_csv(terms_file, [
        {"curie": "GO:0008150", "label": "biological_process", "retrieved_at": "2025-01-01"},
        {"curie": "GO:0003674", "label": "molecular_function", "retrieved_at": "2025-01-01"},
        {"curie": "GO:0005575", "label": "cellular_component", "retrieved_at": "2025-01-01"},
    ])

    result = runner.invoke(app, ["migrate-cache", "--cache-dir", str(cache_dir)])
    assert result.exit_code == 0

    rows = _read_terms_csv(terms_file)
    curies = [r["curie"] for r in rows]
    assert curies == sorted(curies)


def test_migrate_preserves_timestamps(tmp_path):
    """migrate-cache should preserve existing timestamps."""
    cache_dir = tmp_path / "cache"
    terms_file = cache_dir / "go" / "terms.csv"
    ts = "2025-01-15T10:00:00.000000"
    _write_terms_csv(terms_file, [
        {"curie": "GO:0003674", "label": "molecular_function", "retrieved_at": ts},
        {"curie": "GO:0008150", "label": "biological_process", "retrieved_at": ts},
    ])

    result = runner.invoke(app, ["migrate-cache", "--cache-dir", str(cache_dir)])
    assert result.exit_code == 0

    rows = _read_terms_csv(terms_file)
    for row in rows:
        assert row["retrieved_at"] == ts


def test_migrate_removes_duplicates(tmp_path):
    """migrate-cache should deduplicate entries, keeping latest timestamp."""
    cache_dir = tmp_path / "cache"
    terms_file = cache_dir / "go" / "terms.csv"
    _write_terms_csv(terms_file, [
        {"curie": "GO:0008150", "label": "biological_process", "retrieved_at": "2025-01-01"},
        {"curie": "GO:0008150", "label": "biological_process", "retrieved_at": "2025-06-01"},
        {"curie": "GO:0003674", "label": "molecular_function", "retrieved_at": "2025-01-01"},
    ])

    result = runner.invoke(app, ["migrate-cache", "--cache-dir", str(cache_dir)])
    assert result.exit_code == 0
    assert "dupes removed" in result.output

    rows = _read_terms_csv(terms_file)
    assert len(rows) == 2
    by_curie = {r["curie"]: r for r in rows}
    assert by_curie["GO:0008150"]["retrieved_at"] == "2025-06-01"


def test_migrate_dry_run(tmp_path):
    """--dry-run should not modify files."""
    cache_dir = tmp_path / "cache"
    terms_file = cache_dir / "go" / "terms.csv"
    _write_terms_csv(terms_file, [
        {"curie": "GO:0008150", "label": "biological_process", "retrieved_at": "2025-01-01"},
        {"curie": "GO:0003674", "label": "molecular_function", "retrieved_at": "2025-01-01"},
    ])

    content_before = terms_file.read_text()
    result = runner.invoke(app, ["migrate-cache", "--cache-dir", str(cache_dir), "--dry-run"])
    assert result.exit_code == 0
    assert "preview" in result.output

    content_after = terms_file.read_text()
    assert content_before == content_after


def test_migrate_no_cache_dir(tmp_path):
    """Should fail gracefully if cache dir doesn't exist."""
    result = runner.invoke(app, ["migrate-cache", "--cache-dir", str(tmp_path / "nonexistent")])
    assert result.exit_code == 1


def test_migrate_already_sorted(tmp_path):
    """Already-sorted file should report no changes needed."""
    cache_dir = tmp_path / "cache"
    terms_file = cache_dir / "go" / "terms.csv"
    _write_terms_csv(terms_file, [
        {"curie": "GO:0003674", "label": "molecular_function", "retrieved_at": "2025-01-01"},
        {"curie": "GO:0008150", "label": "biological_process", "retrieved_at": "2025-01-01"},
    ])

    content_before = terms_file.read_text()
    result = runner.invoke(app, ["migrate-cache", "--cache-dir", str(cache_dir)])
    assert result.exit_code == 0
    assert "Files updated: 0" in result.output or "Files needing: 0" in result.output

    content_after = terms_file.read_text()
    assert content_before == content_after


# =========================================================================
# Enum cache file tests
# =========================================================================


def _write_enum_csv(cache_file: Path, curies: list[str]) -> None:
    """Helper to write an enum cache CSV file."""
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["curie"])
        writer.writeheader()
        for curie in curies:
            writer.writerow({"curie": curie})


def _read_enum_csv(cache_file: Path) -> list[str]:
    """Helper to read an enum cache CSV file."""
    curies = []
    with open(cache_file) as f:
        reader = csv.DictReader(f)
        for row in reader:
            curies.append(row["curie"])
    return curies


def test_migrate_sorts_enum_entries(tmp_path):
    """migrate-cache should sort enum cache entries by CURIE."""
    cache_dir = tmp_path / "cache"
    enum_file = cache_dir / "enums" / "test_enum_abc123.csv"
    _write_enum_csv(enum_file, ["HP:0000003", "HP:0000001", "HP:0000002"])

    result = runner.invoke(app, ["migrate-cache", "--cache-dir", str(cache_dir)])
    assert result.exit_code == 0

    curies = _read_enum_csv(enum_file)
    assert curies == ["HP:0000001", "HP:0000002", "HP:0000003"]


def test_migrate_deduplicates_enum_entries(tmp_path):
    """migrate-cache should deduplicate enum cache entries."""
    cache_dir = tmp_path / "cache"
    enum_file = cache_dir / "enums" / "test_enum_abc123.csv"
    _write_enum_csv(enum_file, ["HP:0000001", "HP:0000002", "HP:0000001", "HP:0000003"])

    result = runner.invoke(app, ["migrate-cache", "--cache-dir", str(cache_dir)])
    assert result.exit_code == 0
    assert "dupes removed" in result.output

    curies = _read_enum_csv(enum_file)
    assert curies == ["HP:0000001", "HP:0000002", "HP:0000003"]


def test_migrate_enum_already_sorted(tmp_path):
    """Already-sorted enum file should not be rewritten."""
    cache_dir = tmp_path / "cache"
    enum_file = cache_dir / "enums" / "test_enum_abc123.csv"
    _write_enum_csv(enum_file, ["HP:0000001", "HP:0000002", "HP:0000003"])

    content_before = enum_file.read_text()
    result = runner.invoke(app, ["migrate-cache", "--cache-dir", str(cache_dir)])
    assert result.exit_code == 0

    content_after = enum_file.read_text()
    assert content_before == content_after


def test_migrate_enum_dry_run(tmp_path):
    """--dry-run should not modify enum files."""
    cache_dir = tmp_path / "cache"
    enum_file = cache_dir / "enums" / "test_enum_abc123.csv"
    _write_enum_csv(enum_file, ["HP:0000003", "HP:0000001"])

    content_before = enum_file.read_text()
    result = runner.invoke(app, ["migrate-cache", "--cache-dir", str(cache_dir), "--dry-run"])
    assert result.exit_code == 0
    assert "Would update" in result.output

    content_after = enum_file.read_text()
    assert content_before == content_after
