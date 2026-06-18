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
import json
import re
from pathlib import Path
from typing import Any, Literal, Optional

from linkml.validator.plugins import ValidationPlugin  # type: ignore[import-untyped]
from linkml.validator.validation_context import ValidationContext  # type: ignore[import-untyped]
from linkml_runtime.linkml_model import EnumDefinition

from linkml_term_validator.cache_utils import atomic_write_csv, locked_cache_file
from linkml_term_validator.models import CacheStrategy, ValidationConfig
from linkml_term_validator.utils import OntologyAccess, get_prefix, normalize_string


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
        cache_enum_expansions: bool = True,
        saturate_enum_caches: bool = False,
        cache_dir: Path | str = Path("cache"),
        oak_config_path: Optional[Path | str] = None,
        cache_strategy: Literal["progressive", "greedy"] | CacheStrategy = CacheStrategy.PROGRESSIVE,
    ):
        """Initialize base ontology plugin.

        Args:
            oak_adapter_string: Default OAK adapter string (e.g., "sqlite:obo:")
            cache_labels: Whether to cache ontology labels to disk
            cache_enum_expansions: Whether to cache expanded dynamic enum values to disk
            saturate_enum_caches: Whether progressive validation should materialize full enum closures
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
            cache_enum_expansions=cache_enum_expansions,
            saturate_enum_caches=saturate_enum_caches,
            cache_dir=Path(cache_dir) if isinstance(cache_dir, str) else cache_dir,
            oak_config_path=(
                Path(oak_config_path) if isinstance(oak_config_path, str) else oak_config_path
            ),
            cache_strategy=cache_strategy,
        )

        # Shared ontology access (adapter management + label caching).
        self.ontology = OntologyAccess(
            oak_adapter_string=self.config.oak_adapter_string,
            cache_labels=self.config.cache_labels,
            cache_dir=self.config.cache_dir,
            oak_config_path=self.config.oak_config_path,
        )

        # Enum-expansion caches (plugin-specific, not shared).
        self._enum_cache: dict[str, set[str]] = {}  # enum_name -> cached values
        self._closed_enum_caches: set[str] = set()  # enum_name -> cache is known complete

        # Read plugin-specific extras (cache strategy/flags) from the same
        # oak_config.yaml the ontology service already parsed.
        if self.ontology.loaded_config:
            self._load_oak_config_extras(self.ontology.loaded_config)

    @property
    def cache_strategy(self) -> CacheStrategy:
        """Get the cache strategy for dynamic enums."""
        return self.config.cache_strategy

    def _load_oak_config_extras(self, config: dict[str, Any]) -> None:
        """Apply plugin-specific overrides from the parsed oak_config.yaml.

        The ``ontology_adapters`` mapping is handled by the shared
        :class:`~linkml_term_validator.utils.OntologyAccess`. This reads the
        remaining cache-strategy keys from the same already-parsed config.

        Args:
            config: Parsed oak_config.yaml contents
        """
        if "cache_strategy" in config:
            self.config.cache_strategy = CacheStrategy(config["cache_strategy"])
        if "cache_enum_expansions" in config:
            self.config.cache_enum_expansions = self._parse_bool_config_value(
                config["cache_enum_expansions"], "cache_enum_expansions"
            )
        if "saturate_enum_caches" in config:
            self.config.saturate_enum_caches = self._parse_bool_config_value(
                config["saturate_enum_caches"], "saturate_enum_caches"
            )

    @staticmethod
    def _parse_bool_config_value(value: Any, field_name: str) -> bool:
        """Parse a boolean config value without accepting arbitrary truthy strings."""
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized == "true":
                return True
            if normalized == "false":
                return False
        raise ValueError(f"{field_name} must be a boolean or 'true'/'false', got: {value!r}")

    # =========================================================================
    # Ontology-access delegation (see linkml_term_validator.utils.OntologyAccess)
    # =========================================================================

    @staticmethod
    def _get_prefix(curie: str) -> Optional[str]:
        """Extract prefix from a CURIE (e.g., "GO" from "GO:0008150")."""
        return get_prefix(curie)

    def _is_prefix_configured(self, prefix: str) -> bool:
        """Check if a prefix has a non-empty adapter configured in oak_config."""
        return self.ontology.is_prefix_configured(prefix)

    def _get_cache_file(self, prefix: str) -> Path:
        """Get the cache file path for a prefix."""
        return self.ontology.get_cache_file(prefix)

    def _load_cache(self, prefix: str) -> dict[str, str]:
        """Load cached labels for a prefix as a CURIE -> label dict."""
        return self.ontology.load_cache(prefix)

    def _load_cache_with_timestamps(self, prefix: str) -> dict[str, dict[str, str]]:
        """Load cached labels with timestamps for a prefix."""
        return self.ontology.load_cache_with_timestamps(prefix)

    def _save_to_cache(self, prefix: str, curie: str, label: str) -> None:
        """Save a label to the cache (locked, atomic, timestamp-preserving)."""
        self.ontology.save_to_cache(prefix, curie, label)

    def _get_adapter(self, prefix: str) -> object | None:
        """Get an OAK adapter for a prefix (or None if unavailable)."""
        return self.ontology.get_adapter(prefix)

    def get_ontology_label(self, curie: str) -> Optional[str]:
        """Get the label for an ontology term using multi-level caching."""
        return self.ontology.get_label(curie)

    @staticmethod
    def normalize_string(s: str) -> str:
        """Normalize a string for comparison (lowercase, depunctuated)."""
        return normalize_string(s)

    def get_unknown_prefixes(self) -> set[str]:
        """Get set of prefixes that were encountered but not configured."""
        return self.ontology.get_unknown_prefixes()

    # =========================================================================
    # Enum Caching
    # =========================================================================

    def _get_enum_cache_key(self, enum_def: EnumDefinition) -> str:
        """Generate a cache key from enum definition.

        The key incorporates every dynamic construct that affects the expanded
        value set (reachable_from, concepts, include, minus, inherits) so the
        cache is invalidated when any of them changes.

        Args:
            enum_def: Enum definition

        Returns:
            A hash string for cache file naming
        """
        key_parts = [enum_def.name or ""]

        if enum_def.reachable_from:
            key_parts.append(f"rf:{self._reachability_key(enum_def.reachable_from)}")

        if enum_def.concepts:
            key_parts.append(f"c:{','.join(sorted(enum_def.concepts))}")

        if enum_def.matches:
            key_parts.append(f"m:{self._matches_key(enum_def.matches)}")

        if enum_def.permissible_values:
            key_parts.append(f"pv:{self._permissible_values_key(enum_def.permissible_values)}")

        # Set-operation clauses also change the expanded value set; sort so the
        # key is independent of clause ordering.
        if enum_def.include:
            key_parts.append(
                "inc:" + ",".join(sorted(self._enum_expression_key(e) for e in enum_def.include))
            )
        if enum_def.minus:
            key_parts.append(
                "min:" + ",".join(sorted(self._enum_expression_key(e) for e in enum_def.minus))
            )
        if enum_def.inherits:
            key_parts.append(f"inh:{','.join(sorted(enum_def.inherits))}")

        # Create a short hash for filename
        key_string = "|".join(key_parts)
        return hashlib.md5(key_string.encode()).hexdigest()[:12]

    @staticmethod
    def _reachable_from_include_self(query: Any) -> bool:
        """Return the effective include_self value for a reachable_from query."""
        return bool(getattr(query, "include_self", False))

    @staticmethod
    def _reachability_key(query: Any) -> str:
        """Serialize a reachable_from query deterministically for cache keys."""
        parts = [
            f"sn:{','.join(sorted(query.source_nodes or []))}",
            f"rt:{','.join(sorted(query.relationship_types or []))}",
            f"is:{BaseOntologyPlugin._reachable_from_include_self(query)}",
            f"tu:{query.traverse_up if hasattr(query, 'traverse_up') else False}",
        ]
        return "|".join(parts)

    @staticmethod
    def _matches_key(query: Any) -> str:
        """Serialize a matches query deterministically for cache keys."""
        source_ontology = getattr(query, "source_ontology", None)
        return json.dumps(
            {
                "identifier_pattern": getattr(query, "identifier_pattern", None),
                "source_ontology": str(source_ontology) if source_ontology is not None else None,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _permissible_values_key(permissible_values: Any) -> str:
        """Serialize permissible values as the expanded names and meanings."""
        parts = []
        items = permissible_values.items() if hasattr(permissible_values, "items") else permissible_values._items()
        for pv_name, pv in items:
            if isinstance(pv, dict):
                meaning = pv.get("meaning")
            else:
                meaning = getattr(pv, "meaning", None)
            parts.append([str(pv_name), str(meaning) if meaning is not None else None])
        return json.dumps(sorted(parts), separators=(",", ":"))

    def _enum_expression_key(self, expr: Any) -> str:
        """Serialize an include/minus enum expression deterministically."""
        parts: list[str] = []
        if getattr(expr, "reachable_from", None):
            parts.append(self._reachability_key(expr.reachable_from))
        if getattr(expr, "matches", None):
            parts.append(f"m:{self._matches_key(expr.matches)}")
        if getattr(expr, "concepts", None):
            parts.append(f"c:{','.join(sorted(expr.concepts))}")
        if getattr(expr, "permissible_values", None):
            parts.append(f"pv:{self._permissible_values_key(expr.permissible_values)}")
        return "(" + "|".join(parts) + ")"

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

    def _get_enum_cache_marker_file(self, enum_def: EnumDefinition) -> Path:
        """Get the completion-marker file for a fully materialized enum cache."""
        cache_key = self._get_enum_cache_key(enum_def)
        cache_file = self._get_enum_cache_file(enum_def.name or "unknown", cache_key)
        return cache_file.with_suffix(f"{cache_file.suffix}.complete")

    def _is_enum_cache_complete(self, enum_def: EnumDefinition) -> bool:
        """Check whether an enum cache is explicitly marked complete."""
        return self._get_enum_cache_marker_file(enum_def).exists()

    def _mark_enum_cache_complete(self, enum_def: EnumDefinition) -> None:
        """Mark an enum cache as a fully materialized closure."""
        marker_file = self._get_enum_cache_marker_file(enum_def)
        marker_file.write_text("complete\n")

    def _clear_enum_cache_complete_marker(self, enum_def: EnumDefinition) -> None:
        """Remove the completion marker so the cache is treated as partial."""
        marker_file = self._get_enum_cache_marker_file(enum_def)
        if marker_file.exists():
            marker_file.unlink()

    def _load_enum_cache(self, enum_def: EnumDefinition) -> Optional[set[str]]:
        """Load cached enum values if available.

        Reads a simple CSV file with header 'curie' and one CURIE per line.

        Args:
            enum_def: Enum definition

        Returns:
            Set of cached values, or None if cache miss
        """
        if not self.config.cache_enum_expansions:
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

    def _save_enum_cache(self, enum_def: EnumDefinition, values: set[str], complete: bool = True) -> None:
        """Save expanded enum values to cache (greedy mode - writes all values).

        Writes a simple CSV file with header 'curie' and one CURIE per line.

        Args:
            enum_def: Enum definition
            values: Set of expanded values to cache
        """
        if not self.config.cache_enum_expansions:
            return

        cache_key = self._get_enum_cache_key(enum_def)
        cache_file = self._get_enum_cache_file(enum_def.name or "unknown", cache_key)

        with locked_cache_file(cache_file):
            atomic_write_csv(
                cache_file,
                ["curie"],
                ({"curie": curie} for curie in sorted(values)),
            )

            if complete:
                self._mark_enum_cache_complete(enum_def)
            else:
                self._clear_enum_cache_complete_marker(enum_def)

    def _add_to_enum_cache(self, enum_def: EnumDefinition, value: str) -> None:
        """Add a single value to the enum cache (progressive mode).

        Merges the CURIE with existing cached values and rewrites the cache sorted
        by CURIE for deterministic output.

        Args:
            enum_def: Enum definition
            value: CURIE to add to cache
        """
        if not self.config.cache_enum_expansions:
            return

        cache_key = self._get_enum_cache_key(enum_def)
        cache_file = self._get_enum_cache_file(enum_def.name or "unknown", cache_key)

        with locked_cache_file(cache_file):
            existing = self._load_enum_cache(enum_def) or set()
            existing.add(value)
            self._clear_enum_cache_complete_marker(enum_def)
            atomic_write_csv(
                cache_file,
                ["curie"],
                ({"curie": curie} for curie in sorted(existing)),
            )

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
        3. Reuses complete enum caches only when they are explicitly marked
        4. Optionally saturates the cache by materializing the full enum on demand
        5. Otherwise falls back to per-value ontology checks

        Args:
            value: CURIE to validate
            enum_def: Enum definition to validate against
            schema_view: SchemaView for resolving inherited enums (optional)

        Returns:
            True if value is valid for the enum
        """
        enum_name = enum_def.name or "unknown"

        # 1. Check in-memory cache
        if enum_name in self._enum_cache:
            if value in self._enum_cache[enum_name]:
                return True
            if enum_name in self._closed_enum_caches:
                return False

        # 2. Check file cache
        cached = self._load_enum_cache(enum_def)
        if cached is not None:
            # Store in memory for future lookups
            self._enum_cache[enum_name] = cached
            if self._is_enum_cache_complete(enum_def):
                self._closed_enum_caches.add(enum_name)
                return value in cached
            if value in cached:
                return True

        # Progressive mode only treats a cache as authoritative when it carries an
        # explicit completion marker. Otherwise fall back to ontology checks or
        # opt-in saturation so legacy append-only caches remain safe.
        if (
            self.config.cache_enum_expansions
            and self.config.saturate_enum_caches
            and self.is_dynamic_enum(enum_def)
            and schema_view is not None
        ):
            expanded = self.expand_enum(enum_def, schema_view, use_cache=True)
            return value in expanded

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
        include_self = self._reachable_from_include_self(query)

        # Check if value is reachable from any source node
        for source_node in query.source_nodes:
            # Reflexive case: the source node itself.
            if include_self and value == source_node:
                return True

            if query.traverse_up:
                # value must be an ancestor of source_node (we traverse up from
                # the source), i.e. value appears among source_node's ancestors.
                ancestors = adapter.ancestors(  # type: ignore[attr-defined]
                    source_node,
                    predicates=predicates,
                    reflexive=include_self,
                )
                if ancestors and value in ancestors:
                    return True
            else:
                # value must be a descendant of source_node, i.e. source_node
                # appears among value's ancestors.
                ancestors = adapter.ancestors(  # type: ignore[attr-defined]
                    value,
                    predicates=predicates,
                    reflexive=include_self,
                )
                if ancestors and source_node in ancestors:
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

        # Check in-memory cache first, but only trust dynamic enum caches when they
        # are known complete.
        if enum_name in self._enum_cache and (
            not self.is_dynamic_enum(enum_def) or enum_name in self._closed_enum_caches
        ):
            return self._enum_cache[enum_name]

        # Check file cache for dynamic enums only when explicitly marked complete.
        if use_cache and self.is_dynamic_enum(enum_def) and self._is_enum_cache_complete(enum_def):
            cached = self._load_enum_cache(enum_def)
            if cached is not None:
                self._enum_cache[enum_name] = cached
                self._closed_enum_caches.add(enum_name)
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
        if self.is_dynamic_enum(enum_def):
            self._closed_enum_caches.add(enum_name)
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

        # Use OAK to get descendants or ancestors. A query failure is allowed to
        # propagate: expand_enum writes its cache only after a fully successful
        # expansion, so a partial/empty result is never persisted as complete
        # (see #35).
        include_self = self._reachable_from_include_self(query)
        for source_node in query.source_nodes:
            if query.traverse_up:
                # Get ancestors
                ancestors_result = adapter.ancestors(  # type: ignore[attr-defined]
                    source_node,
                    predicates=predicates,
                    reflexive=include_self,
                )
                if ancestors_result:
                    values.update(ancestors_result)
            else:
                # Get descendants (default)
                descendants_result = adapter.descendants(  # type: ignore[attr-defined]
                    source_node,
                    predicates=predicates,
                    reflexive=include_self,
                )
                if descendants_result:
                    values.update(descendants_result)

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
