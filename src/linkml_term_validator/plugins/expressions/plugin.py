"""Plugin for validating values written in a grammar-based code system.

Ontology terms are looked up; UCUM units, SMILES strings and InChIs are
parsed. A slot opts in with an ``expression_grammar`` annotation, either on
the slot itself or on its range type (so a ``UcumCode`` type can carry it for
every slot that uses it):

.. code-block:: yaml

    types:
      UcumCode:
        typeof: string
        annotations:
          expression_grammar: ucum
    slots:
      unit:
        range: UcumCode
      smiles:
        range: string
        annotations:
          expression_grammar: smiles

Example:
    >>> from linkml_term_validator.plugins import ExpressionValidationPlugin
    >>> plugin = ExpressionValidationPlugin()
    >>> plugin.severity_overrides
    {}
"""

from collections.abc import Iterator
from typing import Any, Optional

from linkml.validator.plugins import ValidationPlugin  # type: ignore[import-untyped]
from linkml.validator.report import Severity, ValidationResult  # type: ignore[import-untyped]
from linkml.validator.validation_context import ValidationContext  # type: ignore[import-untyped]

from linkml_term_validator.models import ErrorMode
from linkml_term_validator.plugins.base import BaseOntologyPlugin, SeverityOverrides
from linkml_term_validator.plugins.expressions.grammars import (
    Checker,
    GrammarUnavailableError,
    available_grammars,
    get_grammar,
    load_checker,
)

GRAMMAR_ANNOTATION = "expression_grammar"


def _annotation_value(element: Any, key: str) -> Optional[str]:
    """Return a string annotation on a schema element, or None."""
    annotations = getattr(element, "annotations", None)
    if not annotations:
        return None
    # A plain dict on a schema element, a JsonObj on an induced slot.
    if isinstance(annotations, dict):
        annotation = annotations.get(key)
    else:
        annotation = getattr(annotations, key, None)
    if annotation is None:
        return None
    value = getattr(annotation, "value", annotation)
    return str(value) if value is not None else None


