"""Validator for external terms in LinkML schemas."""

import csv
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from linkml_runtime.linkml_model import EnumDefinition, PermissibleValue
from linkml_runtime.utils.schemaview import SchemaView
from oaklib import get_adapter
from ruamel.yaml import YAML

from linkml_term_validator.models import (
    SeverityLevel,
    ValidationConfig,
    ValidationIssue,
    ValidationResult,
)


class EnumValidator:
    """Validates external term references in LinkML enums.

    This validator checks that `meaning` fields in permissible values
    reference valid ontology terms with correct labels.

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
        self._label_cache: dict[str, Optional[str]] = {}
        self._adapter_cache: dict[str, Optional[object]] = {}
        self._oak_config: dict[str, str] = {}
        self._unknown_prefixes: set[str] = set()

        if config.oak_config_path and config.oak_config_path.exists():
            self._load_oak_config()

        if config.cache_labels:
            config.get_cache_dir()

    def _load_oak_config(self) -> None:
        """Load oak_config.yaml to get per-prefix adapter settings."""
        if self.config.oak_config_path is None:
            return
        yaml = YAML(typ="safe")
        with open(self.config.oak_config_path) as f:
            config_data = yaml.load(f)

        if "ontology_adapters" in config_data:
            self._oak_config = config_data["ontology_adapters"]

    def _get_prefix(self, curie: str) -> Optional[str]:
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
        if ":" not in curie:
            return None
        return curie.split(":", 1)[0]

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
        return prefix in self._oak_config and bool(self._oak_config[prefix])

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
        prefix_dir = self.config.cache_dir / prefix.lower()
        prefix_dir.mkdir(parents=True, exist_ok=True)
        return prefix_dir / "terms.csv"

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
        cache_file = self._get_cache_file(prefix)
        if not cache_file.exists():
            return {}

        cached = {}
        with open(cache_file) as f:
            reader = csv.DictReader(f)
            for row in reader:
                cached[row["curie"]] = row["label"]
        return cached

    def _save_to_cache(self, prefix: str, curie: str, label: str) -> None:
        """Save a label to the cache.

        Args:
            prefix: Ontology prefix
            curie: Full CURIE
            label: Label to cache
        """
        if not self.config.cache_labels:
            return

        cache_file = self._get_cache_file(prefix)
        file_exists = cache_file.exists()

        with open(cache_file, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["curie", "label", "retrieved_at"])
            if not file_exists:
                writer.writeheader()
            writer.writerow(
                {
                    "curie": curie,
                    "label": label,
                    "retrieved_at": datetime.now().isoformat(),
                }
            )

    def _get_adapter(self, prefix: str) -> object | None:
        """Get an OAK adapter for a prefix.

        Args:
            prefix: Ontology prefix

        Returns:
            OAK adapter or None if unavailable
        """
        if prefix in self._adapter_cache:
            return self._adapter_cache[prefix]

        adapter_string = None

        if prefix in self._oak_config:
            configured = self._oak_config[prefix]
            if not configured:
                self._adapter_cache[prefix] = None
                return None
            adapter_string = configured
        elif self._oak_config:
            # oak_config is loaded but prefix not in it - don't fall back to default
            self._adapter_cache[prefix] = None
            return None
        elif self.config.oak_adapter_string == "sqlite:obo:":
            adapter_string = f"sqlite:obo:{prefix.lower()}"

        if adapter_string:
            try:
                adapter = get_adapter(adapter_string)
                self._adapter_cache[prefix] = adapter
                return adapter
            except Exception as e:
                # Failed to get adapter (e.g., network error, missing database)
                # Cache None so we don't keep trying
                print(f"Warning: Could not load adapter for prefix '{prefix}': {e}")
                self._adapter_cache[prefix] = None
                return None

        self._adapter_cache[prefix] = None
        return None

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
        if curie in self._label_cache:
            return self._label_cache[curie]

        prefix = self._get_prefix(curie)
        if not prefix:
            return None

        if self.config.cache_labels:
            cached = self._load_cache(prefix)
            if curie in cached:
                label = cached[curie]
                self._label_cache[curie] = label
                return label

        adapter = self._get_adapter(prefix)
        if adapter is None:
            if not self._is_prefix_configured(prefix):
                self._unknown_prefixes.add(prefix)
            self._label_cache[curie] = None
            return None

        label = adapter.label(curie)  # type: ignore[attr-defined]
        self._label_cache[curie] = label

        if label and self.config.cache_labels:
            self._save_to_cache(prefix, curie, label)

        return label

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
        s = re.sub(r"[^\w\s]", " ", s)
        s = re.sub(r"\s+", " ", s)
        return s.strip().lower()

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
                if prefix and self._is_prefix_configured(prefix):
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

    def get_unknown_prefixes(self) -> set[str]:
        """Get the set of unknown prefixes encountered.

        Returns:
            Set of prefixes that were not configured

        Examples:
            >>> validator = EnumValidator(ValidationConfig())
            >>> validator.get_unknown_prefixes()
            set()
        """
        return self._unknown_prefixes
