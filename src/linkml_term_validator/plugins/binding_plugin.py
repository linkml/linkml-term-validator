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
import re
from typing import Any, Literal, NamedTuple, Optional

from linkml.validator.report import Severity, ValidationResult  # type: ignore[import-untyped]
from linkml.validator.validation_context import ValidationContext  # type: ignore[import-untyped]

from linkml_term_validator.models import CacheStrategy, ErrorMode
from linkml_term_validator.plugins.base import BaseOntologyPlugin, SeverityOverrides
from linkml_term_validator.utils import not4curation_message

VALID_OBLIGATION_LEVELS = frozenset(
    {"REQUIRED", "RECOMMENDED", "OPTIONAL", "EXAMPLE", "DISCOURAGED"}
)
SKIP_MEMBERSHIP_OBLIGATION_LEVELS = frozenset({"OPTIONAL", "EXAMPLE"})


class _ExtractedTarget(NamedTuple):
    """A single target value resolved by ``binds_value_of`` within a slot value."""

    value: Any
    field_path: str
    label_parent: Optional[dict[str, Any]]
    label_class: Optional[str]
    parent_subpath: str


class _PathCursor(NamedTuple):
    """Intermediate cursor while walking ``binds_value_of`` path segments."""

    curr_val: Any
    curr_class: Optional[str]
    curr_path: str
    parent_dict: Optional[dict[str, Any]]
    parent_class: Optional[str]
    parent_subpath: str


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
        cache_strategy: Literal["progressive", "greedy"]
        | CacheStrategy = CacheStrategy.PROGRESSIVE,
        offline: bool = False,
        severity_overrides: Optional[SeverityOverrides] = None,
        check_not4curation: Optional[bool] = None,
        not4curation_markers: Optional[Iterable[str]] = None,
        service_retries: Optional[int] = None,
        service_retry_backoff: Optional[float] = None,
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
            check_not4curation: Flag a bound term whose ontology marks it as
                not for annotation, e.g. with a ``Not4Curation`` synonym
                (#70). Explicit value wins over ``oak_config.yaml``; None
                takes the config file's, else True. ``binding_not4curation``
                defaults to ERROR; demote it via ``severity_overrides``
            not4curation_markers: Custom marker substrings; explicit wins over
                the config file; None takes the config file's, else defaults
            service_retries: Extra attempts while the ontology service reports
                itself unavailable; explicit wins over the config file's
                ``service_retries``; None takes that, else 2 (0 fails fast)
            service_retry_backoff: Seconds before the first retry, doubling
                thereafter; explicit wins over the config file's
                ``service_retry_backoff``; None takes that, else 1.0
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
            service_retries=service_retries,
            service_retry_backoff=service_retry_backoff,
        )
        self.validate_labels = validate_labels
        self.strict = strict
        self.schema_view = None
        # Map (class_name, slot_name) -> [EnumBinding]
        self.bindings_map: dict[tuple[str, str], list] = {}
        # Map (class_name, slot_name) -> range name for O(1) class-range lookups
        self._slot_range_map: dict[tuple[str, str], str] = {}
        # Map id(binding) -> normalized obligation level precomputed in pre_process
        self._binding_levels: dict[int, str] = {}
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
        self._slot_range_map.clear()
        self._binding_levels.clear()
        all_classes = list(self.schema_view.all_classes().values())
        self._class_names = {cls.name for cls in all_classes}

        for cls in all_classes:
            class_properties: dict[str, set[str]] = {}
            for slot in self.schema_view.class_induced_slots(cls.name):
                if slot.range:
                    self._slot_range_map[(cls.name, slot.name)] = slot.range
                if slot.bindings:
                    key = (cls.name, slot.name)
                    self.bindings_map[key] = slot.bindings
                    # Track referenced enums (skip enums only bound via OPTIONAL/EXAMPLE,
                    # whose membership is never checked)
                    for binding in slot.bindings:
                        level = self._normalize_obligation_level(
                            getattr(binding, "obligation_level", None)
                        )
                        self._binding_levels[id(binding)] = level
                        if (
                            binding.range
                            and level not in SKIP_MEMBERSHIP_OBLIGATION_LEVELS
                        ):
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
                self.expanded_enums[enum_name] = self.expand_enum(
                    enum_def, self.schema_view
                )

    def process(
        self, instance: dict, context: ValidationContext
    ) -> Iterator[ValidationResult]:
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

                    item_path = (
                        f"{slot_path}[{i}]" if isinstance(value, list) else slot_path
                    )

                    # Validate all binding constraints on this item together so
                    # membership checks run before deduplicated existence,
                    # Not4Curation, and label checks regardless of binding order.
                    yield from self._validate_bindings_for_value(
                        value=val,
                        bindings=self.bindings_map[key],
                        slot_name=slot_name,
                        instance=root_instance,
                        target_class=current_class,
                        path=item_path,
                    )

            # Recurse into nested objects
            nested_class = self._get_slot_class_range(current_class, slot_name)
            if nested_class:
                values = value if isinstance(value, list) else [value]
                for i, val in enumerate(values):
                    if isinstance(val, dict):
                        item_path = (
                            f"{slot_path}[{i}]"
                            if isinstance(value, list)
                            else slot_path
                        )
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

    def _get_slot_class_range(
        self, class_name: Optional[str], slot_name: str
    ) -> Optional[str]:
        """Return the LinkML class range of ``(class_name, slot_name)`` if it is a class."""
        if not class_name:
            return None
        rng = self._slot_range_map.get((class_name, slot_name))
        if rng is None:
            slot_def = self._get_slot_definition(class_name, slot_name)
            rng = slot_def.range if slot_def else None
        if rng and rng in self._class_names:
            return rng
        return None

    @staticmethod
    def _normalize_obligation_level(raw_level: Any) -> str:
        """Normalize a binding's ``obligation_level`` (defaulting to ``REQUIRED``).

        Accepts LinkML ``ObligationLevelEnum`` ``PermissibleValue`` objects,
        Python ``Enum`` members, plain strings, or ``None``. Also accepts
        ``ENCOURAGED`` (listed under ``aliases`` on ``RECOMMENDED`` in
        LinkML's ``meta.yaml``, though YAML schemas should use ``RECOMMENDED``).

        Examples:
            >>> BindingValidationPlugin._normalize_obligation_level(None)
            'REQUIRED'
            >>> BindingValidationPlugin._normalize_obligation_level("recommended")
            'RECOMMENDED'
            >>> BindingValidationPlugin._normalize_obligation_level("ENCOURAGED")
            'RECOMMENDED'
            >>> BindingValidationPlugin._normalize_obligation_level("DISCOURAGED")
            'DISCOURAGED'
        """
        if raw_level is None:
            return "REQUIRED"
        text = (
            str(getattr(raw_level, "text", getattr(raw_level, "value", raw_level)))
            .strip()
            .upper()
        )
        if not text:
            return "REQUIRED"
        if text == "ENCOURAGED":
            return "RECOMMENDED"
        if text not in VALID_OBLIGATION_LEVELS:
            valid = ", ".join(sorted(VALID_OBLIGATION_LEVELS))
            raise ValueError(
                f"Unknown obligation_level: {raw_level!r}. Valid levels are: {valid}"
            )
        return text

    def _validate_binding(
        self,
        value: Any,
        binding: Any,
        slot_name: str,
        instance: dict,
        target_class: str,
        path: str = "",
    ) -> Iterator[ValidationResult]:
        """Validate a single binding constraint on ``value``."""
        yield from self._validate_bindings_for_value(
            value=value,
            bindings=[binding],
            slot_name=slot_name,
            instance=instance,
            target_class=target_class,
            path=path,
        )

    def _validate_bindings_for_value(
        self,
        value: Any,
        bindings: list[Any],
        slot_name: str,
        instance: dict,
        target_class: str,
        path: str = "",
    ) -> Iterator[ValidationResult]:
        """Validate all bindings on a single slot value in two passes.

        Pass 1 evaluates enum membership across all bindings first so
        ``hard_rejected_fields`` is complete regardless of binding order in the
        schema. Pass 2 then runs term existence, ``Not4Curation``, and label
        validation once per unique resolved target.
        """
        slot_range_class = self._get_slot_class_range(target_class, slot_name)
        hard_rejected_fields: set[tuple[str, str]] = set()
        not4curation_scope_fields: set[tuple[str, str]] = set()
        post_check_targets: list[tuple[_ExtractedTarget, Any]] = []

        # Pass 1: enum membership checks across all bindings
        for binding in bindings:
            obligation_level = self._binding_levels.get(id(binding))
            if obligation_level is None:
                obligation_level = self._normalize_obligation_level(
                    getattr(binding, "obligation_level", None)
                )

            raw_field_path = getattr(binding, "binds_value_of", None)
            targets = self._extract_targets(value, raw_field_path, slot_range_class)

            for target in targets:
                field_value = target.value
                field_path = target.field_path

                enum_results: list[ValidationResult] = []
                if (
                    binding.range
                    and obligation_level not in SKIP_MEMBERSHIP_OBLIGATION_LEVELS
                ):
                    enum_results = list(
                        self._validate_against_enum(
                            field_value=field_value,
                            enum_name=binding.range,
                            field_path=field_path,
                            slot_name=slot_name,
                            instance=instance,
                            target_class=target_class,
                            path=path,
                            obligation_level=obligation_level,
                        )
                    )
                    yield from enum_results

                if not isinstance(field_value, str):
                    continue

                if any(
                    r.type == ErrorMode.BINDING_VALIDATION.value
                    and r.severity == Severity.ERROR
                    for r in enum_results
                ):
                    hard_rejected_fields.add((field_path, field_value))

                not4curation_enum = (
                    binding.range
                    if obligation_level not in SKIP_MEMBERSHIP_OBLIGATION_LEVELS
                    else None
                )
                if self._not4curation_in_scope(field_value, not4curation_enum):
                    not4curation_scope_fields.add((field_path, field_value))

                post_check_targets.append((target, binding))

        # Pass 2: deduplicated ontology hygiene checks (existence, Not4Curation, labels)
        seen_checks: set[tuple[Any, ...]] = set()
        for target, binding in post_check_targets:
            field_value = target.value
            field_path = target.field_path

            # Check term existence for configured prefixes (strict mode). Offline mode
            # always checks existence so an uncached term can't pass silently (#51).
            exists_key = ("exists", field_path, field_value)
            if (self.strict or self.config.offline) and exists_key not in seen_checks:
                seen_checks.add(exists_key)
                exists_results = list(
                    self._validate_term_exists(
                        field_value=field_value,
                        field_path=field_path,
                        slot_name=slot_name,
                        instance=instance,
                        target_class=target_class,
                        path=path,
                    )
                )
                yield from exists_results
                if any(
                    r.type == ErrorMode.TERM_NOT_FOUND.value for r in exists_results
                ):
                    seen_checks.add(("not_found", field_path, field_value))

            # A term the enum accepted (or only warned on under RECOMMENDED) may
            # still be one its ontology says not to annotate with (a Not4Curation
            # synonym; see #70). Skip only when membership hard-rejected at ERROR
            # (across any binding on this slot) or the term was reported absent.
            hard_rejected = (field_path, field_value) in hard_rejected_fields
            not_found = ("not_found", field_path, field_value) in seen_checks
            not4curation_key = ("not4curation", field_path, field_value)
            if (
                not hard_rejected
                and not not_found
                and not4curation_key not in seen_checks
                and (field_path, field_value) in not4curation_scope_fields
            ):
                seen_checks.add(not4curation_key)
                yield from self._validate_not4curation(
                    field_value=field_value,
                    field_path=field_path,
                    slot_name=slot_name,
                    instance=instance,
                    target_class=target_class,
                    path=path,
                )

            # Optionally validate label matches ontology on the enclosing dict
            if self.validate_labels and isinstance(target.label_parent, dict):
                label_key = ("label", id(target.label_parent), field_value)
                if label_key not in seen_checks:
                    seen_checks.add(label_key)
                    range_class = target.label_class
                    if range_class is None and not target.parent_subpath:
                        range_class = self._get_binding_range_class(binding, slot_name)
                    label_path = (
                        f"{path}.{target.parent_subpath}"
                        if (path and target.parent_subpath)
                        else (target.parent_subpath or path)
                    )
                    yield from self._validate_label(
                        value=target.label_parent,
                        field_value=field_value,
                        slot_name=slot_name,
                        instance=instance,
                        target_class=target_class,
                        range_class=range_class,
                        path=label_path,
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

    @staticmethod
    def _parse_field_path(raw_path: Optional[str]) -> list[str]:
        """Parse ``binds_value_of`` into path segments.

        ``None``, ``""``, and ``"."`` all denote the slot value itself (``[]``).
        Bracket indices (``extensions[0].value``) and wildcard brackets
        (``coding[*].code``) are normalized to dot segments.

        Examples:
            >>> BindingValidationPlugin._parse_field_path(None)
            []
            >>> BindingValidationPlugin._parse_field_path(".")
            []
            >>> BindingValidationPlugin._parse_field_path("term.id")
            ['term', 'id']
            >>> BindingValidationPlugin._parse_field_path("extensions.0.value")
            ['extensions', '0', 'value']
            >>> BindingValidationPlugin._parse_field_path("extensions[0].value")
            ['extensions', '0', 'value']
        """
        if raw_path is None:
            return []
        cleaned = raw_path.strip()
        if cleaned in ("", "."):
            return []
        cleaned = re.sub(r"\[\*\]", "", cleaned)
        cleaned = re.sub(r"\[(\d+)\]", r".\1", cleaned)
        return [seg for seg in cleaned.split(".") if seg and seg != "."]

    @staticmethod
    def _iter_leaf_items(val: Any, base_path: str) -> Iterator[tuple[Any, str]]:
        """Flatten multivalued list leaves, preserving index paths."""
        if val is None:
            return
        if isinstance(val, list):
            for i, item in enumerate(val):
                if item is None:
                    continue
                item_path = f"{base_path}[{i}]" if base_path else f"[{i}]"
                yield from BindingValidationPlugin._iter_leaf_items(item, item_path)
        else:
            yield (val, base_path or ".")

    def _step_segment(
        self,
        curr_val: Any,
        curr_class: Optional[str],
        curr_path: str,
        seg: str,
    ) -> Iterator[_PathCursor]:
        """Advance one path segment from ``curr_val``, fanning out across lists."""
        if curr_val is None:
            return

        if isinstance(curr_val, list):
            if seg.isdigit():
                idx = int(seg)
                if 0 <= idx < len(curr_val) and curr_val[idx] is not None:
                    next_path = f"{curr_path}[{idx}]" if curr_path else f"[{idx}]"
                    yield _PathCursor(
                        curr_val[idx], curr_class, next_path, None, None, ""
                    )
                return
            for i, elem in enumerate(curr_val):
                if elem is None:
                    continue
                elem_path = f"{curr_path}[{i}]" if curr_path else f"[{i}]"
                yield from self._step_segment(elem, curr_class, elem_path, seg)
            return

        if isinstance(curr_val, dict) and seg in curr_val and curr_val[seg] is not None:
            next_class = self._get_slot_class_range(curr_class, seg)
            next_path = f"{curr_path}.{seg}" if curr_path else seg
            yield _PathCursor(
                curr_val[seg], next_class, next_path, curr_val, curr_class, curr_path
            )

    def _extract_targets(
        self,
        value: Any,
        raw_field_path: Optional[str] = None,
        slot_range_class: Optional[str] = None,
    ) -> list[_ExtractedTarget]:
        """Extract target value(s) from ``value`` along ``binds_value_of``.

        Supports:
        - ``None`` / ``""`` / ``"."``: binds ``value`` itself (implicit default ``"."``)
        - Multi-level dot paths across nested dicts (e.g. ``"term.id"``, ``"a.b.c"``)
        - Auto-fan-out across intermediate or leaf lists (e.g. ``"coding.code"``)
        - Explicit integer list indices (e.g. ``"extensions.0.value"``)

        Absent or ``None`` fields yield no target (slot presence is governed by
        LinkML's ``required`` metaslot rather than bindings).

        Examples:
            >>> plugin = BindingValidationPlugin(cache_labels=False)
            >>> [t.value for t in plugin._extract_targets("GO:0008150")]
            ['GO:0008150']
            >>> [t.value for t in plugin._extract_targets({"term": {"id": "GO:1"}}, "term.id")]
            ['GO:1']
            >>> [(t.value, t.field_path) for t in plugin._extract_targets(
            ...     {"coding": [{"code": "GO:1"}, {"code": "GO:2"}]}, "coding.code"
            ... )]
            [('GO:1', 'coding[0].code'), ('GO:2', 'coding[1].code')]
        """
        if value is None:
            return []

        segments = self._parse_field_path(raw_field_path)
        if not segments:
            if isinstance(value, list):
                return [
                    _ExtractedTarget(item, item_path, None, None, "")
                    for item, item_path in self._iter_leaf_items(value, "")
                ]
            return [_ExtractedTarget(value, ".", None, None, "")]

        cursors: list[_PathCursor] = [
            _PathCursor(value, slot_range_class, "", None, None, "")
        ]

        for seg in segments:
            next_cursors: list[_PathCursor] = []
            for cursor in cursors:
                next_cursors.extend(
                    self._step_segment(
                        cursor.curr_val, cursor.curr_class, cursor.curr_path, seg
                    )
                )
            cursors = next_cursors
            if not cursors:
                return []

        targets: list[_ExtractedTarget] = []
        for cursor in cursors:
            if cursor.curr_val is None:
                continue
            if isinstance(cursor.curr_val, list):
                for item, item_path in self._iter_leaf_items(
                    cursor.curr_val, cursor.curr_path
                ):
                    targets.append(_ExtractedTarget(item, item_path, None, None, ""))
            else:
                targets.append(
                    _ExtractedTarget(
                        cursor.curr_val,
                        cursor.curr_path,
                        cursor.parent_dict,
                        cursor.parent_class,
                        cursor.parent_subpath,
                    )
                )
        return targets

    def _extract_field(
        self, value: Any, field_path: Optional[str] = None
    ) -> Optional[Any]:
        """Convenience helper returning the scalar or fanned-out list at ``field_path``.

        Examples:
            >>> plugin = BindingValidationPlugin(cache_labels=False)
            >>> plugin._extract_field("GO:0008150")
            'GO:0008150'
            >>> plugin._extract_field({"extensions": [{"value": "GO:1"}, {"value": "GO:2"}]}, "extensions.0.value")
            'GO:1'
            >>> plugin._extract_field({"coding": [{"code": "GO:1"}]}, "coding.code")
            ['GO:1']
        """
        targets = self._extract_targets(value, field_path)
        if not targets:
            return None
        segments = self._parse_field_path(field_path)
        # Distinguish direct/indexed access ("extensions.0.value" -> "extensions[0].value")
        # from implicit list fan-out ("coding.code" -> "coding[0].code").
        expected_single_path = (
            "."
            if not segments
            else ".".join(f"[{s}]" if s.isdigit() else s for s in segments).replace(
                ".[", "["
            )
        )
        if len(targets) == 1 and targets[0].field_path == expected_single_path:
            return targets[0].value
        return [t.value for t in targets]

    def _validate_against_enum(
        self,
        field_value: Any,
        enum_name: str,
        field_path: str,
        slot_name: str,
        instance: dict,
        target_class: str,
        path: str = "",
        obligation_level: str = "REQUIRED",
    ) -> Iterator[ValidationResult]:
        """Validate field value against enum (static or dynamic) under ``obligation_level``.

        Obligation levels:
        - ``REQUIRED`` (default): ``ERROR`` when value is outside the enum
        - ``RECOMMENDED`` / ``ENCOURAGED``: ``WARN`` when value is outside the enum
        - ``OPTIONAL`` / ``EXAMPLE``: skipped (no membership validation)
        - ``DISCOURAGED``: ``WARN`` when value IS inside the enum

        Args:
            field_value: Value to validate
            enum_name: Name of the enum to validate against
            field_path: Path to the field within the binding
            slot_name: Name of the slot
            instance: Full instance
            target_class: Name of the class
            path: JSON path to this location
            obligation_level: Binding obligation level (default ``"REQUIRED"``)

        Yields:
            ValidationResult on binding violation
        """
        level = self._normalize_obligation_level(obligation_level)
        if level in SKIP_MEMBERSHIP_OBLIGATION_LEVELS:
            return

        if self.schema_view is None:
            return
        enum_def = self.schema_view.get_enum(enum_name)
        if not enum_def:
            return

        default_severity = Severity.ERROR if level == "REQUIRED" else Severity.WARN
        severity = self.severity_for(
            ErrorMode.BINDING_VALIDATION, default=default_severity
        )
        extra_ctx = [f"obligation_level: {level}"] if level != "REQUIRED" else []

        if isinstance(field_value, (dict, list)):
            yield ValidationResult(
                type="binding_validation",
                severity=severity,
                message=(
                    f"Bound value at '{field_path}' must be a scalar to validate "
                    f"against enum '{enum_name}', got {type(field_value).__name__}"
                ),
                instance=instance,
                instantiates=target_class,
                context=[
                    f"path: {path}",
                    f"slot: {slot_name}",
                    f"field: {field_path}",
                    *extra_ctx,
                ],
            )
            return

        field_str = field_value if isinstance(field_value, str) else str(field_value)
        is_dynamic = self.is_dynamic_enum(enum_def)

        if is_dynamic:
            # Use the greedy pre-expanded set when the enum was materialized;
            # otherwise validate per value. The per-value branch covers progressive
            # mode AND greedy dynamic enums that were not pre-expanded (e.g. an
            # un-materialized enum offline), where it surfaces the clear offline
            # diagnostic instead of silently passing via the static path below.
            if enum_name in self.expanded_enums:
                valid_values = self.expanded_enums[enum_name]
                in_enum = field_str in valid_values
                if level == "DISCOURAGED":
                    if in_enum:
                        yield ValidationResult(
                            type="binding_validation",
                            severity=severity,
                            message=(
                                f"Value '{field_str}' is in discouraged dynamic enum "
                                f"'{enum_name}' (expanded from ontology)"
                            ),
                            instance=instance,
                            instantiates=target_class,
                            context=[
                                f"path: {path}",
                                f"slot: {slot_name}",
                                f"field: {field_path}",
                                f"discouraged_values: {len(valid_values)} terms",
                                *extra_ctx,
                            ],
                        )
                elif not in_enum:
                    yield ValidationResult(
                        type="binding_validation",
                        severity=severity,
                        message=(
                            f"Value '{field_str}' not in dynamic enum "
                            f"'{enum_name}' (expanded from ontology)"
                        ),
                        instance=instance,
                        instantiates=target_class,
                        context=[
                            f"path: {path}",
                            f"slot: {slot_name}",
                            f"field: {field_path}",
                            f"allowed_values: {len(valid_values)} terms",
                            *extra_ctx,
                        ],
                    )
                return

            in_enum = self.is_value_in_enum(field_str, enum_def, self.schema_view)
            # Offline with an unmaterialized dynamic enum is an infrastructure/cache
            # problem, not a data error. Report ERROR intentionally regardless of
            # obligation_level (including RECOMMENDED and DISCOURAGED, since without
            # a complete cache we cannot confirm a value is outside a discouraged
            # closure either; see #51).
            if not in_enum and self._offline_dynamic_enum_unmaterialized(enum_def):
                yield ValidationResult(
                    type="binding_validation",
                    severity=self.severity_for(ErrorMode.BINDING_VALIDATION),
                    message=self._offline_unmaterialized_enum_message(
                        field_str, enum_name
                    ),
                    instance=instance,
                    instantiates=target_class,
                    context=[
                        f"path: {path}",
                        f"slot: {slot_name}",
                        f"field: {field_path}",
                        "validation: offline (enum cache not materialized)",
                        *extra_ctx,
                    ],
                )
                return

            if level == "DISCOURAGED":
                if in_enum:
                    yield ValidationResult(
                        type="binding_validation",
                        severity=severity,
                        message=(
                            f"Value '{field_str}' is in discouraged dynamic enum "
                            f"'{enum_name}' (expanded from ontology)"
                        ),
                        instance=instance,
                        instantiates=target_class,
                        context=[
                            f"path: {path}",
                            f"slot: {slot_name}",
                            f"field: {field_path}",
                            "validation: progressive (lazy)",
                            *extra_ctx,
                        ],
                    )
            elif not in_enum:
                yield ValidationResult(
                    type="binding_validation",
                    severity=severity,
                    message=(
                        f"Value '{field_str}' not in dynamic enum "
                        f"'{enum_name}' (expanded from ontology)"
                    ),
                    instance=instance,
                    instantiates=target_class,
                    context=[
                        f"path: {path}",
                        f"slot: {slot_name}",
                        f"field: {field_path}",
                        "validation: progressive (lazy)",
                        *extra_ctx,
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

        in_enum = field_str in valid_values
        if level == "DISCOURAGED":
            if in_enum:
                yield ValidationResult(
                    type="binding_validation",
                    severity=severity,
                    message=f"Value '{field_str}' is in discouraged enum '{enum_name}'",
                    instance=instance,
                    instantiates=target_class,
                    context=[
                        f"path: {path}",
                        f"slot: {slot_name}",
                        f"field: {field_path}",
                        f"discouraged_values: {len(valid_values)} terms",
                        *extra_ctx,
                    ],
                )
        elif not in_enum:
            yield ValidationResult(
                type="binding_validation",
                severity=severity,
                message=f"Value '{field_str}' not in enum '{enum_name}'",
                instance=instance,
                instantiates=target_class,
                context=[
                    f"path: {path}",
                    f"slot: {slot_name}",
                    f"field: {field_path}",
                    f"allowed_values: {len(valid_values)} terms",
                    *extra_ctx,
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

    def _not4curation_in_scope(
        self, field_value: str, enum_name: Optional[str]
    ) -> bool:
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
                provided_labels = [
                    label for label in provided_label if isinstance(label, str)
                ]
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
                self.normalize_string(label) == normalized_ontology
                for label in provided_labels
            ):
                yield ValidationResult(
                    type="binding_label_mismatch",
                    severity=self.severity_for(ErrorMode.BINDING_LABEL_MISMATCH),
                    message=f"Label mismatch for '{field_value}': expected '{ontology_label}', got '{provided_label}'",
                    instance=instance,
                    instantiates=target_class,
                    context=result_context,
                )
