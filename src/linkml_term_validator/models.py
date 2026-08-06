"""Pydantic models for validation configuration and results."""

from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


class SeverityLevel(str, Enum):
    """Severity levels for validation issues."""

    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


class CacheStrategy(str, Enum):
    """Caching strategies for dynamic enum expansion.

    Examples:
        >>> CacheStrategy.PROGRESSIVE.value
        'progressive'
        >>> CacheStrategy.GREEDY.value
        'greedy'
    """

    PROGRESSIVE = "progressive"
    """Validate lazily and reuse positive cache hits (default, scalable)."""

    GREEDY = "greedy"
    """Expand entire enum upfront and cache all terms."""


class ErrorMode(str, Enum):
    """Categories of problem the validation plugins can report.

    Each member's value is the ``type`` field of an emitted
    ``linkml.validator.report.ValidationResult``. Passing a mapping of these
    to severities as ``severity_overrides`` lets a schema owner decide which
    problems are hard failures, without changing the plugin code.

    Examples:
        >>> ErrorMode.BINDING_LABEL_MISMATCH.value
        'binding_label_mismatch'
        >>> ErrorMode("term_not_found")
        <ErrorMode.TERM_NOT_FOUND: 'term_not_found'>
    """

    BINDING_VALIDATION = "binding_validation"
    """A bound field's value is outside its enum's permitted values."""

    BINDING_LABEL_INVALID = "binding_label_invalid"
    """A label field is not a string or list of strings."""

    BINDING_LABEL_MISMATCH = "binding_label_mismatch"
    """A label field disagrees with the ontology's canonical label."""

    TERM_NOT_FOUND = "term_not_found"
    """A term ID could not be found in the configured ontologies."""

    DYNAMIC_ENUM_VALIDATION = "dynamic_enum_validation"
    """A value is outside a dynamic enum's expanded closure."""

    PERMISSIBLE_VALUE_MEANING = "permissible_value_meaning"
    """A permissible value's ``meaning`` does not resolve to a real term."""

    PERMISSIBLE_VALUE_OBSOLETE = "permissible_value_obsolete"
    """A permissible value's ``meaning`` points at an obsolete term."""

    PERMISSIBLE_VALUE_LABEL_MISMATCH = "permissible_value_label_mismatch"
    """A permissible value's title/description disagrees with the ontology."""


class ValidationIssue(BaseModel):
    """A single validation issue found during term validation.

    Examples:
        >>> issue = ValidationIssue(
        ...     enum_name="BioreactorTypeEnum",
        ...     value_name="MEMBRANE",
        ...     severity=SeverityLevel.ERROR,
        ...     message="Label mismatch",
        ...     meaning="ENVO:03600010",
        ...     expected_label="membrane bioreactor",
        ...     actual_label="membrane reactor"
        ... )
        >>> issue.enum_name
        'BioreactorTypeEnum'
        >>> issue.is_error()
        True
    """

    enum_name: str = Field(..., description="Name of the enum being validated")
    value_name: str = Field(..., description="Name of the permissible value")
    severity: SeverityLevel = Field(..., description="Severity level of the issue")
    message: str = Field(..., description="Human-readable issue description")
    meaning: Optional[str] = Field(None, description="The CURIE being validated")
    expected_label: Optional[str] = Field(None, description="Expected label from schema")
    actual_label: Optional[str] = Field(None, description="Actual label from ontology")

    def is_error(self) -> bool:
        """Check if this issue is an error.

        Returns:
            True if severity is ERROR

        Examples:
            >>> ValidationIssue(
            ...     enum_name="Test", value_name="VAL", severity=SeverityLevel.ERROR,
            ...     message="test"
            ... ).is_error()
            True
        """
        return self.severity == SeverityLevel.ERROR

    def is_warning(self) -> bool:
        """Check if this issue is a warning.

        Returns:
            True if severity is WARNING

        Examples:
            >>> ValidationIssue(
            ...     enum_name="Test", value_name="VAL", severity=SeverityLevel.WARNING,
            ...     message="test"
            ... ).is_warning()
            True
        """
        return self.severity == SeverityLevel.WARNING


