"""CLI interface for linkml-term-validator.

This CLI supports three validation modes:
1. Schema validation - validates meaning fields in permissible values
2. Data validation - validates data against dynamic enums and bindings
3. Combined validation - both schema and data validation

For integration with LinkML's validator framework, see documentation.
"""

from pathlib import Path
from typing import Optional

import typer
from linkml.validator import Validator  # type: ignore[import-untyped]
from linkml.validator.loaders import default_loader_for_file  # type: ignore[import-untyped]
from typing_extensions import Annotated

from linkml_term_validator.models import CacheStrategy, ValidationConfig
from linkml_term_validator.plugins import (
    BindingValidationPlugin,
    DynamicEnumPlugin,
)
from linkml_term_validator.validator import EnumValidator

app = typer.Typer(
    help="linkml-term-validator: Validating external terms in LinkML schemas and data",
    pretty_exceptions_enable=False,
    rich_markup_mode=None,
)


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
            help="Directory for caching ontology labels",
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
    """
    validation_config = ValidationConfig(
        oak_adapter_string=adapter,
        strict_mode=strict,
        cache_labels=not no_cache,
        cache_dir=cache_dir,
        oak_config_path=config,
    )

    validator = EnumValidator(validation_config)
    result = validator.validate_schema(schema_path)

    if verbose or result.has_errors() or result.has_warnings():
        result.print_summary(verbose=verbose)

    unknown_prefixes = validator.get_unknown_prefixes()
    if unknown_prefixes:
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
            help="Validate labels match ontology",
        ),
    ] = False,
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
    cache_dir: Annotated[
        Path,
        typer.Option(
            "--cache-dir",
            help="Directory for caching ontology labels",
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
    cache_strategy: Annotated[
        str,
        typer.Option(
            "--cache-strategy",
            help="Caching strategy for dynamic enums: 'progressive' (lazy, default) or 'greedy' (expand upfront)",
        ),
    ] = "progressive",
):
    """Validate data against dynamic enums and binding constraints.

    Validates data instances against:
    - Dynamic enum definitions (reachable_from, matches, concepts)
    - Binding constraints on nested object fields

    Accepts multiple data files - each is validated independently.

    Examples:
        linkml-term-validator validate-data data.yaml --schema schema.yaml
        linkml-term-validator validate-data data.yaml -s schema.yaml -t Person
        linkml-term-validator validate-data *.yaml -s schema.yaml --labels
    """
    # Verify all data files exist
    for data_path in data_paths:
        if not data_path.exists():
            typer.echo(f"❌ File not found: {data_path}", err=True)
            raise typer.Exit(code=1)

    # Parse cache strategy
    strategy = CacheStrategy(cache_strategy)

    # Build plugin list based on options
    plugins = []

    if validate_dynamic_enums:
        plugins.append(
            DynamicEnumPlugin(
                oak_adapter_string=adapter,
                cache_dir=cache_dir,
                oak_config_path=config,
                cache_strategy=strategy,
            )
        )

    if validate_bindings:
        plugins.append(
            BindingValidationPlugin(
                oak_adapter_string=adapter,
                validate_labels=validate_labels,
                strict=not lenient,
                cache_dir=cache_dir,
                oak_config_path=config,
                cache_strategy=strategy,
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
    failed_files = []

    for data_path in data_paths:
        loader = default_loader_for_file(data_path)
        report = validator.validate_source(loader, target_class=target_class)

        if len(report.results) == 0:
            if len(data_paths) > 1:
                typer.echo(f"✅ {data_path.name}")
        else:
            failed_files.append(data_path)
            total_issues += len(report.results)
            if len(data_paths) > 1:
                typer.echo(f"\n❌ {data_path.name} - {len(report.results)} issue(s):")
            else:
                typer.echo(f"\n❌ Validation failed with {len(report.results)} issue(s):\n")
            for result in report.results:
                severity_emoji = "❌" if result.severity.name == "ERROR" else "⚠️ "
                typer.echo(f"  {severity_emoji} {result.severity.name}: {result.message}")
                if result.context:
                    for ctx in result.context:
                        typer.echo(f"      {ctx}")

    # Output summary
    if len(data_paths) > 1:
        typer.echo("")
        if failed_files:
            typer.echo(
                f"Summary: {len(failed_files)}/{len(data_paths)} files failed, "
                f"{total_issues} total issue(s)"
            )
        else:
            typer.echo(f"✅ All {len(data_paths)} files passed validation")
    elif not failed_files:
        # Single file success
        typer.echo("✅ Validation passed")

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
            help="Treat all warnings as errors (schema validation)",
        ),
    ] = False,
    lenient: Annotated[
        bool,
        typer.Option(
            "--lenient/--no-lenient",
            help="Lenient mode: don't fail when term IDs are not found (data validation)",
        ),
    ] = False,
    cache_dir: Annotated[
        Path,
        typer.Option(
            "--cache-dir",
            help="Directory for caching ontology labels",
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
):
    """Validate schemas or data (auto-detect mode).

    - If --schema is NOT provided: validates input as a LinkML schema
    - If --schema IS provided: validates input as data against the schema

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
            validate_labels=False,
            lenient=lenient,
            adapter=adapter,
            cache_dir=cache_dir,
            config=config,
            cache_strategy=cache_strategy,
        )
    else:
        # Schema validation mode (backward compatible) - call validate_schema directly
        validate_schema(
            schema_path=input_path,
            adapter=adapter,
            strict=strict,
            no_cache=False,
            cache_dir=cache_dir,
            config=config,
            verbose=verbose,
        )


def main():
    """Main entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
