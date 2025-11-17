"""Legacy EnumValidator for backward compatibility.

This module provides the original EnumValidator API that delegates to the
PermissibleValueMeaningPlugin internally. This ensures backward compatibility
while leveraging the new plugin architecture.
"""

from pathlib import Path

from linkml_runtime.utils.schemaview import SchemaView

from linkml_term_validator.models import (
    SeverityLevel,
    ValidationConfig,
    ValidationIssue,
    ValidationResult,
)
from linkml_term_validator.plugins import PermissibleValueMeaningPlugin


class EnumValidatorLegacy:
    """Legacy validator for backward compatibility.

    This class maintains the original EnumValidator API but delegates
    to PermissibleValueMeaningPlugin internally.

    Note: For new code, use PermissibleValueMeaningPlugin directly with
    the LinkML Validator framework.

    Examples:
        >>> from pathlib import Path
        >>> config = ValidationConfig(cache_labels=False)
        >>> validator = EnumValidatorLegacy(config)
    """

    def __init__(self, config: ValidationConfig):
        """Initialize the validator.

        Args:
            config: Configuration for validation behavior
        """
        self.config = config
        self._plugin = PermissibleValueMeaningPlugin(
            oak_adapter_string=config.oak_adapter_string,
            cache_labels=config.cache_labels,
            cache_dir=config.cache_dir,
            oak_config_path=config.oak_config_path,
            strict_mode=config.strict_mode,
        )

    def validate_schema(self, schema_path: Path) -> ValidationResult:
        """Validate all enums in a LinkML schema.

        Args:
            schema_path: Path to LinkML YAML schema

        Returns:
            ValidationResult with all issues found
        """
        result = ValidationResult(schema_path=schema_path)

        # Load schema
        schema_view = SchemaView(str(schema_path))

        # Count enums and values
        all_enums = schema_view.all_enums()
        for enum_name in all_enums:
            enum_def = schema_view.get_enum(enum_name)
            result.total_enums_checked += 1

            if enum_def.permissible_values:
                result.total_values_checked += len(enum_def.permissible_values)
                meanings_count = sum(
                    1 for pv in enum_def.permissible_values.values() if pv.meaning
                )
                result.total_meanings_checked += meanings_count

        # Use plugin for validation
        from linkml.validator.validation_context import ValidationContext  # type: ignore[import-untyped]

        # Create a minimal validation context
        context = ValidationContext(
            schema=str(schema_path),
            target_class=None,  # Not applicable for schema validation
        )

        # Initialize plugin
        self._plugin.pre_process(context)

        # Run validation (instance is ignored for schema validation)
        for validation_result in self._plugin.process({}, context):
            # Convert LinkML ValidationResult to our ValidationIssue
            issue = ValidationIssue(
                enum_name=validation_result.instantiates or "unknown",
                value_name=(
                    validation_result.instance.get("value", "unknown")
                    if isinstance(validation_result.instance, dict)
                    else "unknown"
                ),
                severity=(
                    SeverityLevel.ERROR
                    if validation_result.severity.name == "ERROR"
                    else SeverityLevel.WARNING
                ),
                message=validation_result.message,
                meaning=(
                    validation_result.instance.get("meaning")
                    if isinstance(validation_result.instance, dict)
                    else None
                ),
                expected_label=(
                    validation_result.instance.get("expected")
                    if isinstance(validation_result.instance, dict)
                    else None
                ),
                actual_label=(
                    validation_result.instance.get("actual")
                    if isinstance(validation_result.instance, dict)
                    else None
                ),
            )
            result.issues.append(issue)

        # Cleanup
        self._plugin.post_process(context)

        return result

    def get_unknown_prefixes(self) -> set[str]:
        """Get the set of unknown prefixes encountered.

        Returns:
            Set of prefixes that were not configured
        """
        return self._plugin.get_unknown_prefixes()

    def get_ontology_label(self, curie: str) -> str | None:
        """Get the label for an ontology term.

        Args:
            curie: A CURIE like "GO:0008150"

        Returns:
            The label or None if not found
        """
        return self._plugin.get_ontology_label(curie)

    @staticmethod
    def normalize_string(s: str) -> str:
        """Normalize a string for comparison.

        Args:
            s: String to normalize

        Returns:
            Normalized string
        """
        return PermissibleValueMeaningPlugin.normalize_string(s)
