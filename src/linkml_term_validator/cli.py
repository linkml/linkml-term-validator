"""CLI interface for linkml-term-validator.

This CLI supports three validation modes:
1. Schema validation - validates meaning fields in permissible values
2. Data validation - validates data against dynamic enums and bindings
3. Combined validation - both schema and data validation

For integration with LinkML's validator framework, see documentation.
"""

import re
from enum import Enum
from pathlib import Path
from typing import Optional

import typer
from linkml.validator import Validator  # type: ignore[import-untyped]
from linkml.validator.loaders import default_loader_for_file  # type: ignore[import-untyped]
from linkml.validator.report import Severity  # type: ignore[import-untyped]
from typing_extensions import Annotated

from linkml_term_validator.models import CacheStrategy, ValidationConfig
from linkml_term_validator.plugins import (
    BindingValidationPlugin,
    DynamicEnumPlugin,
)
from linkml_term_validator.utils import OntologyServiceUnavailableError
from linkml_term_validator.validator import EnumValidator

app = typer.Typer(
    help="linkml-term-validator: Validating external terms in LinkML schemas and data",
    pretty_exceptions_enable=False,
    rich_markup_mode=None,
)

# Exit code for "could not validate" (service outage), kept distinct from the
# code 1 used for genuine validation failures so callers/scripts can tell an
# ontology-service outage apart from invalid data.
EXIT_SERVICE_UNAVAILABLE = 2


class FailOn(str, Enum):
    """Which validation results should make ``validate-data`` exit non-zero.

    Results are printed either way; this only selects the exit code. ``ANY`` is
    the default because it preserves this command's long-standing behavior --
    quietly becoming *less* strict on upgrade is the failure mode this
    validator exists to prevent.

    Examples:
        >>> FailOn.ERROR.value
        'error'
        >>> FailOn("warn")
        <FailOn.WARN: 'warn'>
    """

    ANY = "any"
    """Any result at all fails, whatever its severity (default)."""

    ERROR = "error"
    """Only ERROR (and FATAL) fails -- the rule ``linkml-validate`` uses."""

    WARN = "warn"
    """WARN and above fails; INFO does not."""


# Severities that count as a failure under each mode. ANY is handled separately
# because it does not inspect severity at all.
_FAIL_ON_SEVERITIES = {
    FailOn.ERROR: {Severity.FATAL, Severity.ERROR},
    FailOn.WARN: {Severity.FATAL, Severity.ERROR, Severity.WARN},
}

# INFO is reachable now that severity_overrides can demote a mode to it, so it
# gets its own marker rather than borrowing the warning one.
_SEVERITY_EMOJI = {
    Severity.FATAL: "❌",
    Severity.ERROR: "❌",
    Severity.WARN: "⚠️ ",
    Severity.INFO: "ℹ️ ",
}


def _effective_fail_on(fail_on: FailOn, strict: bool) -> FailOn:
    """Fold ``--strict`` into the ``--fail-on`` threshold.

    ``--strict`` promises that a WARN exits non-zero. It never loosens anything:
    ``any`` already fails on warnings and stays ``any``; only ``error`` is raised
    to ``warn``. This keeps ``--strict`` a stable pin for CI regardless of what
    the default threshold does in a given release.

    Examples:
        >>> _effective_fail_on(FailOn.ERROR, strict=True)
        <FailOn.WARN: 'warn'>
        >>> _effective_fail_on(FailOn.ANY, strict=True)
        <FailOn.ANY: 'any'>
        >>> _effective_fail_on(FailOn.ERROR, strict=False)
        <FailOn.ERROR: 'error'>
    """
    if strict and fail_on == FailOn.ERROR:
        return FailOn.WARN
    return fail_on


def _counts_as_failure(results: list, fail_on: FailOn) -> bool:
    """Decide whether a file's results should make the process exit non-zero.

    Args:
        results: ValidationResults for a single data file
        fail_on: The configured threshold

    Returns:
        True if the process should exit non-zero because of these results
    """
    if not results:
        return False
    if fail_on == FailOn.ANY:
        return True
    failing = _FAIL_ON_SEVERITIES[fail_on]
    return any(result.severity in failing for result in results)


