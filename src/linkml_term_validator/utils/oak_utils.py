"""OAK adapter management and label caching.

This module centralizes the ontology-access infrastructure shared by the
standalone :class:`~linkml_term_validator.validator.EnumValidator` and the
:class:`~linkml_term_validator.plugins.base.BaseOntologyPlugin`. Both hold an
:class:`OntologyAccess` instance rather than reimplementing prefix parsing,
adapter resolution, and label caching.

Example:
    >>> from linkml_term_validator.utils import OntologyAccess, get_prefix, normalize_string
    >>> get_prefix("GO:0008150")
    'GO'
    >>> get_prefix("invalid-no-colon")
    >>> normalize_string("Hello, World!")
    'hello world'
"""

import csv
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from oaklib import get_adapter
from ruamel.yaml import YAML

from linkml_term_validator.cache_utils import atomic_write_csv, locked_cache_file


def get_prefix(curie: str) -> Optional[str]:
    """Extract the prefix from a CURIE.

    Args:
        curie: A CURIE like "GO:0008150"

    Returns:
        The prefix (e.g., "GO") or None if there is no prefix separator

    Examples:
        >>> get_prefix("GO:0008150")
        'GO'
        >>> get_prefix("CHEBI:12345")
        'CHEBI'
        >>> get_prefix("invalid")
    """
    if ":" not in curie:
        return None
    return curie.split(":", 1)[0]


def normalize_string(s: str) -> str:
    """Normalize a string for comparison.

    Removes punctuation and collapses whitespace, lowercasing the result.

    Args:
        s: String to normalize

    Returns:
        Normalized string

    Examples:
        >>> normalize_string("Hello, World!")
        'hello world'
        >>> normalize_string("T-Cell Receptor")
        't cell receptor'
        >>> normalize_string("Multi  Spaces")
        'multi spaces'
    """
    normalized = re.sub(r"[^\w\s]", " ", s.lower())
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


