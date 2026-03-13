"""Unit tests for CPT utilities (offline, no network calls)."""

import csv
import io
import zipfile
from pathlib import Path

import pytest

from linkml_term_validator.cpt_utils import (
    CPT_CODE_PATTERN,
    build_cpt_cache,
    find_rvu_csv_in_zip,
    is_cpt_prefix,
    parse_rvu_csv,
)

SAMPLE_RVU_PATH = Path(__file__).parent / "data" / "sample_rvu_data.csv"


@pytest.fixture
def sample_csv_content() -> str:
    return SAMPLE_RVU_PATH.read_text()


@pytest.fixture
def sample_zip_bytes(sample_csv_content: str) -> bytes:
    """Create an in-memory ZIP containing the sample CSV."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("PPRRVU2026_Jan_nonQPP.csv", sample_csv_content)
    return buf.getvalue()


# ── is_cpt_prefix ──


def test_is_cpt_prefix_uppercase():
    assert is_cpt_prefix("CPT") is True


def test_is_cpt_prefix_lowercase():
    assert is_cpt_prefix("cpt") is True


def test_is_cpt_prefix_mixed_case():
    assert is_cpt_prefix("Cpt") is True


def test_is_cpt_prefix_other():
    assert is_cpt_prefix("GO") is False
    assert is_cpt_prefix("HP") is False
    assert is_cpt_prefix("") is False


# ── CPT_CODE_PATTERN ──


def test_cpt_code_pattern_category_i():
    assert CPT_CODE_PATTERN.match("99213")
    assert CPT_CODE_PATTERN.match("99214")
    assert CPT_CODE_PATTERN.match("10021")


def test_cpt_code_pattern_category_ii():
    assert CPT_CODE_PATTERN.match("0001F")
    assert CPT_CODE_PATTERN.match("9999F")


def test_cpt_code_pattern_category_iii():
    assert CPT_CODE_PATTERN.match("0054T")
    assert CPT_CODE_PATTERN.match("0001T")


def test_cpt_code_pattern_rejects_hcpcs_level_ii():
    assert CPT_CODE_PATTERN.match("G0101") is None
    assert CPT_CODE_PATTERN.match("J1234") is None
    assert CPT_CODE_PATTERN.match("A0001") is None


def test_cpt_code_pattern_rejects_invalid():
    assert CPT_CODE_PATTERN.match("9921") is None  # too short
    assert CPT_CODE_PATTERN.match("992133") is None  # too long
    assert CPT_CODE_PATTERN.match("9921A") is None  # wrong suffix


# ── parse_rvu_csv ──


def test_parse_rvu_csv_extracts_base_codes(sample_csv_content: str):
    codes = parse_rvu_csv(sample_csv_content)
    assert "99213" in codes
    assert "99214" in codes
    assert codes["99213"] == "Office o/p est low 20 min"
    assert codes["99214"] == "Office o/p est mod 30 min"


def test_parse_rvu_csv_excludes_modifier_rows(sample_csv_content: str):
    codes = parse_rvu_csv(sample_csv_content)
    # 99213 with MOD=25 should be excluded; only the base row kept
    assert codes["99213"] == "Office o/p est low 20 min"
    # Only one entry for 99213 (base code)
    count_99213 = sum(1 for k in codes if k == "99213")
    assert count_99213 == 1


def test_parse_rvu_csv_excludes_hcpcs_level_ii(sample_csv_content: str):
    codes = parse_rvu_csv(sample_csv_content)
    assert "G0101" not in codes
    assert "G0102" not in codes


def test_parse_rvu_csv_includes_category_ii_and_iii(sample_csv_content: str):
    codes = parse_rvu_csv(sample_csv_content)
    assert "0001F" in codes
    assert codes["0001F"] == "Heart failure composite"
    assert "0054T" in codes
    assert codes["0054T"] == "Bone integrity ultrasound"


# ── find_rvu_csv_in_zip ──


def test_find_rvu_csv_in_zip(sample_zip_bytes: bytes, sample_csv_content: str):
    result = find_rvu_csv_in_zip(sample_zip_bytes)
    assert "HCPCS" in result
    assert "99213" in result


def test_find_rvu_csv_in_zip_missing():
    """ZIP with no matching CSV should raise."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("unrelated.txt", "nothing here")

    with pytest.raises(Exception, match="No RVU CSV found"):
        find_rvu_csv_in_zip(buf.getvalue())


# ── build_cpt_cache ──


def test_build_cpt_cache_writes_correct_format(
    tmp_path: Path, sample_zip_bytes: bytes, monkeypatch: pytest.MonkeyPatch
):
    """Monkeypatch download, verify CSV output format."""
    monkeypatch.setattr(
        "linkml_term_validator.cpt_utils.download_rvu_zip",
        lambda url=None: sample_zip_bytes,
    )

    cache_file = build_cpt_cache(tmp_path)
    assert cache_file.exists()

    with open(cache_file) as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    assert len(rows) == 4  # 99213, 99214, 0001F, 0054T
    assert set(reader.fieldnames or []) == {"curie", "label", "retrieved_at"}


def test_build_cpt_cache_curie_format(
    tmp_path: Path, sample_zip_bytes: bytes, monkeypatch: pytest.MonkeyPatch
):
    """CURIEs should have CPT: prefix."""
    monkeypatch.setattr(
        "linkml_term_validator.cpt_utils.download_rvu_zip",
        lambda url=None: sample_zip_bytes,
    )

    cache_file = build_cpt_cache(tmp_path)

    with open(cache_file) as f:
        reader = csv.DictReader(f)
        curies = [row["curie"] for row in reader]

    assert all(c.startswith("CPT:") for c in curies)
    assert "CPT:99213" in curies
    assert "CPT:0001F" in curies
    assert "CPT:0054T" in curies


def test_build_cpt_cache_sorted(
    tmp_path: Path, sample_zip_bytes: bytes, monkeypatch: pytest.MonkeyPatch
):
    """Cache entries should be sorted by CURIE."""
    monkeypatch.setattr(
        "linkml_term_validator.cpt_utils.download_rvu_zip",
        lambda url=None: sample_zip_bytes,
    )

    cache_file = build_cpt_cache(tmp_path)

    with open(cache_file) as f:
        reader = csv.DictReader(f)
        curies = [row["curie"] for row in reader]

    assert curies == sorted(curies)
