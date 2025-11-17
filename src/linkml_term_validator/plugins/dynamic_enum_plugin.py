"""Plugin for validating data against dynamic enum definitions."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any, Optional

from linkml.validator.report import Severity, ValidationResult  # type: ignore[import-untyped]
from linkml.validator.validation_context import ValidationContext  # type: ignore[import-untyped]
from linkml_runtime.linkml_model import EnumDefinition

from linkml_term_validator.plugins.base import BaseOntologyPlugin


class DynamicEnumPlugin(BaseOntologyPlugin):
    """Validates data values against dynamically-defined enums.

    This plugin materializes dynamic enums (those using reachable_from, matches,
    concepts, etc.) and validates data instance values against the expanded enum.

    Example:
        # Schema
        enums:
          NeuronTypeEnum:
            reachable_from:
              source_ontology: obo:cl
              source_nodes:
                - CL:0000540  # neuron
              relationship_types:
                - rdfs:subClassOf

        # Data validation
        cell_type: CL:0000100  # ← Validates this is reachable from CL:0000540
    """

    def __init__(
        self,
        oak_adapter_string: str = "sqlite:obo:",
        cache_labels: bool = True,
        cache_dir: Path | str = Path("cache"),
        oak_config_path: Optional[Path | str] = None,
    ):
        """Initialize dynamic enum plugin.

        Args:
            oak_adapter_string: Default OAK adapter string (e.g., "sqlite:obo:")
            cache_labels: Whether to cache ontology labels to disk
            cache_dir: Directory for label cache files
            oak_config_path: Path to oak_config.yaml for per-prefix adapters
        """
        super().__init__(
            oak_adapter_string=oak_adapter_string,
            cache_labels=cache_labels,
            cache_dir=cache_dir,
            oak_config_path=oak_config_path,
        )
        self.schema_view = None
        self.expanded_enums: dict[str, set[str]] = {}

    def pre_process(self, context: ValidationContext) -> None:
        """Materialize all dynamic enums before processing instances."""
        self.schema_view = context.schema_view

        # Expand all dynamic enums
        if self.schema_view is None:
            return
        for enum_name, enum_def in self.schema_view.all_enums().items():
            if self._is_dynamic_enum(enum_def):
                self.expanded_enums[enum_name] = self._expand_enum(enum_def)

    def process(self, instance: dict, context: ValidationContext) -> Iterator[ValidationResult]:
        """Validate instance slot values against expanded dynamic enums.

        Args:
            instance: Data instance to validate
            context: Validation context

        Yields:
            ValidationResult for each validation failure
        """
        if not self.schema_view or not context.target_class:
            return

        target_class = context.target_class

        # Validate each field in the instance
        for slot_name, value in instance.items():
            # Get induced slot for this class
            try:
                slot = self.schema_view.induced_slot(slot_name, target_class)
            except (KeyError, AttributeError):
                # Slot not found in schema - let other validators handle this
                continue

            # Check if slot range is a dynamic enum
            if slot.range and slot.range in self.expanded_enums:
                yield from self._validate_enum_value(
                    slot_name=slot_name,
                    value=value,
                    enum_name=slot.range,
                    instance=instance,
                    target_class=target_class,
                )

    def _validate_enum_value(
        self,
        slot_name: str,
        value: Any,
        enum_name: str,
        instance: dict,
        target_class: str,
    ) -> Iterator[ValidationResult]:
        """Validate a slot value against a dynamic enum.

        Args:
            slot_name: Name of the slot
            value: Value to validate (may be single or list)
            enum_name: Name of the enum
            instance: Full instance being validated
            target_class: Name of the class being validated

        Yields:
            ValidationResult if value not in enum
        """
        allowed_values = self.expanded_enums[enum_name]

        # Handle multivalued slots
        values = value if isinstance(value, list) else [value]

        for val in values:
            # Skip None values
            if val is None:
                continue

            # Convert to string for comparison
            val_str = str(val)

            if val_str not in allowed_values:
                yield ValidationResult(
                    type="dynamic_enum_validation",
                    severity=Severity.ERROR,
                    message=f"Value '{val_str}' not in dynamic enum '{enum_name}' (expanded from ontology)",
                    instance=instance,
                    instantiates=target_class,
                    context=[
                        f"slot: {slot_name}",
                        f"enum: {enum_name}",
                        f"allowed_values: {len(allowed_values)} terms",
                    ],
                )

    def _is_dynamic_enum(self, enum_def: EnumDefinition) -> bool:
        """Check if enum uses dynamic definition.

        Args:
            enum_def: Enum definition to check

        Returns:
            True if enum is dynamic
        """
        return bool(
            enum_def.reachable_from
            or enum_def.matches
            or enum_def.concepts
            or enum_def.include
            or enum_def.inherits
        )

    def _expand_enum(self, enum_def: EnumDefinition) -> set[str]:
        """Expand a dynamic enum definition to a set of allowed values.

        Args:
            enum_def: Enum definition to expand

        Returns:
            Set of allowed CURIE strings
        """
        values = set()

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
        if enum_def.inherits and self.schema_view is not None:
            for parent_enum_name in enum_def.inherits:
                parent_enum = self.schema_view.get_enum(parent_enum_name)
                if parent_enum:
                    values.update(self._expand_enum(parent_enum))

        # Also include static permissible_values if present
        if enum_def.permissible_values:
            for pv_name, pv in enum_def.permissible_values.items():
                # Add the PV name
                values.add(pv_name)
                # Add the meaning if present
                if pv.meaning:
                    values.add(pv.meaning)

        return values

    def _expand_enum_expression(self, expr) -> set[str]:
        """Expand an enum expression (for include/minus).

        Args:
            expr: Enum expression object

        Returns:
            Set of CURIEs
        """
        values = set()

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

    def _expand_reachable_from(self, query) -> set[str]:
        """Expand reachable_from query using OAK.

        Args:
            query: ReachabilityQuery object

        Returns:
            Set of reachable CURIEs
        """
        values: set[str] = set()

        # Get adapter for source ontology
        # For now, use the prefix from the first source node
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

    def _expand_matches(self, query) -> set[str]:
        """Expand matches query using pattern matching.

        Args:
            query: MatchQuery object

        Returns:
            Set of matching CURIEs
        """
        # This would require querying the ontology for all terms matching a pattern
        # For now, return empty set - this is a more advanced feature
        # that would require iterating through all terms in an ontology
        return set()
