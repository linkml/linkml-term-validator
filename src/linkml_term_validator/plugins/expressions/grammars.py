"""Registry of grammar-based code systems the expression plugin can check.

A grammar-based (compositional) code system defines its valid codes by a
syntax rather than a list: UCUM builds ``mg/dL`` from ``m`` + ``g`` + ``/`` +
``d`` + ``L``, and SMILES describes a molecule atom by atom. There is nothing
to look up, so these values are validated by parsing them.

Each grammar's parser is imported only when a schema first asks for it, so the
base install stays light. A parser that is not installed is reported, never
treated as a pass: :func:`load_checker` raises :class:`GrammarUnavailableError`
naming the extra to install.

Grammars from other packages register under the ``linkml_term_validator.grammars``
entry point group; each entry point resolves to a :class:`Grammar`.

Examples:
    >>> sorted(available_grammars())
    ['inchi', 'inchikey', 'smiles', 'ucum']
    >>> get_grammar("InChIKey").name
    'inchikey'
    >>> check = load_checker("inchikey")
    >>> check("LFQSCWFLJHTTHZ-UHFFFAOYSA-N") is None
    True
    >>> check("LFQSCWFLJHTTHZ-UHFFFAOYSA")
    'not a valid InChIKey (expected 14 letters, hyphen, 10 letters, hyphen, 1 letter)'
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from importlib.metadata import entry_points
from typing import Optional

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "linkml_term_validator.grammars"

# A checker answers None for a valid value, else a short reason it is invalid.
Checker = Callable[[str], Optional[str]]


class GrammarUnavailableError(RuntimeError):
    """A grammar's parser could not be imported, so its values went unchecked."""

    def __init__(self, grammar: "Grammar", original: Exception):
        """Record which grammar failed to load and why.

        Args:
            grammar: The grammar whose parser is missing
            original: The import error raised while loading it
        """
        hint = (
            f"pip install 'linkml-term-validator[{grammar.extra}]'"
            if grammar.extra
            else "install the package that provides it"
        )
        super().__init__(
            f"the {grammar.title} parser is not installed ({original}); to check these values, {hint}"
        )
        self.grammar = grammar
        self.original = original


@dataclass(frozen=True)
class Grammar:
    """A grammar-based code system and how to build a checker for it.

    Attributes:
        name: Lowercase key used in the ``expression_grammar`` annotation
        title: Human-readable name used in messages
        loader: Builds the checker; may raise ImportError if its parser is missing
        extra: The ``linkml-term-validator`` extra that installs the parser, if any
    """

    name: str
    title: str
    loader: Callable[[], Checker]
    extra: Optional[str] = None


# --- UCUM -------------------------------------------------------------------


def _load_ucum() -> Checker:
    """Build a UCUM checker on ucumvert's Lark grammar (case-sensitive UCUM)."""
    from lark.exceptions import LarkError  # type: ignore[import-not-found,unused-ignore]
    from ucumvert import get_ucum_parser  # type: ignore[import-not-found,import-untyped,unused-ignore]

    parser = get_ucum_parser()

    def check(value: str) -> Optional[str]:
        try:
            parser.parse(value)
        except LarkError as exc:
            # Lark's first line names the offending character and column.
            first_line = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
            return f"not a valid UCUM unit ({first_line})"
        return None

    return check


# --- Chemistry (RDKit) ------------------------------------------------------


def _quiet_rdkit() -> None:
    """Stop RDKit printing its own parse errors; the plugin reports them."""
    from rdkit import RDLogger  # type: ignore[import-not-found,import-untyped,unused-ignore]

    RDLogger.DisableLog("rdApp.*")  # type: ignore[attr-defined,unused-ignore]