class ValidationResult(BaseModel):
    """Results from validating a LinkML schema.

    Examples:
        >>> result = ValidationResult(schema_path=Path("schema.yaml"))
        >>> result.total_enums_checked
        0
        >>> result.has_errors()
        False
    """

    schema_path: Path = Field(..., description="Path to the validated schema")
    issues: list[ValidationIssue] = Field(
        default_factory=list, description="List of validation issues found"
    )
    total_enums_checked: int = Field(default=0, description="Number of enums validated")
    total_values_checked: int = Field(default=0, description="Number of permissible values checked")
    total_meanings_checked: int = Field(default=0, description="Number of meanings validated")

    def has_errors(self) -> bool:
        """Check if any errors were found.

        Returns:
            True if any issues are errors

        Examples:
            >>> result = ValidationResult(schema_path=Path("test.yaml"))
            >>> result.has_errors()
            False
            >>> result.issues.append(ValidationIssue(
            ...     enum_name="Test", value_name="VAL", severity=SeverityLevel.ERROR,
            ...     message="test"
            ... ))
            >>> result.has_errors()
            True
        """
        return any(issue.is_error() for issue in self.issues)

    def has_warnings(self) -> bool:
        """Check if any warnings were found.

        Returns:
            True if any issues are warnings

        Examples:
            >>> result = ValidationResult(schema_path=Path("test.yaml"))
            >>> result.has_warnings()
            False
        """
        return any(issue.is_warning() for issue in self.issues)

    def error_count(self) -> int:
        """Count the number of errors.

        Returns:
            Number of error-level issues

        Examples:
            >>> result = ValidationResult(schema_path=Path("test.yaml"))
            >>> result.error_count()
            0
        """
        return sum(1 for issue in self.issues if issue.is_error())

    def warning_count(self) -> int:
        """Count the number of warnings.

        Returns:
            Number of warning-level issues

        Examples:
            >>> result = ValidationResult(schema_path=Path("test.yaml"))
            >>> result.warning_count()
            0
        """
        return sum(1 for issue in self.issues if issue.is_warning())

    def print_summary(self, verbose: bool = False) -> None:
        """Print a summary of validation results.

        Args:
            verbose: If True, print detailed information
        """
        if not self.issues and not verbose:
            print("✅")
            return

        print(f"\nValidation Results for {self.schema_path}")
        print(f"{'=' * 60}")
        print(f"Enums checked: {self.total_enums_checked}")
        print(f"Values checked: {self.total_values_checked}")
        print(f"Meanings validated: {self.total_meanings_checked}")
        print()

        errors = [i for i in self.issues if i.is_error()]
        warnings = [i for i in self.issues if i.is_warning()]

        if errors:
            print(f"❌ ERRORS ({len(errors)}):")
            for issue in errors:
                print(f"  {issue.enum_name}.{issue.value_name}: {issue.message}")
                if issue.meaning:
                    print(f"    Meaning: {issue.meaning}")
                if issue.expected_label and issue.actual_label:
                    print(f"    Expected: {issue.expected_label}")
                    print(f"    Actual: {issue.actual_label}")
            print()

        if warnings:
            print(f"⚠️  WARNINGS ({len(warnings)}):")
            for issue in warnings:
                print(f"  {issue.enum_name}.{issue.value_name}: {issue.message}")
            print()

        if not errors and not warnings:
            print("✅ No issues found!")


class ValidationConfig(BaseModel):
    """Configuration for term validation.

    Examples:
        >>> config = ValidationConfig()
        >>> config.cache_dir
        PosixPath('cache')
        >>> config.strict_mode
        False
        >>> config.cache_labels
        True
        >>> config.cache_strategy
        <CacheStrategy.PROGRESSIVE: 'progressive'>
    """

    oak_adapter_string: str = Field(
        default="sqlite:obo:",
        description="Default OAK adapter string (e.g., 'sqlite:obo:', 'ols:')",
    )
    strict_mode: bool = Field(
        default=False,
        description="If True, treat warnings as errors for unconfigured prefixes",
    )
    cache_labels: bool = Field(
        default=True, description="If True, cache ontology labels to disk"
    )
    cache_enum_expansions: bool = Field(
        default=True,
        description="If True, cache expanded dynamic enum values to disk",
    )
    saturate_enum_caches: bool = Field(
        default=False,
        description="If True, materialize full enum closures and mark enum caches complete",
    )
    cache_strategy: CacheStrategy = Field(
        default=CacheStrategy.PROGRESSIVE,
        description="Caching strategy for dynamic enums: 'progressive' (default) or 'greedy'",
    )
    oak_config_path: Optional[Path] = Field(
        default=None,
        description="Path to oak_config.yaml with per-prefix adapter settings",
    )
    cache_dir: Path = Field(
        default=Path("cache"),
        description="Directory for caching ontology labels and dynamic enum expansions",
    )
    offline: bool = Field(
        default=False,
        description=(
            "If True, force offline validation: never build OAK adapters and "
            "resolve everything exclusively from the file cache, guaranteeing "
            "no external access"
        ),
    )

    def get_cache_dir(self) -> Path:
        """Get the cache directory, creating it if needed.

        Returns:
            Path to cache directory

        Examples:
            >>> config = ValidationConfig()
            >>> config.get_cache_dir().name
            'cache'
        """
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        return self.cache_dir
