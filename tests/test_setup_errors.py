"""Tests for exit code 3: validation cannot run as set up.

Exit 1 means the data failed validation and exit 2 a transient outage worth
retrying. Exit 3 means someone has to fix the setup first: invalid input or
configuration, or an adapter that cannot be built or loaded (#78, covered in
test_adapter_unavailable.py). These cases used to exit 1 (or 1 with a
traceback), indistinguishable from invalid data, and a mistyped --config path
was silently ignored, so the run went ahead without the user's configuration.

Configuration errors are a ConfigurationError, a ValueError subclass, so
Python callers that catch ValueError are unaffected.
"""

import csv
from pathlib import Path

import pytest
from typer.testing import CliRunner

from linkml_term_validator.cli import EXIT_SETUP_ERROR, app
from linkml_term_validator.plugins import BindingValidationPlugin, DynamicEnumPlugin
from linkml_term_validator.utils import ConfigurationError, OntologyAccess
from linkml_term_validator.validator import EnumValidator

DATA = Path("tests/data")
OAK_CONFIG = DATA / "test_oak_config.yaml"


@pytest.fixture
def runner():
    return CliRunner()


def _config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "oak_config.yaml"
    path.write_text(text)
    return path


# ---------------------------------------------------------------------------
# Reading oak_config.yaml
# ---------------------------------------------------------------------------


def test_missing_config_path_is_an_error(tmp_path):
    missing = tmp_path / "typo_oak_config.yaml"
    with pytest.raises(ConfigurationError, match="OAK config file not found"):
        OntologyAccess(cache_labels=False, oak_config_path=missing)


def test_configuration_error_is_a_value_error(tmp_path):
    """Existing ``except ValueError`` handlers keep working."""
    with pytest.raises(ValueError):
        OntologyAccess(cache_labels=False, oak_config_path=tmp_path / "missing.yaml")


@pytest.mark.parametrize(
    "text,message",
    [
        ("ontology_adapters: [unclosed\n", "is not valid YAML"),
        ("- TEST\n- GO\n", "must be a mapping of settings"),
        ("ontology_adapters: sqlite:obo:go\n", "must map each prefix to an adapter"),
        ("ontology_adapters:\n  GO: 42\n", "must map each prefix to an adapter"),
    ],
)
def test_malformed_config_is_an_error(tmp_path, text, message):
    with pytest.raises(ConfigurationError, match=message):
        OntologyAccess(cache_labels=False, oak_config_path=_config(tmp_path, text))


def test_empty_config_and_skip_entries_are_valid(tmp_path):
    empty = OntologyAccess(cache_labels=False, oak_config_path=_config(tmp_path, ""))
    assert empty.oak_config == {}

    config = _config(tmp_path, "ontology_adapters:\n  GO: sqlite:obo:go\n  SKIPPED:\n")
    access = OntologyAccess(cache_labels=False, oak_config_path=config)
    assert access.is_prefix_configured("GO")
    assert not access.is_prefix_configured("SKIPPED")
    assert access.get_adapter("SKIPPED") is None


@pytest.mark.parametrize(
    "text,message",
    [
        ("cache_strategy: eager\n", "cache_strategy must be one of: progressive, greedy"),
        ("cache_enum_expansions: maybe\n", "cache_enum_expansions must be a boolean"),
        ("severity_overrides:\n  not_a_mode: ERROR\n", "Unknown severity_overrides key"),
        ("severity_overrides:\n  binding_label_mismatch: LOUD\n", "Unknown severity"),
        ("service_retries: lots\n", "service_retries must be a non-negative integer"),
        ("check_not4curation: yes please\n", "check_not4curation must be a boolean"),
    ],
)
def test_invalid_config_values_are_configuration_errors(tmp_path, text, message):
    config = _config(tmp_path, text)
    with pytest.raises(ConfigurationError, match=message):
        BindingValidationPlugin(cache_labels=False, oak_config_path=config)


def test_invalid_constructor_values_are_configuration_errors():
    with pytest.raises(ConfigurationError, match="cache_strategy must be one of"):
        DynamicEnumPlugin(cache_labels=False, cache_strategy="eager")  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError, match="Unknown severity_overrides key"):
        BindingValidationPlugin(cache_labels=False, severity_overrides={"nope": "ERROR"})


# ---------------------------------------------------------------------------
# CLI: every command reports a setup error with exit code 3
# ---------------------------------------------------------------------------


def _cache_with_one_term(tmp_path: Path) -> Path:
    cache_dir = tmp_path / "cache"
    terms = cache_dir / "test" / "terms.csv"
    terms.parent.mkdir(parents=True)
    with open(terms, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["curie", "label", "retrieved_at"])
        writer.writeheader()
        writer.writerow({"curie": "TEST:0000001", "label": "root term", "retrieved_at": ""})
    return cache_dir


