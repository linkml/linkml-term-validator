"""Tests for ExpressionValidationPlugin: grammar-based code systems (UCUM, SMILES, InChI)."""

from importlib.util import find_spec
from pathlib import Path

import pytest
from linkml.validator import Validator  # type: ignore[import-untyped]
from linkml.validator.loaders import YamlLoader  # type: ignore[import-untyped]
from typer.testing import CliRunner

from linkml_term_validator.cli import app
from linkml_term_validator.plugins import ExpressionValidationPlugin
from linkml_term_validator.plugins.expressions import grammars
from linkml_term_validator.plugins.expressions.grammars import (
    Grammar,
    GrammarUnavailableError,
    load_checker,
)

DATA = Path(__file__).parent / "data"
SCHEMA = DATA / "expression_schema.yaml"
VALID = DATA / "expression_valid_data.yaml"
INVALID = DATA / "expression_invalid_data.yaml"

needs_ucum = pytest.mark.skipif(
    find_spec("ucumvert") is None,
    reason="ucumvert not installed (the 'ucum' extra needs Python >= 3.11)",
)
needs_rdkit = pytest.mark.skipif(
    find_spec("rdkit") is None,
    reason="rdkit not installed (the 'chem' extra)",
)


def _validate(data_path: Path, plugin=None):
    validator = Validator(
        schema=str(SCHEMA), validation_plugins=[plugin or ExpressionValidationPlugin()]
    )
    return validator.validate_source(YamlLoader(data_path), target_class="Sample").results


@pytest.fixture
def fresh_checkers():
    """Drop cached checkers so a test can swap in a different loader."""
    load_checker.cache_clear()
    grammars.available_grammars.cache_clear()
    yield
    load_checker.cache_clear()
    grammars.available_grammars.cache_clear()


# --- individual grammars -----------------------------------------------------


@needs_ucum
@pytest.mark.parametrize(
    "value,valid",
    [
        ("mg/dL", True),
        ("m.s-2", True),
        ("10*3/uL", True),
        ("{cells}/uL", True),
        ("[degF]", True),
        ("%", True),
        ("mg//dL", False),
        ("furlongs", False),
    ],
)
def test_ucum(value, valid):
    assert (load_checker("ucum")(value) is None) is valid


@needs_rdkit
@pytest.mark.parametrize(
    "value,expected",
    [
        ("CCO", None),
        ("c1ccccc1", None),
        ("C(C", "not valid SMILES syntax"),
        ("C1CC", "not valid SMILES syntax"),
        ("N(C)(C)(C)(C)C", "chemically invalid"),
        ("c1ccccc", "not valid SMILES syntax"),
    ],
)
def test_smiles(value, expected):
    reason = load_checker("smiles")(value)
    if expected is None:
        assert reason is None
    else:
        assert reason is not None and expected in reason


@needs_rdkit
@pytest.mark.parametrize(
    "value,valid",
    [
        ("InChI=1S/C2H6O/c1-2-3/h3H,2H2,1H3", True),
        ("InChI=1S/H2O/h1H2", True),
        ("InChI=1S/C2H6O/c1-2-9/h3H", False),
        ("InChI=garbage", False),
        ("1S/H2O/h1H2", False),
    ],
)
def test_inchi(value, valid):
    assert (load_checker("inchi")(value) is None) is valid


@pytest.mark.parametrize(
    "value,valid",
    [
        ("LFQSCWFLJHTTHZ-UHFFFAOYSA-N", True),
        ("LFQSCWFLJHTTHZ-UHFFFAOYNA-N", True),  # non-standard InChI flag
        ("LFQSCWFLJHTTHZ-UHFFFAOYSA", False),
        ("lfqscwfljhtthz-uhfffaoysa-n", False),
        ("LFQSCWFLJHTTHZ-UHFFFAOYXA-N", False),  # flag must be S or N
    ],
)
def test_inchikey_needs_no_extra(value, valid):
    assert (load_checker("inchikey")(value) is None) is valid


# --- plugin ------------------------------------------------------------------


@needs_ucum
@needs_rdkit
def test_valid_data_has_no_results():
    results = _validate(VALID)
    assert results == [], [r.message for r in results]


@needs_ucum
@needs_rdkit
def test_invalid_data_reports_each_bad_value():
    results = _validate(INVALID)
    assert all(r.type == "expression_invalid" for r in results)
    paths = sorted(next(c for c in r.context if c.startswith("path: ")) for r in results)
    assert paths == [
        "path: alt_smiles[1]",
        "path: inchi",
        "path: inchikey",
        "path: measurements[1].unit",
        "path: smiles",
        "path: unit",
    ]
    assert all(r.severity.name == "ERROR" for r in results)


@needs_ucum
@needs_rdkit
def test_severity_override():
    plugin = ExpressionValidationPlugin(severity_overrides={"expression_invalid": "WARN"})
    results = _validate(INVALID, plugin)
    assert results and all(r.severity.name == "WARN" for r in results)