def _fail_service_unavailable(exc: OntologyServiceUnavailableError) -> typer.Exit:
    """Report an ontology-service outage and return an Exit with a distinct code.

    A network outage means terms could not be checked at all, which is different
    from data being invalid. Surfacing it as "unable to validate at this time"
    (rather than "term not found") avoids mislabeling every term as bad data.
    """
    typer.echo(
        "\n🌐 Unable to validate at this time: ontology service unavailable.\n"
        f"   {exc}\n"
        "   Terms could not be checked; this is not a data error. "
        "Retry when the ontology service is reachable, or use --offline to "
        "validate against the local cache only.",
        err=True,
    )
    return typer.Exit(code=EXIT_SERVICE_UNAVAILABLE)


@app.command()
def validate_schema(
    schema_path: Annotated[
        Path,
        typer.Argument(
            help="Path to LinkML YAML schema file",
            exists=True,
        ),
    ],
    adapter: Annotated[
        str,
        typer.Option(
            "--adapter",
            "-a",
            help="OAK adapter string (default: sqlite:obo:)",
        ),
    ] = "sqlite:obo:",
    strict: Annotated[
        bool,
        typer.Option(
            "--strict",
            help="Treat all warnings as errors",
        ),
    ] = False,
    no_cache: Annotated[
        bool,
        typer.Option(
            "--no-cache",
            help="Disable label caching",
        ),
    ] = False,
    cache_dir: Annotated[
        Path,
        typer.Option(
            "--cache-dir",
            help="Directory for caching ontology labels and dynamic enum expansions",
        ),
    ] = Path("cache"),
    config: Annotated[
        Optional[Path],
        typer.Option(
            "--config",
            "-c",
            help="Path to oak_config.yaml",
        ),
    ] = None,
    offline: Annotated[
        bool,
        typer.Option(
            "--offline",
            help="Force offline validation: resolve only from the cache, never access ontology services",
        ),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
            help="Verbose output",
        ),
    ] = False,
):
    """Validate meaning fields in schema enum permissible values.

    Checks that 'meaning' fields reference valid ontology terms with correct labels.

    Examples:
        linkml-term-validator validate-schema schema.yaml
        linkml-term-validator validate-schema --strict schema.yaml
        linkml-term-validator validate-schema --config oak_config.yaml schema.yaml
        linkml-term-validator validate-schema --offline schema.yaml
    """
    validation_config = ValidationConfig(
        oak_adapter_string=adapter,
        strict_mode=strict,
        cache_labels=not no_cache,
        cache_dir=cache_dir,
        oak_config_path=config,
        offline=offline,
    )

    validator = EnumValidator(validation_config)
    try:
        result = validator.validate_schema(schema_path)
    except OntologyServiceUnavailableError as exc:
        raise _fail_service_unavailable(exc) from exc

    if verbose or result.has_errors() or result.has_warnings():
        result.print_summary(verbose=verbose)

    # In offline mode unresolved terms are reported as errors above (not skipped),
    # so the "validation skipped / add to oak_config" note would be misleading.
    unknown_prefixes = validator.get_unknown_prefixes()
    if unknown_prefixes and not offline:
        typer.echo("\n⚠️  Unknown prefixes encountered (validation skipped):")
        for prefix in sorted(unknown_prefixes):
            typer.echo(f"  - {prefix}")
        typer.echo("\nConsider adding these to oak_config.yaml to enable validation.")

    if result.error_count() > 0:
        typer.echo(
            f"\n❌ Validation failed: {result.error_count()} error(s), {result.warning_count()} warning(s)",
            err=True,
        )
        raise typer.Exit(code=1)
    elif result.warning_count() > 0:
        typer.echo(f"\n⚠️  Validation completed with {result.warning_count()} warning(s)")
    else:
        if not verbose:
            typer.echo("✅")


