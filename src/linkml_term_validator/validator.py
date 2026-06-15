"""Validator for external terms in LinkML schemas."""

from pathlib import Path
from typing import Optional

from linkml_runtime.linkml_model import EnumDefinition, PermissibleValue
from linkml_runtime.utils.schemaview import SchemaView

from linkml_term_validator.models import (
    SeverityLevel,
    ValidationConfig,
    ValidationIssue,
    ValidationResult,
)
from linkml_term_validator.utils import OntologyAccess, get_prefix, normalize_string


class EnumValidator:
    """Validates external term references in LinkML enums.

    This validator checks that `meaning` fields in permissible values
    reference valid ontology terms with correct labels.

    Ontology access (adapter management and label caching) is delegated to a
    shared :class:`~linkml_term_validator.utils.OntologyAccess` instance.

    Examples:
        >>> from pathlib import Path
        >>> config = ValidationConfig(cache_labels=False)
        >>> validator = EnumValidator(config)
    """

    def __init__(self, config: ValidationConfig):
        """Initialize the validator.

        Args:
            config: Configuration for validation behavior
        """
        self.config = config
        self.ontology = OntologyAccess(
            oak_adapter_string=config.oak_adapter_string,
            cache_labels=config.cache_labels,
            cache_dir=config.cache_dir,
            oak_config_path=config.oak_config_path,
        )

        if config.cache_labels:
            config.get_cache_dir()

    # =========================================================================
    # Ontology-access delegation (see linkml_term_validator.utils.OntologyAccess)
    # =========================================================================

    @staticmethod
    def _get_prefix(curie: str) -> Optional[str]:
        """Extract prefix from a CURIE.

        Args:
            curie: A CURIE like "GO:0008150"

        Returns:
            The prefix (e.g., "GO") or None if invalid

        Examples:
            >>> validator = EnumValidator(ValidationConfig())
            >>> validator._get_prefix("GO:0008150")
            'GO'
            >>> validator._get_prefix("CHEBI:12345")
            'CHEBI'
            >>> validator._get_prefix("invalid")
        """
        return get_prefix(curie)

    def _is_prefix_configured(self, prefix: str) -> bool:
        """Check if a prefix is configured in oak_config.yaml.

        Args:
            prefix: Ontology prefix (e.g., "GO")

        Returns:
            True if prefix has a non-empty adapter configured

        Examples:
            >>> validator = EnumValidator(ValidationConfig())
            >>> # Returns False if no oak_config loaded
            >>> validator._is_prefix_configured("GO")
            False
        """
        return self.ontology.is_prefix_configured(prefix)

    def _get_cache_file(self, prefix: str) -> Path:
        """Get the cache file path for a prefix.

        Args:
            prefix: Ontology prefix

        Returns:
            Path to the cache CSV file

        Examples:
            >>> validator = EnumValidator(ValidationConfig(cache_dir=Path("cache")))
            >>> validator._get_cache_file("GO")
            PosixPath('cache/go/terms.csv')
        """
        return self.ontology.get_cache_file(prefix)

    def _load_cache(self, prefix: str) -> dict[str, str]:
        """Load cached labels for a prefix.

        Args:
            prefix: Ontology prefix

        Returns:
            Dict mapping CURIEs to labels

        Examples:
            >>> validator = EnumValidator(ValidationConfig())
            >>> cache = validator._load_cache("GO")
            >>> isinstance(cache, dict)
            True
        """
        return self.ontology.load_cache(prefix)

    def _load_cache_with_timestamps(self, prefix: str) -> dict[str, dict[str, str]]:
        """Load cached labels with timestamps for a prefix.

        Args:
            prefix: Ontology prefix

        Returns:
            Dict mapping CURIEs to {"label": ..., "retrieved_at": ...}
        """
        return self.ontology.load_cache_with_timestamps(prefix)

    def _save_to_cache(self, prefix: str, curie: str, label: str) -> None:
        """Save a label to the cache.

        Preserves existing timestamps for unchanged entries. Only new or changed
        entries get a fresh timestamp. Entries are sorted by CURIE for
        deterministic output.

        Args:
            prefix: Ontology prefix
            curie: Full CURIE
            label: Label to cache
        """
        self.ontology.save_to_cache(prefix, curie, label)

    def _get_adapter(self, prefix: str) -> object | None:
        """Get an OAK adapter for a prefix.

        Args:
            prefix: Ontology prefix

        Returns:
            OAK adapter or None if unavailable
        """
        return self.ontology.get_adapter(prefix)

    def get_ontology_label(self, curie: str) -> Optional[str]:
        """Get the label for an ontology term.

        Uses multi-level caching: in-memory, then file, then adapter.

        Args:
            curie: A CURIE like "GO:0008150"

        Returns:
            The label or None if not found

        Examples:
            >>> validator = EnumValidator(ValidationConfig(cache_labels=False))
            >>> # This would return the actual label if ontology is accessible
            >>> validator.get_ontology_label("GO:0008150")  # doctest: +SKIP
        """
        return self.ontology.get_label(curie)

    @staticmethod
    def normalize_string(s: str) -> str:
        """Normalize a string for comparison.

        Removes punctuation and converts to lowercase.

        Args:
            s: String to normalize

        Returns:
            Normalized string

        Examples:
            >>> EnumValidator.normalize_string("Hello, World!")
            'hello world'
            >>> EnumValidator.normalize_string("T-Cell Receptor")
            't cell receptor'
        """
        return normalize_string(s)

    def get_unknown_prefixes(self) -> set[str]:
        """Get the set of unknown prefixes encountered.

        Returns:
            Set of prefixes that were not configured

        Examples:
            >>> validator = EnumValidator(ValidationConfig())
            >>> validator.get_unknown_prefixes()
            set()
        """
        return self.ontology.get_unknown_prefixes()

    # =========================================================================
    # Enum validation
    # =========================================================================

    def extract_aliases(
        self, pv: PermissibleValue, value_name: str
    ) -> set[str]:
        """Extract all acceptable label aliases from a permissible value.

        Checks: value name, title, aliases, structured_aliases, and annotations.

        Args:
            pv: PermissibleValue from LinkML schema
            value_name: The name of the permissible value

        Returns:
            Set of normalized aliases

        Examples:
            >>> from linkml_runtime.linkml_model import PermissibleValue
            >>> validator = EnumValidator(ValidationConfig())
            >>> pv = PermissibleValue(text="EXAMPLE", title="Example Term")
            >>> aliases = validator.extract_aliases(pv, "EXAMPLE")
            >>> "example" in aliases
            True
            >>> "example term" in aliases
            True
        """
        aliases = {self.normalize_string(value_name)}

        if pv.title:
            aliases.add(self.normalize_string(pv.title))

        if pv.description:
            aliases.add(self.normalize_string(pv.description))

        if hasattr(pv, "aliases") and pv.aliases:
            for alias in pv.aliases:
                aliases.add(self.normalize_string(alias))

        if hasattr(pv, "annotations") and pv.annotations:
            for annotation in pv.annotations:
                tag = annotation.tag
                value = annotation.value
                if tag in [
                    "label",
                    "display_name",
                    "preferred_name",
                    "synonym",
                ]:
                    aliases.add(self.normalize_string(value))

        return aliases

    def validate_enum(
        self, enum_def: EnumDefinition, enum_name: str
    ) -> list[ValidationIssue]:
        """Validate a single enum definition.

        Args:
            enum_def: EnumDefinition from LinkML schema
            enum_name: Name of the enum

        Returns:
            List of validation issues found
        """
        issues: list[ValidationIssue] = []

        if not enum_def.permissible_values:
            return issues

        for value_name, pv in enum_def.permissible_values.items():
            if not pv.meaning:
                continue

            meaning = pv.meaning
            actual_label = self.get_ontology_label(meaning)

            if actual_label is None:
                prefix = self._get_prefix(meaning)
                if (prefix and self._is_prefix_configured(prefix)) or self.config.strict_mode:
                    issues.append(
                        ValidationIssue(
                            enum_name=enum_name,
                            value_name=value_name,
                            severity=SeverityLevel.ERROR,
                            message=f"Could not retrieve label for {meaning}",
                            meaning=meaning,
                            expected_label=None,
                            actual_label=None,
                        )
                    )
                else:
                    issues.append(
                        ValidationIssue(
                            enum_name=enum_name,
                            value_name=value_name,
                            severity=SeverityLevel.INFO,
                            message=f"Unconfigured prefix, could not validate {meaning}",
                            meaning=meaning,
                            expected_label=None,
                            actual_label=None,
                        )
                    )
                continue

            expected_aliases = self.extract_aliases(pv, value_name)
            normalized_actual = self.normalize_string(actual_label)

            if normalized_actual not in expected_aliases:
                prefix = self._get_prefix(meaning)
                severity = (
                    SeverityLevel.ERROR
                    if prefix and self._is_prefix_configured(prefix)
                    else SeverityLevel.WARNING
                )

                if self.config.strict_mode:
                    severity = SeverityLevel.ERROR

                expected_label = pv.title or value_name
                issues.append(
                    ValidationIssue(
                        enum_name=enum_name,
                        value_name=value_name,
                        severity=severity,
                        message=f"Label mismatch for {meaning}",
                        meaning=meaning,
                        expected_label=expected_label,
                        actual_label=actual_label,
                    )
                )

        return issues

    def validate_schema(self, schema_path: Path) -> ValidationResult:
        """Validate all enums in a LinkML schema.

        Args:
            schema_path: Path to LinkML YAML schema

        Returns:
            ValidationResult with all issues found

        Examples:
            >>> from pathlib import Path
            >>> validator = EnumValidator(ValidationConfig())
        """
        result = ValidationResult(schema_path=schema_path)

        schema_view = SchemaView(str(schema_path))
        all_enums = schema_view.all_enums()

        for enum_name in all_enums:
            enum_def = schema_view.get_enum(enum_name)
            result.total_enums_checked += 1

            if enum_def.permissible_values:
                result.total_values_checked += len(enum_def.permissible_values)
                meanings_count = sum(
                    1
                    for pv in enum_def.permissible_values.values()
                    if pv.meaning
                )
                result.total_meanings_checked += meanings_count

            issues = self.validate_enum(enum_def, enum_name)
            result.issues.extend(issues)

        return result

    def validate_curie_label_pairs(
        self,
        pairs: list[tuple[str, str, str]],
    ) -> list[ValidationIssue]:
        """Validate a list of (CURIE, expected_label, location) tuples against ontology.

        For each pair:
        - If the CURIE resolves to a label, checks it matches the expected label.
        - If the CURIE cannot be resolved:
          - Configured prefix or strict_mode → ERROR
          - Unconfigured prefix without strict_mode → silently skipped

        Args:
            pairs: List of (curie, expected_label, location) tuples where location
                   is a human-readable string like "line:3" for error messages.

        Returns:
            List of validation issues found.

        Examples:
            >>> config = ValidationConfig(cache_labels=False)
            >>> validator = EnumValidator(config)
            >>> issues = validator.validate_curie_label_pairs([])
            >>> issues
            []
        """
        issues: list[ValidationIssue] = []

        for curie, expected_label, location in pairs:
            actual_label = self.get_ontology_label(curie)

            if actual_label is None:
                prefix = self._get_prefix(curie)
                if (prefix and self._is_prefix_configured(prefix)) or self.config.strict_mode:
                    issues.append(
                        ValidationIssue(
                            enum_name=location,
                            value_name=curie,
                            severity=SeverityLevel.ERROR,
                            message=f"Unresolvable CURIE: {curie}",
                            meaning=curie,
                            expected_label=expected_label,
                            actual_label=None,
                        )
                    )
                # else: unconfigured prefix without strict mode — silently skip
                continue

            normalized_actual = self.normalize_string(actual_label)
            normalized_expected = self.normalize_string(expected_label)

            if normalized_actual != normalized_expected:
                issues.append(
                    ValidationIssue(
                        enum_name=location,
                        value_name=curie,
                        severity=SeverityLevel.ERROR,
                        message=(
                            f"Label mismatch for {curie}: "
                            f"expected '{expected_label}', got '{actual_label}'"
                        ),
                        meaning=curie,
                        expected_label=expected_label,
                        actual_label=actual_label,
                    )
                )

        return issues