def test_unknown_severity_override_key_raises():
    with pytest.raises(ValueError, match="Unknown severity_overrides key"):
        ExpressionValidationPlugin(severity_overrides={"expression_typo": "WARN"})


def test_missing_parser_is_reported_not_passed(monkeypatch, fresh_checkers):
    """A grammar whose parser cannot be imported fails the file, naming the extra."""

    def missing():
        raise ImportError("No module named 'rdkit'")

    monkeypatch.setitem(
        grammars._BUILTIN_GRAMMARS, "smiles", Grammar("smiles", "SMILES", missing, extra="chem")
    )
    monkeypatch.setitem(
        grammars._BUILTIN_GRAMMARS, "inchi", Grammar("inchi", "InChI", missing, extra="chem")
    )
    monkeypatch.setitem(
        grammars._BUILTIN_GRAMMARS, "ucum", Grammar("ucum", "UCUM", missing, extra="ucum")
    )
    results = _validate(VALID)
    unavailable = [r for r in results if r.type == "expression_grammar_unavailable"]
    # Reported once per grammar per source, not once per value.
    assert sorted(r.context[1] for r in unavailable) == [
        "grammar: inchi",
        "grammar: smiles",
        "grammar: ucum",
    ]
    assert all(r.severity.name == "ERROR" for r in unavailable)
    assert any("linkml-term-validator[chem]" in r.message for r in unavailable)
    # The dependency-free InChIKey grammar still ran (and passed).
    assert all(r.type == "expression_grammar_unavailable" for r in results)


def test_missing_parser_raises_with_hint(monkeypatch, fresh_checkers):
    def missing():
        raise ImportError("nope")

    monkeypatch.setitem(
        grammars._BUILTIN_GRAMMARS, "ucum", Grammar("ucum", "UCUM", missing, extra="ucum")
    )
    with pytest.raises(GrammarUnavailableError, match=r"linkml-term-validator\[ucum\]"):
        load_checker("ucum")


def test_unknown_grammar_is_reported(tmp_path):
    schema = tmp_path / "schema.yaml"
    schema.write_text(
        SCHEMA.read_text().replace("expression_grammar: inchikey", "expression_grammar: inchkey")
    )
    data = tmp_path / "data.yaml"
    data.write_text("id: ex:x\ninchikey: LFQSCWFLJHTTHZ-UHFFFAOYSA-N\n")
    validator = Validator(schema=str(schema), validation_plugins=[ExpressionValidationPlugin()])
    results = validator.validate_source(YamlLoader(data), target_class="Sample").results
    assert [r.type for r in results] == ["expression_grammar_unknown"]
    assert "inchkey" in results[0].message and "inchikey" in results[0].message


def test_empty_value_is_invalid(tmp_path):
    data = tmp_path / "data.yaml"
    data.write_text("id: ex:x\ninchikey: ''\n")
    results = _validate(data)
    assert [r.type for r in results] == ["expression_invalid"]
    assert "empty value" in results[0].message


def test_entry_point_grammar(monkeypatch, fresh_checkers):
    """A third-party package can register a grammar; it cannot shadow a built-in."""

    class FakeEntryPoint:
        def __init__(self, name, grammar):
            self.name = name
            self._grammar = grammar

        def load(self):
            return self._grammar

    upper = Grammar("upper", "Uppercase", lambda: lambda v: None if v.isupper() else "lowercase")
    shadow = Grammar("ucum", "Fake UCUM", lambda: lambda v: None)
    monkeypatch.setattr(
        grammars,
        "entry_points",
        lambda group: [FakeEntryPoint("upper", upper), FakeEntryPoint("ucum", shadow)],
    )
    found = grammars.available_grammars()
    assert found["upper"] is upper
    assert found["ucum"].title == "UCUM"
    assert load_checker("upper")("abc") == "lowercase"


# --- CLI ---------------------------------------------------------------------


@needs_ucum
@needs_rdkit
def test_cli_valid():
    result = CliRunner().invoke(
        app,
        ["validate-data", str(VALID), "-s", str(SCHEMA), "--no-bindings", "--no-dynamic-enums"],
    )
    assert result.exit_code == 0, result.output
    assert "Validation passed" in result.output


@needs_ucum
@needs_rdkit
def test_cli_invalid():
    result = CliRunner().invoke(
        app,
        ["validate-data", str(INVALID), "-s", str(SCHEMA), "--no-bindings", "--no-dynamic-enums"],
    )
    assert result.exit_code == 1
    assert "mg//dL" in result.output
    assert "measurements[1].unit" in result.output


def test_cli_no_expressions_skips_check():
    result = CliRunner().invoke(
        app,
        [
            "validate-data",
            str(INVALID),
            "-s",
            str(SCHEMA),
            "--no-bindings",
            "--no-dynamic-enums",
            "--no-expressions",
        ],
    )
    assert result.exit_code == 1
    assert "No validation enabled" in result.output