@app.command()
def validate_data(
    data_paths: Annotated[
        list[Path],
        typer.Argument(
            help="Path(s) to data file(s) (YAML/JSON)",
        ),
    ],
    schema_path: Annotated[
        Path,
        typer.Option(
            "--schema",
            "-s",
            help="Path to LinkML schema",
            exists=True,
        ),
    ],
    target_class: Annotated[
        Optional[str],
        typer.Option(
            "--target-class",
            "-t",
            help="Target class for validation",
        ),
    ] = None,
    validate_bindings: Annotated[
        bool,
        typer.Option(
            "--bindings/--no-bindings",
            help="Validate binding constraints",
        ),
    ] = True,
    validate_dynamic_enums: Annotated[
        bool,
        typer.Option(
            "--dynamic-enums/--no-dynamic-enums",
            help="Validate against dynamic enums",
        ),
    ] = True,
    validate_labels: Annotated[
        bool,
        typer.Option(
            "--labels/--no-labels",
            help="Validate labels match ontology (default: enabled)",
        ),
    ] = True,
    lenient: Annotated[
        bool,
        typer.Option(
            "--lenient/--no-lenient",
            help="Lenient mode: don't fail when term IDs are not found in ontology",
        ),
    ] = False,
    adapter: Annotated[
        str,
        typer.Option(
            "--adapter",
            "-a",
            help="OAK adapter string (default: sqlite:obo:)",
        ),
    ] = "sqlite:obo:",
    no_cache: Annotated[
        bool,
        typer.Option(
            "--no-cache",
            help="Disable file-based label and enum caching",
        ),
    ] = False,
    cache_dir: Annotated[
        Path,
        typer.Option(
            "--cache-dir",
            help="Directory for caching ontology labels and dynamic enum expansions",
        ),
    ] = Path("cache"),
    cache_enum_expansions: Annotated[
        bool,
        typer.Option(
            "--cache-enum-expansions/--no-cache-enum-expansions",
            help="Enable file-based caching of expanded dynamic enum values",
        ),
    ] = True,
    saturate_enum_caches: Annotated[
        bool,
        typer.Option(
            "--saturate-enum-caches/--no-saturate-enum-caches",
            help="Materialize full dynamic enum closures and mark enum caches complete",
        ),
    ] = False,
    config: Annotated[
        Optional[Path],
        typer.Option(
            "--config",
            "-c",
            help="Path to oak_config.yaml",
        ),
    ] = None,
    cache_strategy: Annotated[
        str,
        typer.Option(
            "--cache-strategy",
            help="Caching strategy for dynamic enums: 'progressive' (lazy, default) or 'greedy' (expand upfront)",
        ),
    ] = "progressive",
    offline: Annotated[
        bool,
        typer.Option(
            "--offline",
            help="Force offline validation: resolve only from the cache, never access ontology services",
        ),
    ] = False,
    fail_on: Annotated[
        FailOn,
        typer.Option(
            "--fail-on",
            help=(
                "Which results cause a non-zero exit: 'any' (default, every result), "
                "'error' (only ERROR, matching linkml-validate), or 'warn' (WARN and above)"
            ),
        ),
    ] = FailOn.ANY,
    strict: Annotated[
        bool,
        typer.Option(
            "--strict",
            help=(
                "Exit non-zero on warnings, even under --fail-on error. Affects the "
                "exit code only; results still print as WARN. Conflicts with --lenient"
            ),
        ),
    ] = False,
):
    """Validate data against dynamic enums and binding constraints.

    Validates data instances against:
    - Dynamic enum definitions (reachable_from, matches, concepts)
    - Binding constraints on nested object fields

    Accepts multiple data files - each is validated independently.

    All results are always printed. --fail-on controls only the exit code, so
    lowering it never hides a problem. Use --fail-on error to match how
    linkml-validate decides its exit code, which is what makes a
    severity_overrides demotion take effect here too.

    --strict is the stable way for CI to insist that warnings fail. It raises
    --fail-on error to warn and leaves the default alone. Unlike --strict on
    validate-schema, it does not promote warnings to ERROR in the report: it
    changes the exit code only, and results still print as WARN. It cannot be
    combined with --lenient, which switches term-existence checks off.

    Examples:
        linkml-term-validator validate-data data.yaml --schema schema.yaml
        linkml-term-validator validate-data data.yaml -s schema.yaml -t Person
        linkml-term-validator validate-data *.yaml -s schema.yaml --labels
        linkml-term-validator validate-data data.yaml -s schema.yaml --offline
        linkml-term-validator validate-data data.yaml -s schema.yaml --fail-on error
        linkml-term-validator validate-data data.yaml -s schema.yaml --strict
    """
    # Verify all data files exist
    for data_path in data_paths:
        if not data_path.exists():
            typer.echo(f"❌ File not found: {data_path}", err=True)
            raise typer.Exit(code=1)

    # Parse cache strategy
    strategy = CacheStrategy(cache_strategy)

    if strict and lenient:
        raise typer.BadParameter(
            "--strict and --lenient conflict: --lenient switches term-existence "
            "checks off, so --strict could not make them fail. Drop one."
        )

    # --strict can only tighten the threshold, never loosen it. The requested
    # value is kept so messages can say what the user typed.
    requested_fail_on = fail_on
    fail_on = _effective_fail_on(fail_on, strict)

    # Build plugin list based on options
    plugins = []

    if validate_dynamic_enums:
        plugins.append(
            DynamicEnumPlugin(
                oak_adapter_string=adapter,
                cache_labels=not no_cache,
                cache_dir=cache_dir,
                oak_config_path=config,
                cache_enum_expansions=cache_enum_expansions and not no_cache,
                saturate_enum_caches=saturate_enum_caches and not no_cache,
                cache_strategy=strategy,
                offline=offline,
            )
        )

    if validate_bindings:
        plugins.append(
            BindingValidationPlugin(
                oak_adapter_string=adapter,
                validate_labels=validate_labels,
                strict=not lenient,
                cache_labels=not no_cache,
                cache_dir=cache_dir,
                oak_config_path=config,
                cache_enum_expansions=cache_enum_expansions and not no_cache,
                saturate_enum_caches=saturate_enum_caches and not no_cache,
                cache_strategy=strategy,
                offline=offline,
            )
        )

    if not plugins:
        typer.echo("⚠️  No validation enabled. Use --bindings or --dynamic-enums", err=True)
        raise typer.Exit(code=1)

    # Create validator with plugins
    validator = Validator(
        schema=str(schema_path),
        validation_plugins=plugins,
    )

    # Validate each data file
    total_issues = 0
    files_with_issues = []
    failed_files = []

    for data_path in data_paths:
        loader = default_loader_for_file(data_path)
        try:
            report = validator.validate_source(loader, target_class=target_class)
        except OntologyServiceUnavailableError as exc:
            raise _fail_service_unavailable(exc) from exc

        if len(report.results) == 0:
            if len(data_paths) > 1:
                typer.echo(f"✅ {data_path.name}")
        else:
            # Every result is reported regardless of --fail-on; the threshold
            # decides only the exit code, never what the user gets to see.
            files_with_issues.append(data_path)
            if _counts_as_failure(report.results, fail_on):
                failed_files.append(data_path)
            total_issues += len(report.results)
            if len(data_paths) > 1:
                typer.echo(f"\n❌ {data_path.name} - {len(report.results)} issue(s):")
            else:
                typer.echo(f"\n❌ Validation failed with {len(report.results)} issue(s):\n")
            for result in report.results:
                severity_emoji = _SEVERITY_EMOJI.get(result.severity, "⚠️ ")
                typer.echo(f"  {severity_emoji} {result.severity.name}: {result.message}")
                if result.context:
                    for ctx in result.context:
                        typer.echo(f"      {ctx}")

    # Output summary
    if len(data_paths) > 1:
        typer.echo("")
        if files_with_issues:
            # Under the default threshold every file with issues is a failure, so
            # keep the long-standing wording; "had issues" only appears when
            # --fail-on has actually spared some of them.
            noun = "failed" if len(failed_files) == len(files_with_issues) else "had issues"
            typer.echo(
                f"Summary: {len(files_with_issues)}/{len(data_paths)} files {noun}, "
                f"{total_issues} total issue(s)"
            )
        else:
            typer.echo(f"✅ All {len(data_paths)} files passed validation")
    elif not files_with_issues:
        # Single file success
        typer.echo("✅ Validation passed")

    if files_with_issues and not failed_files:
        threshold = f"--fail-on {requested_fail_on.value}"
        if fail_on != requested_fail_on:
            threshold += f" (raised to {fail_on.value} by --strict)"
        typer.echo(
            f"\n⚠️  {total_issues} issue(s) reported, none at or above the "
            f"{threshold} threshold; exiting 0."
        )

    if failed_files:
        raise typer.Exit(code=1)