def _text_file(tmp_path: Path) -> Path:
    path = tmp_path / "doc.md"
    path.write_text('- @term TEST:0000001 "root term"\n')
    return path


def _command_with_config(command: str, config: Path, tmp_path: Path) -> list[str]:
    """Arguments that run ``command`` with ``config`` and no label cache writes."""
    if command in ("validate-schema", "validate"):
        return [command, str(DATA / "test_schema.yaml"), "--config", str(config), "--no-cache"]
    if command == "validate-data":
        return [
            "validate-data",
            str(DATA / "dynamic_enum_valid_data.yaml"),
            "--schema",
            str(DATA / "dynamic_enum_schema.yaml"),
            "--target-class",
            "Sample",
            "--config",
            str(config),
            "--no-cache",
        ]
    if command == "validate-text-file":
        return [
            "validate-text-file",
            str(_text_file(tmp_path)),
            "--config",
            str(config),
            "--no-cache",
        ]
    assert command == "migrate-cache"
    return [
        "migrate-cache",
        "--cache-dir",
        str(_cache_with_one_term(tmp_path)),
        "--refresh-labels",
        "--config",
        str(config),
    ]


COMMANDS = ["validate-schema", "validate", "validate-data", "validate-text-file", "migrate-cache"]


@pytest.mark.parametrize("command", COMMANDS)
def test_cli_missing_config_exits_3(runner, tmp_path, command):
    missing = tmp_path / "typo_oak_config.yaml"
    result = runner.invoke(app, _command_with_config(command, missing, tmp_path))

    assert result.exit_code == EXIT_SETUP_ERROR == 3, result.output
    assert f"Invalid configuration: OAK config file not found: {missing}" in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize("command", ["validate-schema", "validate-data", "validate-text-file"])
def test_cli_invalid_config_value_exits_3(runner, tmp_path, command):
    config = _config(
        tmp_path,
        f"ontology_adapters:\n  TEST: simpleobo:{DATA}/test_ontology.obo\n"
        "service_retries: lots\n",
    )
    result = runner.invoke(app, _command_with_config(command, config, tmp_path))

    assert result.exit_code == EXIT_SETUP_ERROR, result.output
    assert "Invalid configuration: service_retries must be a non-negative integer" in result.output


def test_cli_unknown_severity_override_exits_3(runner, tmp_path):
    config = _config(
        tmp_path,
        f"ontology_adapters:\n  TEST: simpleobo:{DATA}/test_ontology.obo\n"
        "severity_overrides:\n  binding_lable_mismatch: WARN\n",
    )
    result = runner.invoke(app, _command_with_config("validate-data", config, tmp_path))

    assert result.exit_code == EXIT_SETUP_ERROR, result.output
    assert "Unknown severity_overrides key: 'binding_lable_mismatch'" in result.output


@pytest.mark.parametrize(
    "args,message",
    [
        (
            ["validate-data", "missing.yaml", "--schema", str(DATA / "dynamic_enum_schema.yaml")],
            "File not found: missing.yaml",
        ),
        (
            [
                "validate-data",
                str(DATA / "dynamic_enum_valid_data.yaml"),
                "--schema",
                str(DATA / "dynamic_enum_schema.yaml"),
                "--no-bindings",
                "--no-dynamic-enums",
            ],
            "No validation enabled",
        ),
        (
            [
                "validate-data",
                str(DATA / "dynamic_enum_valid_data.yaml"),
                "--schema",
                str(DATA / "dynamic_enum_schema.yaml"),
                "--cache-strategy",
                "eager",
            ],
            "--cache-strategy must be one of: progressive, greedy, got: 'eager'",
        ),
    ],
)
def test_cli_invalid_input_exits_3(runner, args, message):
    result = runner.invoke(app, args)

    assert result.exit_code == EXIT_SETUP_ERROR, result.output
    assert message in result.output
    assert "Traceback" not in result.output


def test_cli_text_file_group_out_of_range_exits_3(runner, tmp_path):
    result = runner.invoke(
        app, ["validate-text-file", str(_text_file(tmp_path)), "--curie-group", "5"]
    )

    assert result.exit_code == EXIT_SETUP_ERROR, result.output
    assert "--curie-group 5 is out of range (pattern has 2 group(s))" in result.output


def test_cli_unexpected_value_error_is_not_a_setup_error(runner, monkeypatch):
    """Only ConfigurationError is a setup error. Any other ValueError is a bug
    and must still surface as one, not be relabeled "fix your setup"."""

    def broken(self, schema_path):
        raise ValueError("bug in the validator")

    monkeypatch.setattr(EnumValidator, "validate_schema", broken)
    result = runner.invoke(
        app,
        ["validate-schema", str(DATA / "test_schema.yaml"), "--config", str(OAK_CONFIG)],
    )

    assert result.exit_code == 1
    assert type(result.exception) is ValueError