class OntologyAccess:
    """Resolve ontology labels through OAK adapters with multi-level caching.

    Owns the infrastructure that was previously duplicated between the
    standalone validator and the plugin base class:

    - Per-prefix OAK adapter management (with optional ``oak_config.yaml``)
    - Multi-level label caching (in-memory + file-based CSV)
    - Unknown-prefix tracking

    Examples:
        >>> access = OntologyAccess(cache_labels=False)
        >>> access.get_unknown_prefixes()
        set()
        >>> access.is_prefix_configured("GO")
        False
    """

    def __init__(
        self,
        oak_adapter_string: str = "sqlite:obo:",
        cache_labels: bool = True,
        cache_dir: Path | str = Path("cache"),
        oak_config_path: Optional[Path | str] = None,
        offline: bool = False,
    ):
        """Initialize ontology access.

        Args:
            oak_adapter_string: Default OAK adapter string (e.g., "sqlite:obo:")
            cache_labels: Whether to cache ontology labels to disk
            cache_dir: Directory for label cache files
            oak_config_path: Path to oak_config.yaml for per-prefix adapters
            offline: If True, never build OAK adapters (guaranteeing no external
                access); resolve everything exclusively from the file cache.
        """
        self.oak_adapter_string = oak_adapter_string
        self.cache_labels = cache_labels
        self.offline = offline
        self.cache_dir = Path(cache_dir) if isinstance(cache_dir, str) else cache_dir
        self.oak_config_path = (
            Path(oak_config_path) if isinstance(oak_config_path, str) else oak_config_path
        )

        # In-memory caches and state
        self._label_cache: dict[str, Optional[str]] = {}
        self._adapter_cache: dict[str, object | None] = {}
        self._unknown_prefixes: set[str] = set()

        # ontology_adapters mapping plus the full parsed config (so callers can
        # read additional keys without re-reading the file).
        self.oak_config: dict[str, str] = {}
        self.loaded_config: dict[str, Any] = {}
        if self.oak_config_path and self.oak_config_path.exists():
            self._load_oak_config()

    def _load_oak_config(self) -> None:
        """Load the ``ontology_adapters`` mapping from oak_config.yaml."""
        if self.oak_config_path is None:
            return
        yaml = YAML(typ="safe")
        with open(self.oak_config_path) as f:
            config = yaml.load(f)
        self.loaded_config = config or {}
        if "ontology_adapters" in self.loaded_config:
            self.oak_config = self.loaded_config["ontology_adapters"]

    # =========================================================================
    # Prefix helpers
    # =========================================================================

    def is_prefix_configured(self, prefix: str) -> bool:
        """Check if a prefix has a non-empty adapter configured in oak_config.

        Args:
            prefix: Ontology prefix (e.g., "GO")

        Returns:
            True if the prefix maps to a non-empty adapter string
        """
        return prefix in self.oak_config and bool(self.oak_config[prefix])

    # =========================================================================
    # Label cache (file-based)
    # =========================================================================

    def get_cache_file(self, prefix: str) -> Path:
        """Get the cache file path for a prefix, creating the directory."""
        prefix_dir = self.cache_dir / prefix.lower()
        prefix_dir.mkdir(parents=True, exist_ok=True)
        return prefix_dir / "terms.csv"

    def load_cache(self, prefix: str) -> dict[str, str]:
        """Load cached labels for a prefix as a CURIE -> label dict."""
        cache_file = self.get_cache_file(prefix)
        if not cache_file.exists():
            return {}

        cached = {}
        with open(cache_file) as f:
            reader = csv.DictReader(f)
            for row in reader:
                cached[row["curie"]] = row["label"]
        return cached

    def load_cache_with_timestamps(self, prefix: str) -> dict[str, dict[str, str]]:
        """Load cached labels with timestamps for a prefix.

        Returns:
            Dict mapping CURIEs to {"label": ..., "retrieved_at": ...}
        """
        cache_file = self.get_cache_file(prefix)
        if not cache_file.exists():
            return {}

        cached: dict[str, dict[str, str]] = {}
        with open(cache_file) as f:
            reader = csv.DictReader(f)
            for row in reader:
                cached[row["curie"]] = {
                    "label": row["label"],
                    "retrieved_at": row.get("retrieved_at", ""),
                }
        return cached

    def save_to_cache(self, prefix: str, curie: str, label: str) -> None:
        """Save a label to the cache.

        Preserves existing timestamps for unchanged entries; only new or changed
        entries get a fresh timestamp. Entries are sorted by CURIE for
        deterministic output. The write is locked and atomic so parallel
        validators do not lose entries or leave truncated files.

        Args:
            prefix: Ontology prefix
            curie: Full CURIE
            label: Label to cache
        """
        if not self.cache_labels:
            return

        cache_file = self.get_cache_file(prefix)

        with locked_cache_file(cache_file):
            existing = self.load_cache_with_timestamps(prefix)

            now = datetime.now().isoformat()
            if curie not in existing or existing[curie]["label"] != label:
                existing[curie] = {"label": label, "retrieved_at": now}

            atomic_write_csv(
                cache_file,
                ["curie", "label", "retrieved_at"],
                (
                    {
                        "curie": cached_curie,
                        "label": existing[cached_curie]["label"],
                        "retrieved_at": existing[cached_curie]["retrieved_at"],
                    }
                    for cached_curie in sorted(existing)
                ),
            )

    # =========================================================================
    # Adapters and label resolution
    # =========================================================================

    def get_adapter(self, prefix: str) -> object | None:
        """Get an OAK adapter for a prefix.

        In offline mode no adapter is ever built, guaranteeing no external
        access: resolution falls back entirely to the file cache.

        Resolution order:
        - If oak_config is loaded, use its mapping (None for an empty/missing
          entry; never fall back to the default adapter).
        - Otherwise, for the default ``sqlite:obo:`` adapter string, build a
          per-prefix ``sqlite:obo:<prefix>`` adapter.
        - Otherwise, use the configured default adapter string directly.

        Args:
            prefix: Ontology prefix

        Returns:
            OAK adapter or None if unavailable
        """
        # Offline mode: never construct an adapter. Building one can download an
        # ontology database or hit a remote service, so returning None here is
        # the single chokepoint that guarantees no external access.
        if self.offline:
            return None

        if prefix in self._adapter_cache:
            return self._adapter_cache[prefix]

        adapter_string = None

        if prefix in self.oak_config:
            configured = self.oak_config[prefix]
            if not configured:
                self._adapter_cache[prefix] = None
                return None
            adapter_string = self._adapter_string_for_prefix(prefix, configured)
        elif self.oak_config:
            # oak_config is loaded but prefix not in it - don't fall back
            self._adapter_cache[prefix] = None
            return None
        else:
            adapter_string = self._adapter_string_for_prefix(prefix, self.oak_adapter_string)

        if adapter_string:
            adapter = get_adapter(adapter_string)
            self._configure_adapter_for_prefix(adapter, prefix)
            self._adapter_cache[prefix] = adapter
            return adapter

        self._adapter_cache[prefix] = None
        return None

    @staticmethod
    def _adapter_string_for_prefix(prefix: str, adapter_string: str) -> str | None:
        """Resolve adapter shorthands that need a per-prefix ontology slug."""
        adapter_string = adapter_string.strip()
        if not adapter_string:
            return None

        if adapter_string == "sqlite:obo:":
            return f"sqlite:obo:{prefix.lower()}"

        # OAK's OLS adapter needs a focus ontology. Accept ``ols:`` as a
        # convenient per-prefix shorthand in config files and CLI defaults.
        if adapter_string in {"ols", "ols:"}:
            return f"ols:{prefix.lower()}"

        return adapter_string

    @staticmethod
    def _configure_adapter_for_prefix(adapter: object, prefix: str) -> None:
        """Patch adapter focus metadata when OAK leaves it unset."""
        if not hasattr(adapter, "focus_ontology"):
            return
        if getattr(adapter, "focus_ontology", None):
            return

        resource = getattr(adapter, "resource", None)
        scheme = getattr(resource, "scheme", None)
        slug = getattr(resource, "slug", None)
        if scheme == "ols":
            setattr(adapter, "focus_ontology", slug or prefix.lower())

    def get_label(self, curie: str) -> Optional[str]:
        """Get the label for an ontology term.

        Uses multi-level caching: in-memory, then file, then adapter.

        Args:
            curie: A CURIE like "GO:0008150"

        Returns:
            The label or None if not found
        """
        if curie in self._label_cache:
            return self._label_cache[curie]

        prefix = get_prefix(curie)
        if not prefix:
            return None

        # Offline mode always consults the file cache, even when label caching
        # (writing) is disabled, since the cache is the only permitted source.
        if self.cache_labels or self.offline:
            cached = self.load_cache(prefix)
            if curie in cached:
                cached_label = cached[curie]
                self._label_cache[curie] = cached_label
                return cached_label

        adapter = self.get_adapter(prefix)
        if adapter is None:
            if not self.is_prefix_configured(prefix):
                self._unknown_prefixes.add(prefix)
            self._label_cache[curie] = None
            return None

        label = self._get_adapter_label(adapter, curie)
        self._label_cache[curie] = label

        if label and self.cache_labels:
            self.save_to_cache(prefix, curie, label)

        return label

    def _get_adapter_label(self, adapter: object, curie: str) -> Optional[str]:
        """Get a label from an adapter, including OLS4 response compatibility."""
        label = adapter.label(curie)  # type: ignore[attr-defined]
        if label is not None:
            return label

        return self._get_ols4_embedded_label(adapter, curie)

    @staticmethod
    def _get_ols4_embedded_label(adapter: object, curie: str) -> Optional[str]:
        """Extract labels from OLS4 payloads returned by older OAK adapters."""
        client = getattr(adapter, "client", None)
        focus_ontology = getattr(adapter, "focus_ontology", None)
        curie_to_uri = getattr(adapter, "curie_to_uri", None)
        if not client or not focus_ontology or not curie_to_uri:
            return None

        iri = curie_to_uri(curie)
        if not iri:
            return None

        term = client.get_term(ontology=focus_ontology, iri=iri)
        if not isinstance(term, dict):
            return None

        if isinstance(term.get("label"), str):
            return term["label"]

        embedded = term.get("_embedded")
        if not isinstance(embedded, dict):
            return None
        terms = embedded.get("terms")
        if not isinstance(terms, list) or not terms:
            return None
        first = terms[0]
        if isinstance(first, dict) and isinstance(first.get("label"), str):
            return first["label"]
        return None

    def get_unknown_prefixes(self) -> set[str]:
        """Get the set of prefixes encountered but not configured."""
        return self._unknown_prefixes
