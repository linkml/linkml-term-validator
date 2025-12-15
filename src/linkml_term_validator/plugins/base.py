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
import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Optional

from linkml.validator.plugins import ValidationPlugin  # type: ignore[import-untyped]
from linkml.validator.validation_context import ValidationContext  # type: ignore[import-untyped]
from linkml_runtime.linkml_model import EnumDefinition
from oaklib import get_adapter
from ruamel.yaml import YAML

from linkml_term_validator.models import CacheStrategy, ValidationConfig


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
        cache_strategy: Literal["progressive", "greedy"] | CacheStrategy = CacheStrategy.PROGRESSIVE,
    ):
        """Initialize base ontology plugin.

        Args:
            oak_adapter_string: Default OAK adapter string (e.g., "sqlite:obo:")
            cache_labels: Whether to cache ontology labels to disk
            cache_dir: Directory for label cache files
            oak_config_path: Path to oak_config.yaml for per-prefix adapters
            cache_strategy: Caching strategy for dynamic enums - "progressive" (default) or "greedy"
        """
        # Convert string to enum if needed
        if isinstance(cache_strategy, str):
            cache_strategy = CacheStrategy(cache_strategy)

        self.config = ValidationConfig(
            oak_adapter_string=oak_adapter_string,
            cache_labels=cache_labels,
            cache_dir=Path(cache_dir) if isinstance(cache_dir, str) else cache_dir,
            oak_config_path=(
                Path(oak_config_path) if isinstance(oak_config_path, str) else oak_config_path
            ),
            cache_strategy=cache_strategy,
        )

        # In-memory caches
        self._label_cache: dict[str, Optional[str]] = {}
        self._adapter_cache: dict[str, object | None] = {}
        self._enum_cache: dict[str, set[str]] = {}  # enum_name -> expanded/cached values
        self._unknown_prefixes: set[str] = set()

        # Load OAK config if provided (may override cache_strategy)
        self._oak_config: dict[str, str] = {}
        if self.config.oak_config_path and self.config.oak_config_path.exists():
            self._load_oak_config()

    @property
    def cache_strategy(self) -> CacheStrategy:
        """Get the cache strategy for dynamic enums."""
        return self.config.cache_strategy

    def _load_oak_config(self) -> None:
        """Load OAK configuration from YAML file.

        Loads ontology_adapters and optionally cache_strategy from the config file.
        """
        if self.config.oak_config_path is None:
            return
        yaml = YAML(typ="safe")
        with open(self.config.oak_config_path) as f:
            config = yaml.load(f)
            if "ontology_adapters" in config:
                self._oak_config = config["ontology_adapters"]
            # Override cache_strategy from YAML if specified
            if "cache_strategy" in config:
                strategy_str = config["cache_strategy"]
                self.config.cache_strategy = CacheStrategy(strategy_str)

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

    # =========================================================================
    # Enum Caching
    # =========================================================================

    def _get_enum_cache_key(self, enum_def: EnumDefinition) -> str:
        """Generate a cache key from enum definition.

        The key is based on the dynamic query parameters (source_nodes,
        relationship_types, include_self, traverse_up) so the cache is
        invalidated when the enum definition changes.

        Args:
            enum_def: Enum definition

        Returns:
            A hash string for cache file naming
        """
        key_parts = [enum_def.name or ""]

        if enum_def.reachable_from:
            query = enum_def.reachable_from
            key_parts.append(f"rf:{','.join(sorted(query.source_nodes or []))}")
            key_parts.append(f"rt:{','.join(sorted(query.relationship_types or []))}")
            key_parts.append(f"is:{query.include_self if hasattr(query, 'include_self') else True}")
            key_parts.append(f"tu:{query.traverse_up if hasattr(query, 'traverse_up') else False}")

        if enum_def.concepts:
            key_parts.append(f"c:{','.join(sorted(enum_def.concepts))}")

        # Create a short hash for filename
        key_string = "|".join(key_parts)
        return hashlib.md5(key_string.encode()).hexdigest()[:12]

    def _get_enum_cache_file(self, enum_name: str, cache_key: str) -> Path:
        """Get the cache file path for an enum.

        Args:
            enum_name: Name of the enum
            cache_key: Hash of the enum definition

        Returns:
            Path to the cache CSV file
        """
        enum_dir = self.config.cache_dir / "enums"
        enum_dir.mkdir(parents=True, exist_ok=True)
        # Use enum name + cache key to allow for definition changes
        safe_name = re.sub(r"[^\w\-]", "_", enum_name.lower())
        return enum_dir / f"{safe_name}_{cache_key}.csv"

    def _load_enum_cache(self, enum_def: EnumDefinition) -> Optional[set[str]]:
        """Load cached enum values if available.

        Reads a simple CSV file with header 'curie' and one CURIE per line.

        Args:
            enum_def: Enum definition

        Returns:
            Set of cached values, or None if cache miss
        """
        if not self.config.cache_labels:  # Reuse cache_labels setting
            return None

        cache_key = self._get_enum_cache_key(enum_def)
        cache_file = self._get_enum_cache_file(enum_def.name or "unknown", cache_key)

        if not cache_file.exists():
            return None

        values: set[str] = set()
        with open(cache_file) as f:
            reader = csv.DictReader(f)
            for row in reader:
                values.add(row["curie"])
        return values

    def _save_enum_cache(self, enum_def: EnumDefinition, values: set[str]) -> None:
        """Save expanded enum values to cache (greedy mode - writes all values).

        Writes a simple CSV file with header 'curie' and one CURIE per line.

        Args:
            enum_def: Enum definition
            values: Set of expanded values to cache
        """
        if not self.config.cache_labels:  # Reuse cache_labels setting
            return

        cache_key = self._get_enum_cache_key(enum_def)
        cache_file = self._get_enum_cache_file(enum_def.name or "unknown", cache_key)

        with open(cache_file, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["curie"])
            writer.writeheader()
            for curie in sorted(values):
                writer.writerow({"curie": curie})

    def _add_to_enum_cache(self, enum_def: EnumDefinition, value: str) -> None:
        """Add a single value to the enum cache (progressive mode - appends).

        Appends a single CURIE to the cache file. Creates file with header if needed.

        Args:
            enum_def: Enum definition
            value: CURIE to add to cache
        """
        if not self.config.cache_labels:
            return

        cache_key = self._get_enum_cache_key(enum_def)
        cache_file = self._get_enum_cache_file(enum_def.name or "unknown", cache_key)

        # Check if file exists (need to write header if not)
        file_exists = cache_file.exists()

        with open(cache_file, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["curie"])
            if not file_exists:
                writer.writeheader()
            writer.writerow({"curie": value})

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
    # Progressive Validation (for cache_strategy="progressive")
    # =========================================================================

    def is_value_in_enum(
        self, value: str, enum_def: EnumDefinition, schema_view: Any = None
    ) -> bool:
        """Check if a value is valid for an enum using progressive caching.

        This method is used when cache_strategy is "progressive". It:
        1. Checks the in-memory cache
        2. Checks the file cache
        3. Queries the ontology directly if not cached
        4. Caches valid values for future lookups

        Args:
            value: CURIE to validate
            enum_def: Enum definition to validate against
            schema_view: SchemaView for resolving inherited enums (optional)

        Returns:
            True if value is valid for the enum
        """
        enum_name = enum_def.name or "unknown"

        # 1. Check in-memory cache
        if enum_name in self._enum_cache and value in self._enum_cache[enum_name]:
            return True

        # 2. Check file cache
        cached = self._load_enum_cache(enum_def)
        if cached is not None:
            # Store in memory for future lookups
            self._enum_cache[enum_name] = cached
            if value in cached:
                return True

        # 3. Check static permissible values first (fast)
        if enum_def.permissible_values:
            if value in enum_def.permissible_values:
                self._enum_cache.setdefault(enum_name, set()).add(value)
                return True
            # Check meanings
            for pv in enum_def.permissible_values.values():
                if pv.meaning == value:
                    self._enum_cache.setdefault(enum_name, set()).add(value)
                    return True

        # 4. Check concepts
        if enum_def.concepts and value in enum_def.concepts:
            self._enum_cache.setdefault(enum_name, set()).add(value)
            self._add_to_enum_cache(enum_def, value)
            return True

        # 5. Query ontology for reachable_from (dynamic)
        if enum_def.reachable_from:
            if self._is_value_in_reachable_from(value, enum_def.reachable_from):
                # Valid - add to caches
                self._enum_cache.setdefault(enum_name, set()).add(value)
                self._add_to_enum_cache(enum_def, value)
                return True

        # 6. Handle inherits (recurse into parent enums)
        if enum_def.inherits and schema_view is not None:
            for parent_enum_name in enum_def.inherits:
                parent_enum = schema_view.get_enum(parent_enum_name)
                if parent_enum and self.is_value_in_enum(value, parent_enum, schema_view):
                    self._enum_cache.setdefault(enum_name, set()).add(value)
                    self._add_to_enum_cache(enum_def, value)
                    return True

        return False

    def _is_value_in_reachable_from(self, value: str, query: Any) -> bool:
        """Check if a value is within the reachable_from closure.

        Uses OAK's ancestors method to check if the value is a descendant
        (or ancestor, depending on traverse_up) of the source nodes.

        Args:
            value: CURIE to check
            query: ReachabilityQuery object

        Returns:
            True if value is within the closure
        """
        if not query.source_nodes:
            return False

        # Get prefix and adapter for the value
        prefix = self._get_prefix(value)
        if not prefix:
            return False

        adapter = self._get_adapter(prefix)
        if not adapter:
            return False

        # Check if value exists in ontology first
        label = adapter.label(value)  # type: ignore[attr-defined]
        if label is None:
            return False  # Term doesn't exist or adapter lookup failed

        predicates = query.relationship_types if query.relationship_types else ["rdfs:subClassOf"]

        # Check if value is reachable from any source node
        for source_node in query.source_nodes:
            if query.traverse_up:
                # Check if source_node is an ancestor of value
                ancestors = adapter.ancestors(value, predicates=predicates)  # type: ignore[attr-defined]
                if ancestors and source_node in ancestors:
                    return True
            else:
                # Check if value is a descendant of source_node
                # We do this by checking if source_node is an ancestor of value
                ancestors = adapter.ancestors(value, predicates=predicates)  # type: ignore[attr-defined]
                if ancestors and source_node in ancestors:
                    return True
                # Also check include_self
                include_self = query.include_self if hasattr(query, "include_self") else True
                if include_self and value == source_node:
                    return True

        return False

    # =========================================================================
    # Dynamic Enum Expansion (for cache_strategy="greedy")
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

    def expand_enum(
        self, enum_def: EnumDefinition, schema_view: Any = None, use_cache: bool = True
    ) -> set[str]:
        """Expand a dynamic enum definition to a set of allowed values.

        This method materializes dynamic enums by querying the ontology
        and collecting all terms that match the enum's constraints.
        Results are cached for performance.

        Args:
            enum_def: Enum definition to expand
            schema_view: SchemaView for resolving inherited enums (optional)
            use_cache: Whether to use file-based caching (default: True)

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
        enum_name = enum_def.name or "unknown"

        # Check in-memory cache first
        if enum_name in self._enum_cache:
            return self._enum_cache[enum_name]

        # Check file cache for dynamic enums
        if use_cache and self.is_dynamic_enum(enum_def):
            cached = self._load_enum_cache(enum_def)
            if cached is not None:
                self._enum_cache[enum_name] = cached
                return cached

        # Expand the enum
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
                    values.update(self.expand_enum(parent_enum, schema_view, use_cache))

        # Also include static permissible_values if present
        if enum_def.permissible_values:
            for pv_name, pv in enum_def.permissible_values.items():
                # Add the PV name
                values.add(pv_name)
                # Add the meaning if present
                if pv.meaning:
                    values.add(pv.meaning)

        # Cache the result
        self._enum_cache[enum_name] = values
        if use_cache and self.is_dynamic_enum(enum_def):
            self._save_enum_cache(enum_def, values)

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
