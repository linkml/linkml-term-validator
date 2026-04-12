"""Helpers for cache file locking and atomic CSV writes."""

import csv
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Iterable, Iterator, Mapping, Sequence

from filelock import FileLock


def get_cache_lock_file(cache_file: Path) -> Path:
    """Return the sidecar lock file path for a cache file."""
    suffix = f"{cache_file.suffix}.lock" if cache_file.suffix else ".lock"
    return cache_file.with_suffix(suffix)


@contextmanager
def locked_cache_file(cache_file: Path, timeout: float = 30.0) -> Iterator[None]:
    """Acquire an exclusive lock for a cache file using a sidecar lock file."""
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(get_cache_lock_file(cache_file)), timeout=timeout):
        yield


def atomic_write_csv(
    cache_file: Path,
    fieldnames: Sequence[str],
    rows: Iterable[Mapping[str, str]],
) -> None:
    """Write CSV rows to a temp file and atomically replace the target."""
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            dir=cache_file.parent,
            delete=False,
            newline="",
            encoding="utf-8",
        ) as tmp:
            tmp_path = Path(tmp.name)
            writer = csv.DictWriter(tmp, fieldnames=list(fieldnames), lineterminator="\n")
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        tmp_path.replace(cache_file)
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink()
