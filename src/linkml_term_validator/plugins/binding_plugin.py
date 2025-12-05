"""Plugin for validating bindings in nested objects."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any, Optional

from linkml.validator.report import Severity, ValidationResult  # type: ignore[import-untyped]
from linkml.validator.validation_context import ValidationContext  # type: ignore[import-untyped]

from linkml_term_validator.plugins.base import BaseOntologyPlugin

# Ontology properties that represent labels
LABEL_PROPERTIES = {
    "rdfs:label",
    "skos:prefLabel",
    "schema:name",
    "oboInOwl:hasExactSynonym",
}

# Properties that represent alternative labels (for fuzzy matching)
ALT_LABEL_PROPERTIES = {
    "skos:altLabel",
    "oboInOwl:hasRelatedSynonym",
    "oboInOwl:hasBroadSynonym",
    "oboInOwl:hasNarrowSynonym",
}


class BindingValidationPlugin(BaseOntologyPlugin):
    """Validates binding constraints on nested object fields.

    Bindings allow restricting specific fields within complex objects to
    enum values. This plugin validates that bound fields contain allowed values.

    Example:
        # Schema
        classes:
          Annotation:
            slots:
              - term
            slot_usage:
              term:
                range: Term  # Complex object: {id: ..., label: ...}
                bindings:
                  - binds_value_of: id
                    range: GOTermEnum
                    obligation_level: REQUIRED

        # Data validation
        term:
          id: GO:0008150          # ← Validates against GOTermEnum
          label: biological process  # ← Optionally validates label matches ontology
    """

    def __init__(
        self,
        oak_adapter_string: str = "sqlite:obo:",
        validate_labels: bool = False,
        cache_labels: bool = True,
        cache_dir: Path | str = Path("cache"),
        oak_config_path: Optional[Path | str] = None,
    ):
        """Initialize binding validation plugin.

        Args:
            oak_adapter_string: Default OAK adapter string (e.g., "sqlite:obo:")
            validate_labels: If True, also validate that labels match ontology
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
        self.validate_labels = validate_labels
        self.schema_view = None
        # Map (class_name, slot_name) -> [EnumBinding]
        self.bindings_map: dict[tuple[str, str], list] = {}
        # Map class_name -> {slot_name: set of properties (from implements and slot_uri)}
        self.slot_properties_map: dict[str, dict[str, set[str]]] = {}

    def pre_process(self, context: ValidationContext) -> None:
        """Extract all bindings and slot properties from schema before processing."""
        self.schema_view = context.schema_view

        # Walk schema and collect all bindings and slot properties
        if self.schema_view is None:
            return
        for cls in self.schema_view.all_classes().values():
            class_properties: dict[str, set[str]] = {}
            for slot in self.schema_view.class_induced_slots(cls.name):
                if slot.bindings:
                    key = (cls.name, slot.name)
                    self.bindings_map[key] = slot.bindings
                # Collect implements and slot_uri for label detection
                slot_props: set[str] = set()
                if slot.implements:
                    slot_props.update(slot.implements)
                if slot.slot_uri:
                    slot_props.add(slot.slot_uri)
                if slot_props:
                    class_properties[slot.name] = slot_props
            if class_properties:
                self.slot_properties_map[cls.name] = class_properties

    def process(self, instance: dict, context: ValidationContext) -> Iterator[ValidationResult]:
        """Validate binding constraints on nested fields.

        Recursively walks the instance structure to validate bindings at all
        nesting levels, not just the top-level target class.

        Args:
            instance: Data instance to validate
            context: Validation context

        Yields:
            ValidationResult for each binding violation
        """
        if not self.schema_view or not context.target_class:
            return

        yield from self._process_recursive(
            instance=instance,
            current_class=context.target_class,
            path="",
            root_instance=instance,
        )

    def _process_recursive(
        self,
        instance: Any,
        current_class: str,
        path: str,
        root_instance: dict,
    ) -> Iterator[ValidationResult]:
        """Recursively validate bindings at all nesting levels.

        Args:
            instance: Current object being validated
            current_class: LinkML class name for this object
            path: JSON path to current location (e.g., "disease_term.term")
            root_instance: The top-level instance (for error reporting)

        Yields:
            ValidationResult for each binding violation
        """
        if not isinstance(instance, dict) or self.schema_view is None:
            return

        # Check each slot in the current instance
        for slot_name, value in instance.items():
            slot_path = f"{path}.{slot_name}" if path else slot_name

            # Check if this slot has bindings
            key = (current_class, slot_name)
            if key in self.bindings_map:
                # Handle multivalued slots
                values = value if isinstance(value, list) else [value]

                for i, val in enumerate(values):
                    if val is None:
                        continue

                    item_path = f"{slot_path}[{i}]" if isinstance(value, list) else slot_path

                    # Validate each binding constraint
                    for binding in self.bindings_map[key]:
                        yield from self._validate_binding(
                            value=val,
                            binding=binding,
                            slot_name=slot_name,
                            instance=root_instance,
                            target_class=current_class,
                            path=item_path,
                        )

            # Recurse into nested objects
            slot_def = self._get_slot_definition(current_class, slot_name)
            if slot_def and slot_def.range:
                nested_class = slot_def.range
                # Only recurse if the range is a class (not a type like string)
                if nested_class in [c.name for c in self.schema_view.all_classes().values()]:
                    values = value if isinstance(value, list) else [value]
                    for i, val in enumerate(values):
                        if isinstance(val, dict):
                            item_path = f"{slot_path}[{i}]" if isinstance(value, list) else slot_path
                            yield from self._process_recursive(
                                instance=val,
                                current_class=nested_class,
                                path=item_path,
                                root_instance=root_instance,
                            )

    def _get_slot_definition(self, class_name: str, slot_name: str) -> Optional[Any]:
        """Get the slot definition for a class.

        Args:
            class_name: Name of the class
            slot_name: Name of the slot

        Returns:
            SlotDefinition or None
        """
        if self.schema_view is None:
            return None
        for slot in self.schema_view.class_induced_slots(class_name):
            if slot.name == slot_name:
                return slot
        return None

    def _validate_binding(
        self,
        value: Any,
        binding: Any,
        slot_name: str,
        instance: dict,
        target_class: str,
        path: str = "",
    ) -> Iterator[ValidationResult]:
        """Validate a single binding constraint.

        Args:
            value: Value to validate (may be complex object)
            binding: EnumBinding object
            slot_name: Name of the slot
            instance: Full instance being validated
            target_class: Name of the class being validated
            path: JSON path to this location (for error messages)

        Yields:
            ValidationResult for each violation
        """
        # Extract the field specified by binds_value_of
        field_path = binding.binds_value_of
        field_value = self._extract_field(value, field_path)

        # Check if field is required but missing
        if field_value is None:
            obligation_level = getattr(binding, "obligation_level", None)
            if obligation_level == "REQUIRED":
                yield ValidationResult(
                    type="binding_validation",
                    severity=Severity.ERROR,
                    message=f"Required binding field '{field_path}' not found at {path}",
                    instance=instance,
                    instantiates=target_class,
                    context=[f"path: {path}", f"slot: {slot_name}", f"binding: {binding.range}"],
                )
            return

        # Validate against the enum range
        if binding.range:
            yield from self._validate_against_enum(
                field_value=field_value,
                enum_name=binding.range,
                field_path=field_path,
                slot_name=slot_name,
                instance=instance,
                target_class=target_class,
                path=path,
            )

        # Optionally validate label matches ontology
        if self.validate_labels and isinstance(value, dict):
            # Get the range class to find label slots
            range_class = self._get_binding_range_class(binding, slot_name)
            yield from self._validate_label(
                value=value,
                field_value=field_value,
                slot_name=slot_name,
                instance=instance,
                target_class=target_class,
                range_class=range_class,
                path=path,
            )

    def _get_binding_range_class(self, binding: Any, slot_name: str) -> Optional[str]:
        """Get the range class for a binding's slot.

        Args:
            binding: EnumBinding object
            slot_name: Name of the slot with the binding

        Returns:
            Range class name or None
        """
        if self.schema_view is None:
            return None
        # The slot's range tells us what class the nested object is
        for cls in self.schema_view.all_classes().values():
            for slot in self.schema_view.class_induced_slots(cls.name):
                if slot.name == slot_name and slot.bindings:
                    return slot.range
        return None

    def _find_label_slots(self, class_name: Optional[str]) -> list[str]:
        """Find slots that implement label properties.

        Uses slot.implements or slot.slot_uri to detect fields that should
        contain labels. Falls back to convention (field named 'label') if
        no label property declaration found.

        Args:
            class_name: Name of the class to check

        Returns:
            List of slot names that implement label properties
        """
        label_slots = []

        if class_name and class_name in self.slot_properties_map:
            class_slots = self.slot_properties_map[class_name]
            for slot_name, properties in class_slots.items():
                if properties & LABEL_PROPERTIES:
                    label_slots.append(slot_name)

        # Fall back to convention if no label property found
        if not label_slots:
            label_slots = ["label"]

        return label_slots

    def _extract_field(self, value: Any, field_path: str) -> Optional[Any]:
        """Extract a field from a value using a path.

        Args:
            value: Value to extract from (dict, object, etc.)
            field_path: Path to field (e.g., "id", "extensions.0.value")

        Returns:
            Extracted value or None
        """
        if not isinstance(value, dict):
            return None

        # Simple case: direct field access
        if field_path in value:
            return value[field_path]

        # Complex case: nested path (e.g., "extensions.0.value")
        # For now, just support simple field access
        # TODO: Implement full path navigation
        return None

    def _validate_against_enum(
        self,
        field_value: str,
        enum_name: str,
        field_path: str,
        slot_name: str,
        instance: dict,
        target_class: str,
        path: str = "",
    ) -> Iterator[ValidationResult]:
        """Validate field value against enum.

        Args:
            field_value: Value to validate
            enum_name: Name of the enum to validate against
            field_path: Path to the field within the binding
            slot_name: Name of the slot
            instance: Full instance
            target_class: Name of the class
            path: JSON path to this location

        Yields:
            ValidationResult if value not in enum
        """
        if self.schema_view is None:
            return
        enum_def = self.schema_view.get_enum(enum_name)
        if not enum_def:
            return

        # Skip validation for dynamic enums (those with reachable_from constraints)
        # Dynamic enums are validated by the DynamicEnumPlugin instead
        if enum_def.reachable_from or getattr(enum_def, 'matches', None) or getattr(enum_def, 'concepts', None):
            return

        # Get permissible values
        valid_values = set()

        if enum_def.permissible_values:
            # Add PV names
            valid_values.update(enum_def.permissible_values.keys())

            # Add meanings
            for pv in enum_def.permissible_values.values():
                if pv.meaning:
                    valid_values.add(pv.meaning)

        # Skip validation if no permissible values defined (likely a dynamic enum)
        if not valid_values:
            return

        # Check if value is valid
        if field_value not in valid_values:
            yield ValidationResult(
                type="binding_validation",
                severity=Severity.ERROR,
                message=f"Value '{field_value}' not in enum '{enum_name}'",
                instance=instance,
                instantiates=target_class,
                context=[
                    f"path: {path}",
                    f"slot: {slot_name}",
                    f"field: {field_path}",
                    f"allowed_values: {len(valid_values)} values",
                ],
            )

    def _validate_label(
        self,
        value: dict,
        field_value: str,
        slot_name: str,
        instance: dict,
        target_class: str,
        range_class: Optional[str] = None,
        path: str = "",
    ) -> Iterator[ValidationResult]:
        """Validate that label field matches ontology.

        Detects label fields using slot.implements (e.g., implements: [rdfs:label])
        or slot.slot_uri (e.g., slot_uri: rdfs:label).
        Falls back to convention (field named 'label') if no declaration found.

        Args:
            value: Dict containing the label
            field_value: CURIE to check label for
            slot_name: Name of the slot
            instance: Full instance
            target_class: Name of the class
            range_class: Name of the nested object's class (for implements lookup)
            path: JSON path to this location

        Yields:
            ValidationResult if label doesn't match
        """
        # Find which fields should be validated as labels
        label_slots = self._find_label_slots(range_class)

        for label_field in label_slots:
            if label_field not in value:
                continue

            provided_label = value[label_field]
            ontology_label = self.get_ontology_label(field_value)

            if ontology_label:
                normalized_provided = self.normalize_string(provided_label)
                normalized_ontology = self.normalize_string(ontology_label)

                if normalized_provided != normalized_ontology:
                    yield ValidationResult(
                        type="binding_label_mismatch",
                        severity=Severity.WARN,
                        message=f"Label mismatch for '{field_value}': expected '{ontology_label}', got '{provided_label}'",
                        instance=instance,
                        instantiates=target_class,
                        context=[
                            f"path: {path}",
                            f"slot: {slot_name}",
                            f"label_field: {label_field}",
                            f"curie: {field_value}",
                        ],
                    )