@app.command(name="validate")
def validate_all(
    input_path: Annotated[
        Path,
        typer.Argument(
            help="Path to schema or data file",
            exists=True,
        ),
    ],
    schema_path: Annotated[
        Optional[Path],
        typer.Option(
            "--schema",
            "-s",
            help="Path to LinkML schema (for data validation)",
            exists=True,
        ),
    ] = None,
    adapter: Annotated[
        str,
        typer.Option(
            "--adapter",
            "-a",
            help="OAK adapter string (default: sqlite:obo:)",
        ),
    ] = "sqlite:obo:",
    strict: Annotated[
        bool,
        typer.Option(
            "--strict",
            help=(
                "Treat warnings as errors. Schema mode promotes them to ERROR; "
                "data mode makes them exit non-zero (exit code only)"
            ),
        ),
    ] = False,
    lenient: Annotated[
        bool,
        typer.Option(
            "--lenient/--no-lenient",
            help="Lenient mode: don't fail when term IDs are not found (data validation)",
        ),
    ] = False,
    no_cache: Annotated[
        bool,
        typer.Option(
            "--no-cache",
            help="Disable file-based label and enum caching",
        ),
    ] = False,
    cache_dir: Annotated[
        Path,
        typer.Option(
            "--cache-dir",
            help="Directory for caching ontology labels and dynamic enum expansions",
        ),
    ] = Path("cache"),
    cache_enum_expansions: Annotated[
        bool,
        typer.Option(
            "--cache-enum-expansions/--no-cache-enum-expansions",
            help="Enable file-based caching of expanded dynamic enum values",
        ),
    ] = True,
    saturate_enum_caches: Annotated[
        bool,
        typer.Option(
            "--saturate-enum-caches/--no-saturate-enum-caches",
            help="Materialize full dynamic enum closures and mark enum caches complete",
        ),
    ] = False,
    config: Annotated[
        Optional[Path],
        typer.Option(
            "--config",
            "-c",
            help="Path to oak_config.yaml",
        ),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
            help="Verbose output",
        ),
    ] = False,
    cache_strategy: Annotated[
        str,
        typer.Option(
            "--cache-strategy",
            help="Caching strategy for dynamic enums: 'progressive' (lazy, default) or 'greedy' (expand upfront)",
        ),
    ] = "progressive",
    offline: Annotated[
        bool,
        typer.Option(
            "--offline",
            help="Force offline validation: resolve only from the cache, never access ontology services",
        ),
    ] = False,
    fail_on: Annotated[
        FailOn,
        typer.Option(
            "--fail-on",
            help=(
                "Data validation: which results cause a non-zero exit: 'any' (default), "
                "'error' (matching linkml-validate), or 'warn'"
            ),
        ),
    ] = FailOn.ANY,
):
    """Validate schemas or data (auto-detect mode).

    - If --schema is NOT provided: validates input as a LinkML schema
    - If --schema IS provided: validates input as data against the schema

    --strict applies in both modes. In schema mode it promotes warnings to
    ERROR in the report. In data mode it only makes warnings exit non-zero,
    whatever --fail-on says, and it conflicts with --lenient.

    Examples:
        # Schema validation (default)
        linkml-term-validator validate schema.yaml

        # Data validation
        linkml-term-validator validate data.yaml --schema schema.yaml

        # Both at once
        linkml-term-validator validate schema.yaml --verbose
    """
    if schema_path:
        # Data validation mode - call validate_data directly
        validate_data(
            data_paths=[input_path],
            schema_path=schema_path,
            target_class=None,
            validate_bindings=True,
            validate_dynamic_enums=True,
            validate_labels=True,
            lenient=lenient,
            adapter=adapter,
            no_cache=no_cache,
            cache_dir=cache_dir,
            cache_enum_expansions=cache_enum_expansions,
            saturate_enum_caches=saturate_enum_caches,
            config=config,
            cache_strategy=cache_strategy,
            offline=offline,
            fail_on=fail_on,
            strict=strict,
        )
    else:
        # Schema validation mode (backward compatible) - call validate_schema directly
        validate_schema(
            schema_path=input_path,
            adapter=adapter,
            strict=strict,
            no_cache=no_cache,
            cache_dir=cache_dir,
            config=config,
            offline=offline,
            verbose=verbose,
        )


