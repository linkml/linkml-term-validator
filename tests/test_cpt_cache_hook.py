"""Tests for CPT auto-build hooks in _load_cache (base.py and validator.py)."""

import csv
from pathlib import Path

import pytest

from linkml_term_validator.models import ValidationConfig
from linkml_term_validator.plugins.permissible_value_plugin import PermissibleValueMeaningPlugin
from linkml_term_validator.validator import EnumValidator


def _write_cpt_cache(cache_dir: Path, entries: dict[str, str]) -> None:
    """Helper: write a CPT cache file with the given code->label entries."""
    cpt_dir = cache_dir / "cpt"
    cpt_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cpt_dir / "terms.csv"
    with open(cache_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["curie", "label", "retrieved_at"])
        writer.writeheader()
        for code, label in sorted(entries.items()):
            writer.writerow(
                {"curie": f"CPT:{code}", "label": label, "retrieved_at": "2026-01-01T00:00:00"}
            )


# ── BaseOntologyPlugin._load_cache hook (via concrete PermissibleValueMeaningPlugin) ──


def test_base_load_cache_triggers_cpt_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """When CPT cache is missing, _load_cache should trigger auto-build."""
    build_called = {"count": 0}

    def mock_build(cache_dir, url=None):
        build_called["count"] += 1
        _write_cpt_cache(cache_dir, {"99213": "Office o/p est low 20 min"})
        return cache_dir / "cpt" / "terms.csv"

    monkeypatch.setattr("linkml_term_validator.cpt_utils.build_cpt_cache", mock_build)

    plugin = PermissibleValueMeaningPlugin(cache_dir=tmp_path)
    result = plugin._load_cache("CPT")

    assert build_called["count"] == 1
    assert "CPT:99213" in result
    assert result["CPT:99213"] == "Office o/p est low 20 min"


def test_base_load_cache_skips_build_when_cache_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """When CPT cache already exists, auto-build should not be triggered."""
    _write_cpt_cache(tmp_path, {"99213": "Office o/p est low 20 min"})

    build_called = {"count": 0}

    def mock_build(cache_dir, url=None):
        build_called["count"] += 1

    monkeypatch.setattr("linkml_term_validator.cpt_utils.build_cpt_cache", mock_build)

    plugin = PermissibleValueMeaningPlugin(cache_dir=tmp_path)
    result = plugin._load_cache("CPT")

    assert build_called["count"] == 0
    assert "CPT:99213" in result


def test_base_load_cache_does_not_trigger_for_non_cpt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Non-CPT prefixes should not trigger auto-build."""
    build_called = {"count": 0}

    def mock_build(cache_dir, url=None):
        build_called["count"] += 1

    monkeypatch.setattr("linkml_term_validator.cpt_utils.build_cpt_cache", mock_build)

    plugin = PermissibleValueMeaningPlugin(cache_dir=tmp_path)
    result = plugin._load_cache("GO")

    assert build_called["count"] == 0
    assert result == {}


def test_base_cpt_auto_build_failure_graceful(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    """Download failure should print warning, not crash."""
    from linkml_term_validator.cpt_utils import CptDownloadError

    def mock_build(cache_dir, url=None):
        raise CptDownloadError("Network unavailable")

    monkeypatch.setattr("linkml_term_validator.cpt_utils.build_cpt_cache", mock_build)

    plugin = PermissibleValueMeaningPlugin(cache_dir=tmp_path)
    result = plugin._load_cache("CPT")

    assert result == {}
    captured = capsys.readouterr()
    assert "Warning" in captured.err
    assert "CPT" in captured.err


# ── EnumValidator._load_cache hook ──


def test_validator_load_cache_triggers_cpt_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """EnumValidator._load_cache should trigger auto-build for CPT."""
    def mock_build(cache_dir, url=None):
        _write_cpt_cache(cache_dir, {"99214": "Office o/p est mod 30 min"})
        return cache_dir / "cpt" / "terms.csv"

    monkeypatch.setattr("linkml_term_validator.cpt_utils.build_cpt_cache", mock_build)

    config = ValidationConfig(cache_dir=tmp_path)
    validator = EnumValidator(config)
    result = validator._load_cache("CPT")

    assert "CPT:99214" in result
    assert result["CPT:99214"] == "Office o/p est mod 30 min"


def test_validator_load_cache_does_not_trigger_for_non_cpt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """EnumValidator should not trigger auto-build for non-CPT prefixes."""
    build_called = {"count": 0}

    def mock_build(cache_dir, url=None):
        build_called["count"] += 1

    monkeypatch.setattr("linkml_term_validator.cpt_utils.build_cpt_cache", mock_build)

    config = ValidationConfig(cache_dir=tmp_path)
    validator = EnumValidator(config)
    result = validator._load_cache("HP")

    assert build_called["count"] == 0
    assert result == {}


def test_validator_cpt_auto_build_failure_graceful(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    """EnumValidator should handle CPT build failure gracefully."""
    from linkml_term_validator.cpt_utils import CptDownloadError

    def mock_build(cache_dir, url=None):
        raise CptDownloadError("Timeout")

    monkeypatch.setattr("linkml_term_validator.cpt_utils.build_cpt_cache", mock_build)

    config = ValidationConfig(cache_dir=tmp_path)
    validator = EnumValidator(config)
    result = validator._load_cache("CPT")

    assert result == {}
    captured = capsys.readouterr()
    assert "Warning" in captured.err


# ── Label lookup end-to-end (with pre-populated cache) ──


def test_cpt_label_lookup_from_cache(tmp_path: Path):
    """Pre-populated CPT cache should provide labels via get_ontology_label."""
    _write_cpt_cache(
        tmp_path,
        {
            "99213": "Office o/p est low 20 min",
            "0001F": "Heart failure composite",
        },
    )

    config = ValidationConfig(cache_dir=tmp_path, cache_labels=True)
    validator = EnumValidator(config)

    assert validator.get_ontology_label("CPT:99213") == "Office o/p est low 20 min"
    assert validator.get_ontology_label("CPT:0001F") == "Heart failure composite"
    assert validator.get_ontology_label("CPT:00000") is None  # not in cache


def test_cpt_label_lookup_base_plugin(tmp_path: Path):
    """Pre-populated CPT cache should provide labels via plugin."""
    _write_cpt_cache(
        tmp_path,
        {"99213": "Office o/p est low 20 min"},
    )

    plugin = PermissibleValueMeaningPlugin(cache_dir=tmp_path)
    assert plugin.get_ontology_label("CPT:99213") == "Office o/p est low 20 min"
