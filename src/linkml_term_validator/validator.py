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
from linkml_term_validator.utils import (
    OntologyAccess,
    get_prefix,
    normalize_not4curation_markers,
    normalize_string,
    not4curation_message,
    obsolete_term_message,
)


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
            offline=config.offline,
            not4curation_markers=config.not4curation_markers,
        )
        # The shared oak_config.yaml may carry the Not4Curation keys too, so the
        # CLI and the plugins read one file and agree (see #70).
        self._load_not4curation_config(self.ontology.loaded_config)

        if config.cache_labels:
            config.get_cache_dir()

    def _load_not4curation_config(self, loaded: dict) -> None:
        """Apply ``check_not4curation`` / ``not4curation_markers`` from oak_config."""
        if not loaded:
            return
        if "check_not4curation" in loaded:
            value = loaded["check_not4curation"]
            if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
                value = value.strip().lower() == "true"
            if not isinstance(value, bool):
                raise ValueError(
                    f"check_not4curation must be a boolean or 'true'/'false', got: {value!r}"
                )
            self.config.check_not4curation = value
        if "not4curation_markers" in loaded:
            raw = loaded["not4curation_markers"]
            if isinstance(raw, str):
                raw = [raw]
            if not isinstance(raw, list):
                raise ValueError(
                    "not4curation_markers must be a list of strings, "
                    f"got {type(raw).__name__}: {raw!r}"
                )
            self.ontology.not4curation_markers = normalize_not4curation_markers(raw)
            self.config.not4curation_markers = list(self.ontology.not4curation_markers)

    def _not4curation_issue(
        self,
        curie: str,
        enum_name: str,
        value_name: str,
        expected_label: Optional[str],
        actual_label: Optional[str],
    ) -> Optional[ValidationIssue]:
        """Build the issue for a term its ontology marks as not for annotation.

        Returns None when the check is off, the term is clean, or the term
        could not be checked (the last is recorded on the ontology access
        object and surfaced by :meth:`get_not4curation_unchecked`).
        """
        if not self.config.check_not4curation:
            return None
        markers = self.ontology.find_not4curation_markers(curie)
        if not markers:
            return None
        return ValidationIssue(
            enum_name=enum_name,
            value_name=value_name,
            severity=SeverityLevel.ERROR,
            message=not4curation_message(curie, markers),
            meaning=curie,
            expected_label=expected_label,
            actual_label=actual_label,
        )

    def get_not4curation_unchecked(self) -> set[str]:
        """CURIEs the Not4Curation check was asked about but could not vet."""
        return self.ontology.get_not4curation_unchecked()

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

    def _unresolved_is_error(self, prefix: Optional[str]) -> bool:
        """Whether a term that could not be resolved should be an error.

        Offline mode always errors (no lookup is permitted, so an incomplete
        cache must never pass silently), as does strict mode or a configured
        prefix; an unconfigured prefix without either is downgraded instead.
        """
        return (
            bool(prefix and self._is_prefix_configured(prefix))
            or self.config.strict_mode
            or self.config.offline
        )

    def _unresolved_error_message(self, term: str, online_message: str) -> str:
        """Build the error message for an unresolved term (offline-aware)."""
        if self.config.offline:
            return f"Term {term} not found in offline cache"
        return online_message

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
                if self._unresolved_is_error(prefix):
                    issues.append(
                        ValidationIssue(
                            enum_name=enum_name,
                            value_name=value_name,
                            severity=SeverityLevel.ERROR,
                            message=self._unresolved_error_message(
                                meaning, f"Could not retrieve label for {meaning}"
                            ),
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

            # An obsolete term still resolves to a label, so it passes the
            # "not found" check above. Flag it explicitly instead of only
            # surfacing the incidental label mismatch its "obsolete ..." label
            # would otherwise produce.
            if self.ontology.is_obsolete(meaning):
                issues.append(
                    ValidationIssue(
                        enum_name=enum_name,
                        value_name=value_name,
                        severity=SeverityLevel.ERROR,
                        message=obsolete_term_message(meaning),
                        meaning=meaning,
                        expected_label=pv.title or value_name,
                        actual_label=actual_label,
                    )
                )
                continue

            # Not obsolete, but its ontology may still say "do not annotate"
            # through a synonym. Report it and go on to the label check.
            flagged = self._not4curation_issue(
                meaning, enum_name, value_name, pv.title or value_name, actual_label
            )
            if flagged is not None:
                issues.append(flagged)

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

        # A term whose synonyms could not be read was not vetted for a
        # Not4Curation marker. Carry that forward so a degraded run is
        # distinguishable from a clean one.
        result.not4curation_unchecked = sorted(self.get_not4curation_unchecked())

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
                if self._unresolved_is_error(prefix):
                    issues.append(
                        ValidationIssue(
                            enum_name=location,
                            value_name=curie,
                            severity=SeverityLevel.ERROR,
                            message=self._unresolved_error_message(
                                curie, f"Unresolvable CURIE: {curie}"
                            ),
                            meaning=curie,
                            expected_label=expected_label,
                            actual_label=None,
                        )
                    )
                # else: unconfigured prefix without strict mode — silently skip
                continue

            # An obsolete term resolves to a label, so flag it explicitly rather
            # than letting it pass (or masquerade as a label mismatch).
            if self.ontology.is_obsolete(curie):
                issues.append(
                    ValidationIssue(
                        enum_name=location,
                        value_name=curie,
                        severity=SeverityLevel.ERROR,
                        message=obsolete_term_message(curie),
                        meaning=curie,
                        expected_label=expected_label,
                        actual_label=actual_label,
                    )
                )
                continue

            flagged = self._not4curation_issue(
                curie, location, curie, expected_label, actual_label
            )
            if flagged is not None:
                issues.append(flagged)

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