@app.command(name="migrate-cache")
def migrate_cache(
    cache_dir: Annotated[
        Path,
        typer.Option(
            "--cache-dir",
            help="Directory containing cache files",
        ),
    ] = Path("cache"),
    adapter: Annotated[
        str,
        typer.Option(
            "--adapter",
            "-a",
            help="OAK adapter string (default: sqlite:obo:)",
        ),
    ] = "sqlite:obo:",
    config: Annotated[
        Optional[Path],
        typer.Option(
            "--config",
            "-c",
            help="Path to oak_config.yaml",
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Show what would change without writing",
        ),
    ] = False,
    refresh_labels: Annotated[
        bool,
        typer.Option(
            "--refresh-labels",
            help="Re-fetch labels from ontology and update changed ones",
        ),
    ] = False,
    sort_only: Annotated[
        bool,
        typer.Option(
            "--sort-only",
            help="Only sort and deduplicate cache files without fetching labels",
        ),
    ] = False,
):
    """Migrate and normalize cache files to eliminate spurious diffs.

    This command normalizes existing cache files by:
    - Sorting entries by CURIE for deterministic output
    - Deduplicating entries (keeping the latest timestamp)
    - Optionally re-fetching labels to update relabelings

    Use this after upgrading linkml-term-validator to normalize existing
    cache files and prevent spurious diffs in version control.

    Examples:
        linkml-term-validator migrate-cache
        linkml-term-validator migrate-cache --refresh-labels
        linkml-term-validator migrate-cache --dry-run
        linkml-term-validator migrate-cache --sort-only
    """
    import csv
    from datetime import datetime

    from linkml_term_validator.plugins.dynamic_enum_plugin import DynamicEnumPlugin

    if not cache_dir.exists():
        typer.echo(f"Cache directory not found: {cache_dir}", err=True)
        raise typer.Exit(code=1)

    # Find all terms.csv files
    terms_files = sorted(cache_dir.glob("*/terms.csv"))
    if not terms_files:
        typer.echo("No cache files found to migrate.")
        return

    # Create a plugin instance for label lookups if refreshing
    plugin = None
    if refresh_labels:
        plugin = DynamicEnumPlugin(
            oak_adapter_string=adapter,
            cache_labels=False,  # Don't write cache during migration
            cache_dir=cache_dir,
            oak_config_path=config,
        )

    total_files = 0
    total_updated = 0
    total_relabeled = 0
    total_removed_dupes = 0

    for terms_file in terms_files:
        total_files += 1

        # Load existing entries preserving all data
        entries: dict[str, dict[str, str]] = {}
        dupes = 0
        with open(terms_file) as f:
            reader = csv.DictReader(f)
            for row in reader:
                curie = row["curie"]
                if curie in entries:
                    dupes += 1
                    # Keep the one with the later timestamp
                    existing_ts = entries[curie].get("retrieved_at", "")
                    new_ts = row.get("retrieved_at", "")
                    if new_ts > existing_ts:
                        entries[curie] = {
                            "label": row["label"],
                            "retrieved_at": new_ts,
                        }
                else:
                    entries[curie] = {
                        "label": row["label"],
                        "retrieved_at": row.get("retrieved_at", ""),
                    }

        total_removed_dupes += dupes

        # Optionally refresh labels from ontology
        relabeled = 0
        if refresh_labels and plugin and not sort_only:
            for curie in list(entries.keys()):
                old_label = entries[curie]["label"]
                try:
                    new_label = plugin.get_ontology_label(curie)
                except OntologyServiceUnavailableError as exc:
                    raise _fail_service_unavailable(exc) from exc
                if new_label and new_label != old_label:
                    relabeled += 1
                    if dry_run:
                        typer.echo(f"  {curie}: '{old_label}' -> '{new_label}'")
                    else:
                        entries[curie] = {
                            "label": new_label,
                            "retrieved_at": datetime.now().isoformat(),
                        }

        total_relabeled += relabeled

        # Check if file needs rewriting (sort order, dupes, relabelings)
        sorted_curies = sorted(entries.keys())
        needs_rewrite = dupes > 0 or relabeled > 0

        # Check if already sorted
        if not needs_rewrite:
            existing_curies = []
            with open(terms_file) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    existing_curies.append(row["curie"])
            if existing_curies != sorted_curies:
                needs_rewrite = True

        if needs_rewrite:
            total_updated += 1
            status_parts = []
            if dupes > 0:
                status_parts.append(f"{dupes} dupes removed")
            if relabeled > 0:
                status_parts.append(f"{relabeled} relabeled")
            if not status_parts:
                status_parts.append("sorted")
            status = ", ".join(status_parts)

            if dry_run:
                typer.echo(f"  Would update {terms_file} ({status})")
            else:
                with open(terms_file, "w", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=["curie", "label", "retrieved_at"], lineterminator="\n")
                    writer.writeheader()
                    for c in sorted_curies:
                        writer.writerow(
                            {
                                "curie": c,
                                "label": entries[c]["label"],
                                "retrieved_at": entries[c]["retrieved_at"],
                            }
                        )
                typer.echo(f"  Updated {terms_file} ({status})")
        else:
            if dry_run:
                typer.echo(f"  {terms_file} - no changes needed")

    # Summary
    typer.echo(f"\nMigration {'preview' if dry_run else 'complete'}:")
    typer.echo(f"  Files scanned: {total_files}")
    typer.echo(f"  Files {'needing' if dry_run else 'updated'}: {total_updated}")
    if total_removed_dupes > 0:
        typer.echo(f"  Duplicates removed: {total_removed_dupes}")
    if total_relabeled > 0:
        typer.echo(f"  Labels updated: {total_relabeled}")


