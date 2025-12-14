"""Base plugin with shared OAK adapter and caching logic.

This module provides the base class for ontology validation plugins,
including shared functionality for OAK adapter management, caching,
and dynamic enum expansion.

Example:
    >>> from linkml_term_validator.plugins import BindingValidationPlugin
    >>> plugin = BindingValidationPlugin()
    >>> plugin._get_prefix("GO:0008150")
    'GO'
    >>> plugin._get_prefix("invalid-no-colon")
    >>> plugin.normalize_string("Hello, World!")
    'hello world'
"""

import csv
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from linkml.validator.plugins import ValidationPlugin  # type: ignore[import-untyped]
from linkml.validator.validation_context import ValidationContext  # type: ignore[import-untyped]
from linkml_runtime.linkml_model import EnumDefinition
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

    # =========================================================================
    # Dynamic Enum Expansion
    # =========================================================================

    def is_dynamic_enum(self, enum_def: EnumDefinition) -> bool:
        """Check if an enum uses dynamic definition (reachable_from, matches, etc.).

        Dynamic enums are defined using ontology queries rather than static
        permissible values. They need to be expanded at validation time.

        Args:
            enum_def: Enum definition to check

        Returns:
            True if enum is dynamic (uses reachable_from, matches, concepts, etc.)

        Example:
            >>> from linkml_runtime.linkml_model import EnumDefinition
            >>> from linkml_term_validator.plugins import BindingValidationPlugin
            >>> plugin = BindingValidationPlugin()

            A static enum (only permissible_values):
            >>> static_enum = EnumDefinition(name="StaticEnum")
            >>> plugin.is_dynamic_enum(static_enum)
            False

            A dynamic enum would have reachable_from, matches, or concepts set.
        """
        return bool(
            enum_def.reachable_from
            or enum_def.matches
            or enum_def.concepts
            or enum_def.include
            or enum_def.inherits
        )

    def expand_enum(self, enum_def: EnumDefinition, schema_view: Any = None) -> set[str]:
        """Expand a dynamic enum definition to a set of allowed values.

        This method materializes dynamic enums by querying the ontology
        and collecting all terms that match the enum's constraints.

        Args:
            enum_def: Enum definition to expand
            schema_view: SchemaView for resolving inherited enums (optional)

        Returns:
            Set of allowed CURIE strings

        Example:
            >>> from linkml_runtime.linkml_model import EnumDefinition
            >>> from linkml_term_validator.plugins import BindingValidationPlugin
            >>> plugin = BindingValidationPlugin()

            Static enum with permissible values:
            >>> static = EnumDefinition(
            ...     name="TestEnum",
            ...     permissible_values={"A": {"meaning": "TEST:001"}, "B": {"meaning": "TEST:002"}}
            ... )
            >>> sorted(plugin.expand_enum(static))
            ['A', 'B', 'TEST:001', 'TEST:002']
        """
        values: set[str] = set()

        # Handle reachable_from
        if enum_def.reachable_from:
            values.update(self._expand_reachable_from(enum_def.reachable_from))

        # Handle matches
        if enum_def.matches:
            values.update(self._expand_matches(enum_def.matches))

        # Handle concepts
        if enum_def.concepts:
            values.update(enum_def.concepts)

        # Handle include (union)
        if enum_def.include:
            for include_expr in enum_def.include:
                values.update(self._expand_enum_expression(include_expr))

        # Handle minus (set difference)
        if enum_def.minus:
            for minus_expr in enum_def.minus:
                values -= self._expand_enum_expression(minus_expr)

        # Handle inherits
        if enum_def.inherits and schema_view is not None:
            for parent_enum_name in enum_def.inherits:
                parent_enum = schema_view.get_enum(parent_enum_name)
                if parent_enum:
                    values.update(self.expand_enum(parent_enum, schema_view))

        # Also include static permissible_values if present
        if enum_def.permissible_values:
            for pv_name, pv in enum_def.permissible_values.items():
                # Add the PV name
                values.add(pv_name)
                # Add the meaning if present
                if pv.meaning:
                    values.add(pv.meaning)

        return values

    def _expand_enum_expression(self, expr: Any) -> set[str]:
        """Expand an enum expression (for include/minus).

        Args:
            expr: Enum expression object

        Returns:
            Set of CURIEs
        """
        values: set[str] = set()

        if hasattr(expr, "reachable_from") and expr.reachable_from:
            values.update(self._expand_reachable_from(expr.reachable_from))

        if hasattr(expr, "matches") and expr.matches:
            values.update(self._expand_matches(expr.matches))

        if hasattr(expr, "concepts") and expr.concepts:
            values.update(expr.concepts)

        if hasattr(expr, "permissible_values") and expr.permissible_values:
            for pv_name, pv in expr.permissible_values.items():
                values.add(pv_name)
                if pv.meaning:
                    values.add(pv.meaning)

        return values

    def _expand_reachable_from(self, query: Any) -> set[str]:
        """Expand reachable_from query using OAK.

        Uses OAK's ancestors/descendants methods to traverse the ontology
        graph and collect reachable terms.

        Args:
            query: ReachabilityQuery object with source_nodes, relationship_types, etc.

        Returns:
            Set of reachable CURIEs

        Example:
            Given a simple ontology with:
            - TEST:0000001 (root)
              - TEST:0000002 (child, is_a root)

            A reachable_from query starting from TEST:0000001 would return
            its descendants (TEST:0000002) and optionally itself if include_self=True.
        """
        values: set[str] = set()

        # Get adapter for source ontology
        if not query.source_nodes:
            return values

        first_node = query.source_nodes[0]
        prefix = self._get_prefix(first_node)
        if not prefix:
            return values

        adapter = self._get_adapter(prefix)
        if not adapter:
            return values

        # Get relationship types (predicates)
        predicates = query.relationship_types if query.relationship_types else ["rdfs:subClassOf"]

        # Use OAK to get descendants or ancestors
        for source_node in query.source_nodes:
            try:
                if query.traverse_up:
                    # Get ancestors
                    ancestors_result = adapter.ancestors(  # type: ignore[attr-defined]
                        source_node,
                        predicates=predicates,
                        reflexive=query.include_self if hasattr(query, "include_self") else False,
                    )
                    if ancestors_result:
                        values.update(ancestors_result)
                else:
                    # Get descendants (default)
                    descendants_result = adapter.descendants(  # type: ignore[attr-defined]
                        source_node,
                        predicates=predicates,
                        reflexive=query.include_self if hasattr(query, "include_self") else True,
                    )
                    if descendants_result:
                        values.update(descendants_result)
            except Exception:
                # If OAK query fails, skip this source node
                pass

        return values

    def _expand_matches(self, query: Any) -> set[str]:
        """Expand matches query using pattern matching.

        Args:
            query: MatchQuery object

        Returns:
            Set of matching CURIEs

        Note:
            This is a placeholder - full implementation would require
            iterating through all terms in an ontology.
        """
        # This would require querying the ontology for all terms matching a pattern
        # For now, return empty set - this is a more advanced feature
        return set()