def _load_smiles() -> Checker:
    """Build a SMILES checker: syntax first, then valence and aromaticity."""
    from rdkit import Chem  # type: ignore[import-not-found,import-untyped,unused-ignore]

    _quiet_rdkit()

    def check(value: str) -> Optional[str]:
        # Parsing without sanitizing separates a malformed string from a
        # well-formed one describing an impossible molecule.
        mol = Chem.MolFromSmiles(value, sanitize=False)
        if mol is None:
            return "not valid SMILES syntax"
        if mol.GetNumAtoms() == 0:
            return "SMILES describes no atoms"
        problems = Chem.DetectChemistryProblems(mol)
        if problems:
            return f"SMILES is chemically invalid ({problems[0].Message()})"
        return None

    return check


def _load_inchi() -> Checker:
    """Build an InChI checker on RDKit's bundled InChI library."""
    from rdkit.Chem import rdinchi  # type: ignore[import-not-found,import-untyped,unused-ignore]

    _quiet_rdkit()

    def check(value: str) -> Optional[str]:
        if not value.startswith("InChI="):
            return "not a valid InChI (must start with 'InChI=')"
        mol, retcode, message, log = rdinchi.InchiToMol(value, True, True)[:4]
        # retcode 0 is OK, 1 a warning; anything else means the InChI was rejected.
        if mol is None or retcode not in (0, 1):
            details = [line.strip() for line in f"{message}\n{log}".splitlines() if line.strip()]
            errors = [line for line in details if "error" in line.lower()]
            reason = f" ({errors[-1]})" if errors else ""
            return f"not a valid InChI{reason}"
        return None

    return check


# --- InChIKey (no dependency) -----------------------------------------------

# 14-letter connectivity hash, 8-letter stereo/isotope hash plus the
# standard/non-standard flag and the InChI version, then the protonation flag.
_INCHIKEY_RE = re.compile(r"[A-Z]{14}-[A-Z]{8}[SN][A-Z]-[A-Z]")


def _load_inchikey() -> Checker:
    """Build an InChIKey format checker; a key is a hash, so only its shape is checkable."""

    def check(value: str) -> Optional[str]:
        if _INCHIKEY_RE.fullmatch(value):
            return None
        return "not a valid InChIKey (expected 14 letters, hyphen, 10 letters, hyphen, 1 letter)"

    return check


_BUILTIN_GRAMMARS: dict[str, Grammar] = {
    g.name: g
    for g in (
        Grammar("ucum", "UCUM", _load_ucum, extra="ucum"),
        Grammar("smiles", "SMILES", _load_smiles, extra="chem"),
        Grammar("inchi", "InChI", _load_inchi, extra="chem"),
        Grammar("inchikey", "InChIKey", _load_inchikey),
    )
}


@cache
def available_grammars() -> dict[str, Grammar]:
    """Return every known grammar: the built-ins plus entry-point registrations.

    A built-in name cannot be replaced by an entry point; a plugin that tries
    is logged and ignored so the meaning of ``ucum`` never depends on what else
    happens to be installed.
    """
    grammars = dict(_BUILTIN_GRAMMARS)
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        try:
            grammar = ep.load()
        except Exception as exc:  # a broken third-party plugin must not break validation
            logger.warning("Could not load grammar entry point %r: %s", ep.name, exc)
            continue
        if not isinstance(grammar, Grammar):
            logger.warning("Grammar entry point %r is not a Grammar; ignored", ep.name)
            continue
        if grammar.name.lower() in _BUILTIN_GRAMMARS:
            logger.warning("Grammar entry point %r shadows a built-in grammar; ignored", ep.name)
            continue
        grammars[grammar.name.lower()] = grammar
    return grammars


def get_grammar(name: str) -> Optional[Grammar]:
    """Look up a grammar by name, ignoring case.

    Examples:
        >>> get_grammar("UCUM").title
        'UCUM'
        >>> get_grammar("not-a-grammar") is None
        True
    """
    return available_grammars().get(name.strip().lower())


@cache
def load_checker(name: str) -> Checker:
    """Build (once) the checker for a grammar.

    Raises:
        KeyError: If no grammar has that name
        GrammarUnavailableError: If the grammar's parser is not installed
    """
    grammar = get_grammar(name)
    if grammar is None:
        raise KeyError(name)
    try:
        return grammar.loader()
    except ImportError as exc:
        raise GrammarUnavailableError(grammar, exc) from exc