@app.command()
def validate_text_file(
    file_path: Annotated[
        Path,
        typer.Argument(
            help="Path to the text or markdown file to validate",
            exists=True,
        ),
    ],
    regex: Annotated[
        str,
        typer.Option(
            "--regex",
            "-r",
            help='Regex pattern with capture groups for CURIE and label. '
            'Default matches: @term CURIE "label"',
        ),
    ] = r'@term (\S+) "([^"]*)"',
    curie_group: Annotated[
        int,
        typer.Option(
            "--curie-group",
            help="Capture group index (1-based) for the CURIE",
        ),
    ] = 1,
    label_group: Annotated[
        int,
        typer.Option(
            "--label-group",
            help="Capture group index (1-based) for the label",
        ),
    ] = 2,
    strict: Annotated[
        bool,
        typer.Option(
            "--strict",
            help="Treat unresolvable CURIEs as errors (even for unconfigured prefixes)",
        ),
    ] = False,
    adapter: Annotated[
        str,
        typer.Option(
            "--adapter",
            "-a",
            help="OAK adapter string (default: sqlite:obo:)",
        ),
    ] = "sqlite:obo:",
    config: Annotated[
        Optional[Path],
        typer.Option(
            "--config",
            "-c",
            help="Path to oak_config.yaml",
        ),
    ] = None,
    no_cache: Annotated[
        bool,
        typer.Option(
            "--no-cache",
            help="Disable label caching",
        ),
    ] = False,
    cache_dir: Annotated[
        Path,
        typer.Option(
            "--cache-dir",
            help="Directory for caching ontology labels",
        ),
    ] = Path("cache"),
    offline: Annotated[
        bool,
        typer.Option(
            "--offline",
            help="Force offline validation: resolve only from the cache, never access ontology services",
        ),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
            help="Verbose output: show each validated term",
        ),
    ] = False,
):
    """Validate ontology term CURIEs and labels embedded in a text or markdown file.

    Reads the file, extracts CURIE+label pairs using the specified regex,
    then resolves each CURIE via OAK and checks that the label matches.

    With --strict, CURIEs that cannot be resolved are reported as errors
    rather than silently skipped.

    Examples:

        linkml-term-validator validate-text-file document.md

        linkml-term-validator validate-text-file document.md \\
          --regex '@term (\\S+) "([^"]*)"' \\
          --curie-group 1 --label-group 2 \\
          --config oak_config.yaml --strict -v
    """
    # Compile regex, bail out early on syntax error
    try:
        pattern = re.compile(regex)
    except re.error as exc:
        typer.echo(f"❌ Invalid regex pattern: {exc}", err=True)
        raise typer.Exit(code=1)

    # Validate group indices against the pattern
    num_groups = pattern.groups
    for grp_name, grp_idx in [("--curie-group", curie_group), ("--label-group", label_group)]:
        if grp_idx < 1 or grp_idx > num_groups:
            typer.echo(
                f"❌ {grp_name} {grp_idx} is out of range "
                f"(pattern has {num_groups} group(s))",
                err=True,
            )
            raise typer.Exit(code=1)

    # Extract (curie, label, location) triples from the file
    text = file_path.read_text(encoding="utf-8")
    pairs: list[tuple[str, str, str]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for match in pattern.finditer(line):
            curie = match.group(curie_group)
            label = match.group(label_group)
            location = f"line:{line_no}"
            pairs.append((curie, label, location))

    if not pairs:
        typer.echo(f"⚠️  No matches found in {file_path} with pattern: {regex}")
        return

    # Set up EnumValidator with the given config
    validation_config = ValidationConfig(
        oak_adapter_string=adapter,
        strict_mode=strict,
        cache_labels=not no_cache,
        cache_dir=cache_dir,
        oak_config_path=config,
        offline=offline,
    )
    validator = EnumValidator(validation_config)

    try:
        issues = validator.validate_curie_label_pairs(pairs)
    except OntologyServiceUnavailableError as exc:
        raise _fail_service_unavailable(exc) from exc

    # Print results
    if verbose:
        valid_curies = {issue.value_name for issue in issues}
        for curie, label, location in pairs:
            if curie in valid_curies:
                pass  # will be shown in error block below
            else:
                typer.echo(f"  ✅ {location} {curie} \"{label}\"")

    if issues:
        for issue in issues:
            typer.echo(
                f"  ❌ {issue.enum_name} {issue.value_name}: {issue.message}"
            )

    # Offline mode reports unresolved terms as errors, so the "skipped" note
    # (and the oak_config suggestion) would be misleading there.
    unknown_prefixes = validator.get_unknown_prefixes()
    if unknown_prefixes and not offline:
        typer.echo("\n⚠️  Unknown prefixes encountered (validation skipped):")
        for prefix in sorted(unknown_prefixes):
            typer.echo(f"  - {prefix}")
        typer.echo("\nConsider adding these to oak_config.yaml to enable validation.")

    error_count = sum(1 for i in issues if i.is_error())
    total = len(pairs)

    if error_count > 0:
        typer.echo(
            f"\n❌ Validation failed: {error_count} error(s) in {total} CURIE(s)",
            err=True,
        )
        raise typer.Exit(code=1)
    else:
        if not verbose:
            typer.echo(f"✅ All {total} CURIE(s) validated successfully")
        else:
            typer.echo(f"\n✅ All {total} CURIE(s) validated successfully")


def main():
    """Main entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
