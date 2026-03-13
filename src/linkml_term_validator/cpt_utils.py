"""Utilities for building CPT code validation cache from CMS data.

Downloads the CMS Physician Fee Schedule Relative Value Files (RVU),
extracts CPT codes and short descriptors, and writes a cache file
compatible with the existing label cache format.

No new dependencies — uses only stdlib (urllib, zipfile, csv, io, re).
"""

import csv
import io
import os
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

CMS_RVU_URL = "https://www.cms.gov/files/zip/rvu26a-updated-12-29-2025.zip"

# Matches CPT Category I (99213), Category II (0001F), Category III (0054T)
# Excludes HCPCS Level II codes (alpha prefix like G0101)
CPT_CODE_PATTERN = re.compile(r"^\d{4}[0-9FT]$")

# Finds the RVU CSV inside the ZIP archive
RVU_CSV_PATTERN = re.compile(r"PPRRVU.*nonQP.*\.csv", re.IGNORECASE)


class CptDataError(Exception):
    """Base exception for CPT data errors."""


class CptDownloadError(CptDataError):
    """Error downloading CPT data from CMS."""


class CptParseError(CptDataError):
    """Error parsing CPT data."""


def is_cpt_prefix(prefix: str) -> bool:
    """Check if a prefix refers to CPT codes (case-insensitive).

    Args:
        prefix: Ontology prefix string

    Returns:
        True if this is a CPT prefix
    """
    return prefix.upper() == "CPT"


def download_rvu_zip(url: str | None = None) -> bytes:
    """Download the CMS RVU ZIP file.

    Args:
        url: URL to download from (defaults to CMS_RVU_URL or env var override)

    Returns:
        Raw bytes of the ZIP file

    Raises:
        CptDownloadError: If download fails
    """
    if url is None:
        url = os.environ.get("LINKML_TERM_VALIDATOR_CPT_URL", CMS_RVU_URL)

    print(f"Downloading CPT data from {url} ...", file=sys.stderr)

    try:
        req = Request(url, headers={"User-Agent": "linkml-term-validator"})
        with urlopen(req, timeout=120) as response:  # noqa: S310
            data = response.read()
        print(f"Downloaded {len(data)} bytes.", file=sys.stderr)
        return data
    except Exception as e:
        raise CptDownloadError(f"Failed to download CPT data from {url}: {e}") from e


def find_rvu_csv_in_zip(zip_data: bytes) -> str:
    """Extract the RVU CSV content from a ZIP archive.

    Args:
        zip_data: Raw bytes of the ZIP file

    Returns:
        CSV content as a string

    Raises:
        CptParseError: If the expected CSV file is not found in the ZIP
    """
    try:
        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            for name in zf.namelist():
                if RVU_CSV_PATTERN.search(name):
                    return zf.read(name).decode("utf-8", errors="replace")
            raise CptParseError(
                f"No RVU CSV found in ZIP. Files: {zf.namelist()}"
            )
    except zipfile.BadZipFile as e:
        raise CptParseError(f"Invalid ZIP file: {e}") from e


def parse_rvu_csv(csv_content: str) -> dict[str, str]:
    """Parse the RVU CSV and extract base CPT codes with labels.

    The CMS RVU CSV has 10 header/comment rows before the actual data.
    We extract rows where:
    - MOD column is empty (base codes only, no modifiers)
    - HCPCS code matches CPT pattern (digits + digit/F/T suffix)

    Args:
        csv_content: Raw CSV content string

    Returns:
        Dict mapping CPT codes to short descriptors (e.g. {"99213": "Office o/p est low 20 min"})

    Raises:
        CptParseError: If CSV cannot be parsed
    """
    try:
        lines = csv_content.splitlines()

        # Find the header row (starts with "HCPCS,")
        header_idx = None
        for i, line in enumerate(lines):
            if line.startswith("HCPCS,"):
                header_idx = i
                break

        if header_idx is None:
            raise CptParseError("Could not find HCPCS header row in CSV")

        data_lines = lines[header_idx:]
        reader = csv.DictReader(data_lines)

        codes: dict[str, str] = {}
        for row in reader:
            hcpcs = row.get("HCPCS", "").strip()
            mod = row.get("MOD", "").strip()
            description = row.get("DESCRIPTION", "").strip()

            # Skip rows with modifiers (we want base codes only)
            if mod:
                continue

            # Only include codes matching CPT patterns
            if CPT_CODE_PATTERN.match(hcpcs) and description:
                codes[hcpcs] = description

        if not codes:
            raise CptParseError("No CPT codes found in CSV data")

        return codes

    except CptParseError:
        raise
    except Exception as e:
        raise CptParseError(f"Failed to parse RVU CSV: {e}") from e


def build_cpt_cache(cache_dir: Path | str, url: str | None = None) -> Path:
    """Download CMS data and build the CPT label cache.

    This is the main entry point. It:
    1. Downloads the RVU ZIP from CMS
    2. Extracts and parses the CSV
    3. Writes cache/cpt/terms.csv in the standard cache format

    Args:
        cache_dir: Root cache directory (e.g. Path("cache"))
        url: Optional URL override for testing

    Returns:
        Path to the written cache file

    Raises:
        CptDataError: If download or parsing fails
    """
    cache_dir = Path(cache_dir)

    zip_data = download_rvu_zip(url)
    csv_content = find_rvu_csv_in_zip(zip_data)
    codes = parse_rvu_csv(csv_content)

    # Write cache file
    cpt_dir = cache_dir / "cpt"
    cpt_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cpt_dir / "terms.csv"

    now = datetime.now().isoformat()

    with open(cache_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["curie", "label", "retrieved_at"])
        writer.writeheader()
        for code in sorted(codes.keys()):
            writer.writerow(
                {
                    "curie": f"CPT:{code}",
                    "label": codes[code],
                    "retrieved_at": now,
                }
            )

    print(f"CPT cache built: {len(codes)} codes written to {cache_file}", file=sys.stderr)
    return cache_file