class ExpressionValidationPlugin(ValidationPlugin):
    """Validates slot values against a grammar-based code system (UCUM, SMILES, InChI...).

    Walks nested objects like :class:`BindingValidationPlugin`, so a unit on a
    measurement inside a sample is checked too. Needs no ontology access.
    """

    def __init__(self, severity_overrides: Optional[SeverityOverrides] = None):
        """Initialize the expression plugin.

        Args:
            severity_overrides: Mapping of ErrorMode to severity, e.g.
                ``{"expression_invalid": "WARN"}``
        """
        self.severity_overrides = BaseOntologyPlugin._normalize_severity_overrides(
            severity_overrides
        )
        self.schema_view: Any = None
        # (class, slot) -> grammar name, or None for a slot that has none.
        self._slot_grammars: dict[tuple[str, str], Optional[str]] = {}
        self._class_names: set[str] = set()
        # Problems with the schema or the install are reported once per source,
        # so every data file that depends on them still fails.
        self._reported_once: set[tuple[str, str]] = set()

    def severity_for(self, error_mode: ErrorMode) -> Severity:
        """Resolve the severity to report a problem at."""
        return self.severity_overrides.get(error_mode.value, error_mode.default_severity)

    def pre_process(self, context: ValidationContext) -> None:
        """Capture the schema and reset per-source state."""
        self.schema_view = context.schema_view
        self._slot_grammars = {}
        self._reported_once = set()
        self._class_names = set(self.schema_view.all_classes()) if self.schema_view else set()

    def process(self, instance: dict, context: ValidationContext) -> Iterator[ValidationResult]:
        """Validate every annotated slot value in the instance, at any depth.

        Args:
            instance: Data instance to validate
            context: Validation context

        Yields:
            ValidationResult for each invalid value, unknown grammar, or missing parser
        """
        if not self.schema_view or not context.target_class:
            return
        yield from self._process_recursive(instance, context.target_class, "", instance)

    def _process_recursive(
        self, obj: Any, current_class: str, path: str, root_instance: dict
    ) -> Iterator[ValidationResult]:
        """Check one object's slots, then recurse into nested class-ranged values."""
        if not isinstance(obj, dict):
            return
        for slot_name, value in obj.items():
            slot = self._induced_slot(current_class, slot_name)
            if slot is None:
                continue
            slot_path = f"{path}.{slot_name}" if path else slot_name
            items: list[tuple[Optional[int], Any]] = (
                list(enumerate(value)) if isinstance(value, list) else [(None, value)]
            )

            grammar_name = self._grammar_for(current_class, slot)
            if grammar_name is not None:
                yield from self._check_values(
                    grammar_name, items, slot_name, slot_path, current_class, root_instance
                )
            elif slot.range in self._class_names:
                for i, val in items:
                    item_path = slot_path if i is None else f"{slot_path}[{i}]"
                    yield from self._process_recursive(val, slot.range, item_path, root_instance)

    def _induced_slot(self, class_name: str, slot_name: str) -> Any:
        try:
            return self.schema_view.induced_slot(slot_name, class_name)
        except (KeyError, AttributeError, ValueError):
            # Not in the schema - other validators report unknown slots.
            return None

    def _grammar_for(self, class_name: str, slot: Any) -> Optional[str]:
        """Find the grammar a slot declares, on itself or on its range type's ancestry."""
        key = (class_name, slot.name)
        if key not in self._slot_grammars:
            grammar = _annotation_value(slot, GRAMMAR_ANNOTATION)
            if grammar is None and slot.range and slot.range in self.schema_view.all_types():
                for type_name in self.schema_view.type_ancestors(slot.range):
                    grammar = _annotation_value(self.schema_view.get_type(type_name), GRAMMAR_ANNOTATION)
                    if grammar is not None:
                        break
            self._slot_grammars[key] = grammar
        return self._slot_grammars[key]

    def _check_values(
        self,
        grammar_name: str,
        items: list[tuple[Optional[int], Any]],
        slot_name: str,
        slot_path: str,
        current_class: str,
        root_instance: dict,
    ) -> Iterator[ValidationResult]:
        """Run a grammar's checker over a slot's values."""
        grammar = get_grammar(grammar_name)
        if grammar is None:
            if ("unknown", grammar_name) not in self._reported_once:
                self._reported_once.add(("unknown", grammar_name))
                known = ", ".join(sorted(available_grammars()))
                yield ValidationResult(
                    type="expression_grammar_unknown",
                    severity=self.severity_for(ErrorMode.EXPRESSION_GRAMMAR_UNKNOWN),
                    message=(
                        f"Slot '{slot_name}' declares unknown {GRAMMAR_ANNOTATION} "
                        f"'{grammar_name}'; known grammars: {known}"
                    ),
                    instance=root_instance,
                    instantiates=current_class,
                    context=[f"slot: {slot_name}"],
                )
            return

        try:
            checker: Checker = load_checker(grammar.name)
        except GrammarUnavailableError as exc:
            if ("unavailable", grammar.name) not in self._reported_once:
                self._reported_once.add(("unavailable", grammar.name))
                yield ValidationResult(
                    type="expression_grammar_unavailable",
                    severity=self.severity_for(ErrorMode.EXPRESSION_GRAMMAR_UNAVAILABLE),
                    message=f"{grammar.title} values were not checked: {exc}",
                    instance=root_instance,
                    instantiates=current_class,
                    context=[f"slot: {slot_name}", f"grammar: {grammar.name}"],
                )
            return

        for i, val in items:
            if val is None:
                continue
            item_path = slot_path if i is None else f"{slot_path}[{i}]"
            text = str(val)
            reason = "empty value" if not text.strip() else checker(text)
            if reason is None:
                continue
            yield ValidationResult(
                type="expression_invalid",
                severity=self.severity_for(ErrorMode.EXPRESSION_INVALID),
                message=f"Value '{text}' at {item_path}: {reason}",
                instance=root_instance,
                instantiates=current_class,
                context=[f"slot: {slot_name}", f"grammar: {grammar.name}", f"path: {item_path}"],
            )
