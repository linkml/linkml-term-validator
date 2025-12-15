"""Plugin for validating data against dynamic enum definitions.

This module provides the DynamicEnumPlugin which validates slot values
against dynamic enums defined using ontology queries (reachable_from,
matches, concepts, etc.).

Example:
    >>> from linkml_term_validator.plugins import DynamicEnumPlugin
    >>> plugin = DynamicEnumPlugin()
    >>> plugin.expanded_enums
    {}
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal, Optional

from linkml.validator.report import Severity, ValidationResult  # type: ignore[import-untyped]
from linkml.validator.validation_context import ValidationContext  # type: ignore[import-untyped]

from linkml_term_validator.models import CacheStrategy
from linkml_term_validator.plugins.base import BaseOntologyPlugin


class DynamicEnumPlugin(BaseOntologyPlugin):
    """Validates data values against dynamically-defined enums.

    This plugin materializes dynamic enums (those using reachable_from, matches,
    concepts, etc.) and validates data instance values against the expanded enum.

    For validation via bindings (nested objects), use BindingValidationPlugin instead.
    This plugin handles direct slot ranges.

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
        cache_strategy: Literal["progressive", "greedy"] | CacheStrategy = CacheStrategy.PROGRESSIVE,
    ):
        """Initialize dynamic enum plugin.

        Args:
            oak_adapter_string: Default OAK adapter string (e.g., "sqlite:obo:")
            cache_labels: Whether to cache ontology labels to disk
            cache_dir: Directory for label cache files
            oak_config_path: Path to oak_config.yaml for per-prefix adapters
            cache_strategy: Caching strategy for dynamic enums ('progressive' or 'greedy')
        """
        super().__init__(
            oak_adapter_string=oak_adapter_string,
            cache_labels=cache_labels,
            cache_dir=cache_dir,
            oak_config_path=oak_config_path,
            cache_strategy=cache_strategy,
        )
        self.schema_view = None
        self.expanded_enums: dict[str, set[str]] = {}

    def pre_process(self, context: ValidationContext) -> None:
        """Materialize dynamic enums before processing instances.

        For greedy caching: expands all dynamic enums upfront.
        For progressive caching: skips expansion (will validate lazily).
        """
        self.schema_view = context.schema_view

        # Only expand all dynamic enums upfront for greedy caching
        if self.schema_view is None:
            return

        if self.cache_strategy == CacheStrategy.GREEDY:
            for enum_name, enum_def in self.schema_view.all_enums().items():
                if self.is_dynamic_enum(enum_def):
                    self.expanded_enums[enum_name] = self.expand_enum(enum_def, self.schema_view)

    def process(self, instance: dict, context: ValidationContext) -> Iterator[ValidationResult]:
        """Validate instance slot values against dynamic enums.

        For progressive mode: validates lazily using ontology lookup.
        For greedy mode: validates against pre-expanded enum values.

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

            # Check if slot range is an enum
            if not slot.range:
                continue

            enum_def = self.schema_view.get_enum(slot.range)
            if not enum_def or not self.is_dynamic_enum(enum_def):
                continue

            # For greedy mode, use pre-expanded values
            if self.cache_strategy == CacheStrategy.GREEDY and slot.range in self.expanded_enums:
                yield from self._validate_enum_value_greedy(
                    slot_name=slot_name,
                    value=value,
                    enum_name=slot.range,
                    instance=instance,
                    target_class=target_class,
                )
            else:
                # Progressive mode: validate lazily
                yield from self._validate_enum_value_progressive(
                    slot_name=slot_name,
                    value=value,
                    enum_def=enum_def,
                    instance=instance,
                    target_class=target_class,
                )

    def _validate_enum_value_greedy(
        self,
        slot_name: str,
        value: Any,
        enum_name: str,
        instance: dict,
        target_class: str,
    ) -> Iterator[ValidationResult]:
        """Validate a slot value against a pre-expanded dynamic enum (greedy mode).

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

    def _validate_enum_value_progressive(
        self,
        slot_name: str,
        value: Any,
        enum_def: Any,
        instance: dict,
        target_class: str,
    ) -> Iterator[ValidationResult]:
        """Validate a slot value against a dynamic enum using progressive caching.

        Args:
            slot_name: Name of the slot
            value: Value to validate (may be single or list)
            enum_def: EnumDefinition object
            instance: Full instance being validated
            target_class: Name of the class being validated

        Yields:
            ValidationResult if value not in enum
        """
        # Handle multivalued slots
        values = value if isinstance(value, list) else [value]

        for val in values:
            # Skip None values
            if val is None:
                continue

            # Convert to string for comparison
            val_str = str(val)

            # Use progressive validation (checks cache, then ontology, adds to cache if valid)
            if not self.is_value_in_enum(val_str, enum_def, self.schema_view):
                yield ValidationResult(
                    type="dynamic_enum_validation",
                    severity=Severity.ERROR,
                    message=f"Value '{val_str}' not in dynamic enum '{enum_def.name}' (expanded from ontology)",
                    instance=instance,
                    instantiates=target_class,
                    context=[
                        f"slot: {slot_name}",
                        f"enum: {enum_def.name}",
                        "validation: progressive (lazy)",
                    ],
                )
