"""Base plugin with shared OAK adapter and caching logic."""

import csv
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from linkml.validator.plugins import ValidationPlugin  # type: ignore[import-untyped]
from linkml.validator.validation_context import ValidationContext  # type: ignore[import-untyped]
from oaklib import get_adapter
from ruamel.yaml import YAML

from linkml_term_validator.models import ValidationConfig


class BaseOntologyPlugin(ValidationPlugin):
    """Base class for ontology validation plugins.

    Provides shared functionality:
    - OAK adapter management with per-prefix adapters
    - Multi-level caching (in-memory + file-based CSV)
    - Label normalization for fuzzy matching
    - Unknown prefix tracking
    """

    def __init__(
        self,
        oak_adapter_string: str = "sqlite:obo:",
        cache_labels: bool = True,
        cache_dir: Path | str = Path("cache"),
        oak_config_path: Optional[Path | str] = None,
    ):
        """Initialize base ontology plugin.

        Args:
            oak_adapter_string: Default OAK adapter string (e.g., "sqlite:obo:")
            cache_labels: Whether to cache ontology labels to disk
            cache_dir: Directory for label cache files
            oak_config_path: Path to oak_config.yaml for per-prefix adapters
        """
        self.config = ValidationConfig(
            oak_adapter_string=oak_adapter_string,
            cache_labels=cache_labels,
            cache_dir=Path(cache_dir) if isinstance(cache_dir, str) else cache_dir,
            oak_config_path=(
                Path(oak_config_path) if isinstance(oak_config_path, str) else oak_config_path
            ),
        )

        # In-memory caches
        self._label_cache: dict[str, Optional[str]] = {}
        self._adapter_cache: dict[str, object | None] = {}
        self._unknown_prefixes: set[str] = set()

        # Load OAK config if provided
        self._oak_config: dict[str, str] = {}
        if self.config.oak_config_path and self.config.oak_config_path.exists():
            self._load_oak_config()

    def _load_oak_config(self) -> None:
        """Load OAK configuration from YAML file."""
        if self.config.oak_config_path is None:
            return
        yaml = YAML(typ="safe")
        with open(self.config.oak_config_path) as f:
            config = yaml.load(f)
            if "ontology_adapters" in config:
                self._oak_config = config["ontology_adapters"]

    def _get_prefix(self, curie: str) -> Optional[str]:
        """Extract prefix from a CURIE.

        Args:
            curie: A CURIE like "GO:0008150"

        Returns:
            The prefix (e.g., "GO") or None if invalid
        """
        if ":" not in curie:
            return None
        return curie.split(":", 1)[0]

    def _is_prefix_configured(self, prefix: str) -> bool:
        """Check if a prefix is configured in oak_config.yaml.

        Args:
            prefix: Ontology prefix (e.g., "GO")

        Returns:
            True if prefix has a non-empty adapter configured
        """
        return prefix in self._oak_config and bool(self._oak_config[prefix])

    def _get_cache_file(self, prefix: str) -> Path:
        """Get the cache file path for a prefix.

        Args:
            prefix: Ontology prefix

        Returns:
            Path to the cache CSV file
        """
        prefix_dir = self.config.cache_dir / prefix.lower()
        prefix_dir.mkdir(parents=True, exist_ok=True)
        return prefix_dir / "terms.csv"

    def _load_cache(self, prefix: str) -> dict[str, str]:
        """Load cached labels for a prefix.

        Args:
            prefix: Ontology prefix

        Returns:
            Dict mapping CURIEs to labels
        """
        cache_file = self._get_cache_file(prefix)
        if not cache_file.exists():
            return {}

        cached = {}
        with open(cache_file) as f:
            reader = csv.DictReader(f)
            for row in reader:
                cached[row["curie"]] = row["label"]
        return cached

    def _save_to_cache(self, prefix: str, curie: str, label: str) -> None:
        """Save a label to the cache.

        Args:
            prefix: Ontology prefix
            curie: Full CURIE
            label: Label to cache
        """
        cache_file = self._get_cache_file(prefix)

        # Load existing cache
        existing = self._load_cache(prefix)
        existing[curie] = label

        # Write back
        with open(cache_file, "w") as f:
            writer = csv.DictWriter(f, fieldnames=["curie", "label", "retrieved_at"])
            writer.writeheader()
            for curie, label in existing.items():
                writer.writerow(
                    {
                        "curie": curie,
                        "label": label,
                        "retrieved_at": datetime.now().isoformat(),
                    }
                )

    def _get_adapter(self, prefix: str) -> object | None:
        """Get an OAK adapter for a prefix.

        Args:
            prefix: Ontology prefix

        Returns:
            OAK adapter or None if unavailable
        """
        if prefix in self._adapter_cache:
            return self._adapter_cache[prefix]

        adapter_string = None

        if prefix in self._oak_config:
            configured = self._oak_config[prefix]
            if not configured:
                self._adapter_cache[prefix] = None
                return None
            adapter_string = configured
        elif self._oak_config:
            # oak_config is loaded but prefix not in it - don't fall back to default
            self._adapter_cache[prefix] = None
            return None
        elif self.config.oak_adapter_string == "sqlite:obo:":
            adapter_string = f"sqlite:obo:{prefix.lower()}"

        if adapter_string:
            adapter = get_adapter(adapter_string)
            self._adapter_cache[prefix] = adapter
            return adapter

        self._adapter_cache[prefix] = None
        return None

    def get_ontology_label(self, curie: str) -> Optional[str]:
        """Get the label for an ontology term.

        Uses multi-level caching: in-memory, then file, then adapter.

        Args:
            curie: A CURIE like "GO:0008150"

        Returns:
            The label or None if not found
        """
        if curie in self._label_cache:
            return self._label_cache[curie]

        prefix = self._get_prefix(curie)
        if not prefix:
            return None

        if self.config.cache_labels:
            cached = self._load_cache(prefix)
            if curie in cached:
                label = cached[curie]
                self._label_cache[curie] = label
                return label

        adapter = self._get_adapter(prefix)
        if adapter is None:
            if not self._is_prefix_configured(prefix):
                self._unknown_prefixes.add(prefix)
            self._label_cache[curie] = None
            return None

        label = adapter.label(curie)  # type: ignore[attr-defined]
        self._label_cache[curie] = label

        if label and self.config.cache_labels:
            self._save_to_cache(prefix, curie, label)

        return label

    @staticmethod
    def normalize_string(s: str) -> str:
        """Normalize a string for comparison.

        Removes punctuation and converts to lowercase.

        Args:
            s: String to normalize

        Returns:
            Normalized string
        """
        # Remove all punctuation and convert to lowercase
        normalized = re.sub(r"[^\w\s]", " ", s.lower())
        # Collapse multiple spaces
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized.strip()

    def get_unknown_prefixes(self) -> set[str]:
        """Get set of prefixes that were encountered but not configured.

        Returns:
            Set of unknown prefix strings
        """
        return self._unknown_prefixes

    def pre_process(self, context: ValidationContext) -> None:
        """Hook called before instances are processed.

        Subclasses can override to perform initialization.
        """
        pass

    def post_process(self, context: ValidationContext) -> None:
        """Hook called after instances are processed.

        Subclasses can override to perform cleanup.
        """
        pass
