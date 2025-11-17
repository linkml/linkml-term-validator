"""Plugin for validating meaning fields in enum permissible values."""

from collections.abc import Iterator
from pathlib import Path
from typing import Optional

from linkml.validator.report import Severity, ValidationResult  # type: ignore[import-untyped]
from linkml.validator.validation_context import ValidationContext  # type: ignore[import-untyped]
from linkml_runtime.linkml_model import PermissibleValue

from linkml_term_validator.plugins.base import BaseOntologyPlugin


class PermissibleValueMeaningPlugin(BaseOntologyPlugin):
    """Validates that meaning fields in enum permissible_values reference valid ontology terms.

    This plugin is designed for schema validation - it checks that the schema itself
    is well-formed with respect to ontology term references in enum definitions.

    Example:
        enums:
          BiologicalProcessEnum:
            permissible_values:
              CELL_CYCLE:
                title: cell cycle
                meaning: GO:0007049  # ← Validates this exists and label matches
    """

    def __init__(
        self,
        oak_adapter_string: str = "sqlite:obo:",
        cache_labels: bool = True,
        cache_dir: Path | str = Path("cache"),
        oak_config_path: Optional[Path | str] = None,
        strict_mode: bool = False,
    ):
        """Initialize permissible value meaning plugin.

        Args:
            oak_adapter_string: Default OAK adapter string (e.g., "sqlite:obo:")
            cache_labels: Whether to cache ontology labels to disk
            cache_dir: Directory for label cache files
            oak_config_path: Path to oak_config.yaml for per-prefix adapters
            strict_mode: If True, treat warnings as errors
        """
        super().__init__(
            oak_adapter_string=oak_adapter_string,
            cache_labels=cache_labels,
            cache_dir=cache_dir,
            oak_config_path=oak_config_path,
        )
        self.strict_mode = strict_mode
        self.schema_view = None

    def pre_process(self, context: ValidationContext) -> None:
        """Initialize schema view before processing."""
        self.schema_view = context.schema_view

    def process(self, instance: dict, context: ValidationContext) -> Iterator[ValidationResult]:
        """Validate enum permissible_values in schema.

        Note: This validates the schema itself, not data instances.
        The 'instance' parameter is typically ignored for schema validation.

        Args:
            instance: Schema data (typically ignored)
            context: Validation context with schema_view

        Yields:
            ValidationResult for each validation issue found
        """
        if not self.schema_view:
            return

        # Iterate through all enums in the schema
        for enum_name, enum_def in self.schema_view.all_enums().items():
            if not enum_def.permissible_values:
                continue

            # Check each permissible value with a meaning
            for pv_name, pv in enum_def.permissible_values.items():
                if not pv.meaning:
                    continue

                # Validate the meaning reference
                yield from self._validate_meaning(
                    enum_name=enum_name,
                    pv_name=pv_name,
                    pv=pv,
                    meaning=pv.meaning,
                )

    def _validate_meaning(
        self,
        enum_name: str,
        pv_name: str,
        pv: PermissibleValue,
        meaning: str,
    ) -> Iterator[ValidationResult]:
        """Validate a single meaning field.

        Args:
            enum_name: Name of the enum
            pv_name: Name of the permissible value
            pv: PermissibleValue object
            meaning: CURIE to validate

        Yields:
            ValidationResult if validation fails
        """
        # Get ontology label
        ontology_label = self.get_ontology_label(meaning)

        if ontology_label is None:
            # Term not found in ontology
            yield ValidationResult(
                type="permissible_value_meaning",
                severity=Severity.ERROR,
                message=f"Ontology term '{meaning}' not found",
                instance={"enum": enum_name, "value": pv_name, "meaning": meaning},
                instantiates=enum_name,
                context=[f"enum: {enum_name}", f"value: {pv_name}"],
            )
            return

        # Check if label matches
        if pv.title or pv.description:
            # Extract aliases from PV (title, description)
            aliases = self.extract_aliases(pv, pv_name)

            # Normalize ontology label
            normalized_ontology = self.normalize_string(ontology_label)

            # Check if any alias matches
            if normalized_ontology not in aliases:
                severity = Severity.ERROR if self.strict_mode else Severity.WARN

                # Build expected vs actual message
                expected_labels = [pv.title] if pv.title else []
                if pv.description:
                    expected_labels.append(pv.description)

                yield ValidationResult(
                    type="permissible_value_label_mismatch",
                    severity=severity,
                    message=f"Label mismatch for {meaning}: expected one of {expected_labels}, but ontology has '{ontology_label}'",
                    instance={
                        "enum": enum_name,
                        "value": pv_name,
                        "meaning": meaning,
                        "expected": expected_labels,
                        "actual": ontology_label,
                    },
                    instantiates=enum_name,
                    context=[f"enum: {enum_name}", f"value: {pv_name}"],
                )

    def extract_aliases(self, pv: PermissibleValue, pv_name: str) -> set[str]:
        """Extract normalized aliases from a permissible value.

        Args:
            pv: PermissibleValue object
            pv_name: Name of the permissible value

        Returns:
            Set of normalized alias strings
        """
        aliases = {self.normalize_string(pv_name)}

        if pv.title:
            aliases.add(self.normalize_string(pv.title))

        if pv.description:
            aliases.add(self.normalize_string(pv.description))

        # Handle explicit aliases
        if hasattr(pv, "aliases") and pv.aliases:
            for alias in pv.aliases:
                aliases.add(self.normalize_string(alias))

        return aliases

    def post_process(self, context: ValidationContext) -> None:
        """Report unknown prefixes after validation."""
        unknown = self.get_unknown_prefixes()
        if unknown:
            # Note: We don't yield results here because post_process doesn't return an iterator
            # Unknown prefixes will be reported via logging or could be added to context
            pass
