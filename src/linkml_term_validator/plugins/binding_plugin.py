"""Plugin for validating bindings in nested objects.

This module provides the BindingValidationPlugin which validates data against
binding constraints on nested object fields. Bindings allow restricting specific
fields within complex objects to enum values, including dynamic enums defined
using ontology queries.

Example:
    Basic plugin usage:

    >>> from linkml_term_validator.plugins import BindingValidationPlugin
    >>> plugin = BindingValidationPlugin(strict=True)
    >>> plugin.strict
    True
    >>> plugin.validate_labels
    True
    >>> plugin.expanded_enums
    {}

    The plugin validates bindings defined in schemas like:

    .. code-block:: yaml

        classes:
          Annotation:
            slots:
              - term
            slot_usage:
              term:
                range: Term
                bindings:
                  - binds_value_of: id
                    range: GOTermEnum

    For dynamic enums (reachable_from), the plugin expands the ontology
    query and validates that values fall within the closure.
"""

from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, Literal, Optional

from linkml.validator.report import ValidationResult  # type: ignore[import-untyped]
from linkml.validator.validation_context import ValidationContext  # type: ignore[import-untyped]

from linkml_term_validator.models import CacheStrategy, ErrorMode
from linkml_term_validator.plugins.base import BaseOntologyPlugin, SeverityOverrides
from linkml_term_validator.utils import not4curation_message

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
        validate_labels: bool = True,
        strict: bool = True,
        cache_labels: bool = True,
        cache_enum_expansions: bool = True,
        saturate_enum_caches: bool = False,
        cache_dir: Path | str = Path("cache"),
        oak_config_path: Optional[Path | str] = None,
        cache_strategy: Literal["progressive", "greedy"] | CacheStrategy = CacheStrategy.PROGRESSIVE,
        offline: bool = False,
        severity_overrides: Optional[SeverityOverrides] = None,
        check_not4curation: bool = True,
        not4curation_markers: Optional[Iterable[str]] = None,
    ):
        """Initialize binding validation plugin.

        Args:
            oak_adapter_string: Default OAK adapter string (e.g., "sqlite:obo:")
            validate_labels: If True (default), also validate that labels match ontology
            strict: If True (default), fail when term IDs are not found in configured ontologies
            cache_labels: Whether to cache ontology labels to disk
            cache_enum_expansions: Whether to cache expanded dynamic enum values to disk
            saturate_enum_caches: Whether progressive validation should materialize full enum closures
            cache_dir: Directory for label cache files
            oak_config_path: Path to oak_config.yaml for per-prefix adapters
            cache_strategy: Caching strategy for dynamic enums ('progressive' or 'greedy')
            offline: If True, force offline validation: never build OAK adapters
                and resolve everything exclusively from the file cache
            severity_overrides: Mapping of ErrorMode to severity, e.g.
                ``{"binding_label_mismatch": "WARN"}`` to make label
                disagreements advisory rather than the default hard failure
            check_not4curation: If True (default), flag a bound term whose
                ontology marks it as not for annotation, e.g. with a
                ``Not4Curation`` synonym (#70). ``binding_not4curation``
                defaults to ERROR; demote it via ``severity_overrides``
            not4curation_markers: Custom marker substrings; None uses defaults
        """
        super().__init__(
            oak_adapter_string=oak_adapter_string,
            cache_labels=cache_labels,
            cache_enum_expansions=cache_enum_expansions,
            saturate_enum_caches=saturate_enum_caches,
            cache_dir=cache_dir,
            oak_config_path=oak_config_path,
            cache_strategy=cache_strategy,
            offline=offline,
            severity_overrides=severity_overrides,
            check_not4curation=check_not4curation,
            not4curation_markers=not4curation_markers,
        )
        self.validate_labels = validate_labels
        self.strict = strict
        self.schema_view = None
        # Map (class_name, slot_name) -> [EnumBinding]
        self.bindings_map: dict[tuple[str, str], list] = {}
        # Map class_name -> {slot_name: set of properties (from implements and slot_uri)}
        self.slot_properties_map: dict[str, dict[str, set[str]]] = {}
        # Map enum_name -> set of expanded values (for dynamic enums)
        self.expanded_enums: dict[str, set[str]] = {}
        # Set of all class names in the current schema
        self._class_names: set[str] = set()

    def pre_process(self, context: ValidationContext) -> None:
        """Extract all bindings, slot properties, and optionally expand dynamic enums.

        This method is called before processing any instances. It:
        1. Collects all binding constraints from the schema
        2. Collects slot properties for label detection
        3. For greedy caching: expands all dynamic enums referenced by bindings upfront
        4. For progressive caching: skips expansion (will validate lazily)
        """
        self.schema_view = context.schema_view

        # Walk schema and collect all bindings and slot properties
        if self.schema_view is None:
            return

        # Track which enums are referenced by bindings
        self._referenced_enums: set[str] = set()
        all_classes = list(self.schema_view.all_classes().values())
        self._class_names = {cls.name for cls in all_classes}

        for cls in all_classes:
            class_properties: dict[str, set[str]] = {}
            for slot in self.schema_view.class_induced_slots(cls.name):
                if slot.bindings:
                    key = (cls.name, slot.name)
                    self.bindings_map[key] = slot.bindings
                    # Track referenced enums
                    for binding in slot.bindings:
                        if binding.range:
                            self._referenced_enums.add(binding.range)
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

        # Only expand enums upfront for greedy caching strategy
        if self.cache_strategy == CacheStrategy.GREEDY:
            for enum_name in self._referenced_enums:
                enum_def = self.schema_view.get_enum(enum_name)
                if not (enum_def and self.is_dynamic_enum(enum_def)):
                    continue
                # Offline, skip pre-expanding an un-materialized enum so
                # _validate_against_enum falls back to per-value validation and
                # surfaces the clear "not materialized" diagnostic (nothing cached).
                if self._offline_skip_pre_expansion(enum_def):
                    continue
                self.expanded_enums[enum_name] = self.expand_enum(enum_def, self.schema_view)

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
                if nested_class in self._class_names:
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
                    severity=self.severity_for(ErrorMode.BINDING_VALIDATION),
                    message=f"Required binding field '{field_path}' not found at {path}",
                    instance=instance,
                    instantiates=target_class,
                    context=[f"path: {path}", f"slot: {slot_name}", f"binding: {binding.range}"],
                )
            return

        # Validate against the enum range
        enum_results: list[ValidationResult] = []
        if binding.range:
            enum_results = list(
                self._validate_against_enum(
                    field_value=field_value,
                    enum_name=binding.range,
                    field_path=field_path,
                    slot_name=slot_name,
                    instance=instance,
                    target_class=target_class,
                    path=path,
                )
            )
            yield from enum_results

        # A term the enum accepted may still be one its ontology says not to
        # annotate with (a Not4Curation synonym; see #70). This is checked only
        # when the enum check passed: a value already rejected as out-of-enum
        # needs no second result telling the user not to use it.
        if (
            not enum_results
            and isinstance(field_value, str)
            and self._not4curation_in_scope(field_value, binding.range)
        ):
            yield from self._validate_not4curation(
                field_value=field_value,
                field_path=field_path,
                slot_name=slot_name,
                instance=instance,
                target_class=target_class,
                path=path,
            )

        # Check term existence for configured prefixes (strict mode). Offline mode
        # always checks existence so an uncached term can't pass silently (#51).
        if (self.strict or self.config.offline) and isinstance(field_value, str):
            yield from self._validate_term_exists(
                field_value=field_value,
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
        """Validate field value against enum (static or dynamic).

        For static enums, validates against permissible values.
        For dynamic enums (reachable_from, matches, concepts):
        - Progressive mode: validates lazily using ontology lookup
        - Greedy mode: validates against pre-expanded set

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

        is_dynamic = self.is_dynamic_enum(enum_def)

        if is_dynamic:
            # Use the greedy pre-expanded set when the enum was materialized;
            # otherwise validate per value. The per-value branch covers progressive
            # mode AND greedy dynamic enums that were not pre-expanded (e.g. an
            # un-materialized enum offline), where it surfaces the clear offline
            # diagnostic instead of silently passing via the static path below.
            if enum_name in self.expanded_enums:
                valid_values = self.expanded_enums[enum_name]
                if field_value not in valid_values:
                    yield ValidationResult(
                        type="binding_validation",
                        severity=self.severity_for(ErrorMode.BINDING_VALIDATION),
                        message=f"Value '{field_value}' not in dynamic enum '{enum_name}' (expanded from ontology)",
                        instance=instance,
                        instantiates=target_class,
                        context=[
                            f"path: {path}",
                            f"slot: {slot_name}",
                            f"field: {field_path}",
                            f"allowed_values: {len(valid_values)} terms",
                        ],
                    )
                return

            is_valid = self.is_value_in_enum(field_value, enum_def, self.schema_view)
            if not is_valid:
                # Offline with an unmaterialized dynamic enum is a cache problem,
                # not a data error - report it as such rather than "not in enum".
                if self._offline_dynamic_enum_unmaterialized(enum_def):
                    message = self._offline_unmaterialized_enum_message(field_value, enum_name)
                    validation_note = "validation: offline (enum cache not materialized)"
                else:
                    message = (
                        f"Value '{field_value}' not in dynamic enum "
                        f"'{enum_name}' (expanded from ontology)"
                    )
                    validation_note = "validation: progressive (lazy)"
                yield ValidationResult(
                    type="binding_validation",
                    severity=self.severity_for(ErrorMode.BINDING_VALIDATION),
                    message=message,
                    instance=instance,
                    instantiates=target_class,
                    context=[
                        f"path: {path}",
                        f"slot: {slot_name}",
                        f"field: {field_path}",
                        validation_note,
                    ],
                )
            return

        # Static enum: validate against permissible values
        valid_values = set()
        if enum_def.permissible_values:
            # Add PV names
            valid_values.update(enum_def.permissible_values.keys())
            # Add meanings
            for pv in enum_def.permissible_values.values():
                if pv.meaning:
                    valid_values.add(pv.meaning)

        # Skip validation if no values defined
        if not valid_values:
            return

        # Check if value is valid
        if field_value not in valid_values:
            yield ValidationResult(
                type="binding_validation",
                severity=self.severity_for(ErrorMode.BINDING_VALIDATION),
                message=f"Value '{field_value}' not in enum '{enum_name}'",
                instance=instance,
                instantiates=target_class,
                context=[
                    f"path: {path}",
                    f"slot: {slot_name}",
                    f"field: {field_path}",
                    f"allowed_values: {len(valid_values)} terms",
                ],
            )

    def _validate_term_exists(
        self,
        field_value: str,
        field_path: str,
        slot_name: str,
        instance: dict,
        target_class: str,
        path: str = "",
    ) -> Iterator[ValidationResult]:
        """Validate that a term ID exists in the ontology.

        Only validates terms with prefixes that are configured in oak_config.yaml.
        Terms with unknown prefixes are skipped (handled separately via unknown prefix warnings).

        Args:
            field_value: CURIE to check (e.g., "HP:0000001")
            field_path: Path to the field within the binding
            slot_name: Name of the slot
            instance: Full instance
            target_class: Name of the class
            path: JSON path to this location

        Yields:
            ValidationResult if term not found in configured ontology
        """
        prefix = self._get_prefix(field_value)
        if not prefix:
            return

        # Only check existence for configured prefixes. In offline mode every term
        # must be resolvable from the cache regardless of prefix configuration, so
        # unconfigured prefixes are checked too (see issue #51).
        if not self._is_prefix_configured(prefix) and not self.config.offline:
            return

        # Try to get the label - if None, term doesn't exist
        ontology_label = self.get_ontology_label(field_value)
        if ontology_label is None:
            if self.config.offline:
                message = f"Term '{field_value}' not found in offline cache"
                prefix_context = f"prefix: {prefix} (offline: cache-only)"
            else:
                message = f"Term '{field_value}' not found in ontology"
                prefix_context = f"prefix: {prefix} (configured in oak_config)"
            yield ValidationResult(
                type="term_not_found",
                severity=self.severity_for(ErrorMode.TERM_NOT_FOUND),
                message=message,
                instance=instance,
                instantiates=target_class,
                context=[
                    f"path: {path}",
                    f"slot: {slot_name}",
                    f"field: {field_path}",
                    prefix_context,
                ],
            )

    def _not4curation_in_scope(self, field_value: str, enum_name: Optional[str]) -> bool:
        """Whether the Not4Curation check may consult an adapter for this term.

        The check reads synonyms through an OAK adapter, and building one can
        download an ontology. It therefore runs only where the pipeline would
        already have resolved the term: the binding's enum is dynamic (an
        adapter was needed for membership), the term's prefix has a configured
        adapter (existence is checked for it), or validation is offline (no
        adapter is ever built, and the term is recorded as unchecked). A term
        bound to a static enum under a prefix nobody configured is outside the
        validator's ontology scope, exactly as it is for the existence check.
        """
        if self.config.offline:
            return True
        prefix = self._get_prefix(field_value)
        if prefix and self._is_prefix_configured(prefix):
            return True
        if enum_name and self.schema_view is not None:
            enum_def = self.schema_view.get_enum(enum_name)
            if enum_def is not None and self.is_dynamic_enum(enum_def):
                return True
        return False

    def _validate_not4curation(
        self,
        field_value: str,
        field_path: str,
        slot_name: str,
        instance: dict,
        target_class: str,
        path: str = "",
    ) -> Iterator[ValidationResult]:
        """Report a bound term its ontology marks as not for annotation.

        The term exists, is current, and passed the enum check; only a synonym
        (``Not4Curation``, ``not_recommended_for_annotation``) says not to use
        it. Nothing else in the pipeline reads synonyms, and a positive enum
        cache hit never will, so this is the one place the flag can surface.

        Args:
            field_value: CURIE that passed the binding's enum check
            field_path: Path to the field within the binding
            slot_name: Name of the slot
            instance: Full instance
            target_class: Name of the class
            path: JSON path to this location

        Yields:
            One ``binding_not4curation`` result if the term is flagged
        """
        markers = self.not4curation_markers_for(field_value)
        if not markers:
            return
        yield ValidationResult(
            type="binding_not4curation",
            severity=self.severity_for(ErrorMode.BINDING_NOT4CURATION),
            message=not4curation_message(field_value, markers),
            instance=instance,
            instantiates=target_class,
            context=[
                f"path: {path}",
                f"slot: {slot_name}",
                f"field: {field_path}",
                f"markers: {', '.join(markers)}",
            ],
        )

    @staticmethod
    def _is_absent_label(provided_label: Any) -> bool:
        """Report whether a label value means "no label supplied".

        YAML and JSON round-trips routinely materialize an optional slot as an
        explicit null, and an optional *multivalued* slot as an empty list or a
        list of nulls. None of those are malformed labels -- there is simply
        nothing to compare against the ontology, exactly as if the key had been
        omitted. Failing on them would break pipelines that never opted into
        label checking.

        An empty string is deliberately *not* absent: unlike null, nothing
        produces it mechanically, so it reads as a real (and actionable) label
        defect rather than a missing value.

        Args:
            provided_label: The raw value found in the label field

        Returns:
            True if the value should be treated as no label at all

        Examples:
            >>> BindingValidationPlugin._is_absent_label(None)
            True
            >>> BindingValidationPlugin._is_absent_label([])
            True
            >>> BindingValidationPlugin._is_absent_label([None, None])
            True
            >>> BindingValidationPlugin._is_absent_label("")
            False
            >>> BindingValidationPlugin._is_absent_label(["cell cycle"])
            False
        """
        if provided_label is None:
            return True
        if isinstance(provided_label, list):
            return all(item is None for item in provided_label)
        return False

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
            if self._is_absent_label(provided_label):
                continue

            result_context = [
                f"path: {path}",
                f"slot: {slot_name}",
                f"label_field: {label_field}",
                f"curie: {field_value}",
            ]

            # Shape is checked before the ontology is consulted. "This is not a
            # string" is a defect in the data alone, so it must not depend on
            # whether the term's prefix happens to be configured -- otherwise the
            # same file passes or fails based on cache state.
            if isinstance(provided_label, str):
                provided_labels = [provided_label]
            elif isinstance(provided_label, list):
                # Reached only when the list holds something non-null and
                # non-string; the all-null and empty cases are absent labels.
                provided_labels = [label for label in provided_label if isinstance(label, str)]
                if not provided_labels:
                    yield ValidationResult(
                        type="binding_label_invalid",
                        severity=self.severity_for(ErrorMode.BINDING_LABEL_INVALID),
                        message=(
                            f"Label field '{label_field}' for '{field_value}' must contain "
                            f"a string label, got '{provided_label}'"
                        ),
                        instance=instance,
                        instantiates=target_class,
                        context=result_context,
                    )
                    continue
            else:
                yield ValidationResult(
                    type="binding_label_invalid",
                    severity=self.severity_for(ErrorMode.BINDING_LABEL_INVALID),
                    message=(
                        f"Label field '{label_field}' for '{field_value}' must be a string "
                        f"or list of strings, got {type(provided_label).__name__}"
                    ),
                    instance=instance,
                    instantiates=target_class,
                    context=result_context,
                )
                continue

            # The mismatch check genuinely needs the ontology; if the term does
            # not resolve there is nothing to compare against. Term existence is
            # reported separately by _validate_term_exists.
            ontology_label = self.get_ontology_label(field_value)
            if not ontology_label:
                continue

            normalized_ontology = self.normalize_string(ontology_label)

            if not any(
                self.normalize_string(label) == normalized_ontology for label in provided_labels
            ):
                yield ValidationResult(
                    type="binding_label_mismatch",
                    severity=self.severity_for(ErrorMode.BINDING_LABEL_MISMATCH),
                    message=f"Label mismatch for '{field_value}': expected '{ontology_label}', got '{provided_label}'",
                    instance=instance,
                    instantiates=target_class,
                    context=result_context,
                )
