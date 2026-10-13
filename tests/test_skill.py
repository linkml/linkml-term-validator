"""Keep the published agent skill honest against the CLI it documents.

An agent runs the skill's commands verbatim, so a flag that does not exist
fails for every downstream user (and exits 2, the code the skill says can
mean a service outage).
"""

import re
import shlex
from pathlib import Path

import click
import pytest
import typer
from ruamel.yaml import YAML

from linkml_term_validator.cli import app

SKILLS_DIR = Path(__file__).parent.parent / "skills"
SKILL_DIRS = sorted(p.parent for p in SKILLS_DIR.glob("*/SKILL.md"))
SKILL_DOCS = sorted(SKILLS_DIR.glob("**/*.md"))

_GROUP = typer.main.get_command(app)
assert isinstance(_GROUP, click.Group)
COMMANDS = _GROUP.commands


def _options(command: click.Command) -> set[str]:
    """All option spellings a command accepts, including --no- forms and --help."""
    names = {"--help"}
    for param in command.params:
        names.update(param.opts)
        names.update(param.secondary_opts)
    return names


ALL_OPTIONS = set().union(*(_options(c) for c in COMMANDS.values()))


def _frontmatter(skill_md: Path) -> dict:
    match = re.match(r"^---\n(.*?)\n---\n", skill_md.read_text(), re.DOTALL)
    assert match, f"{skill_md} has no YAML frontmatter"
    return YAML(typ="safe").load(match.group(1))


def _prose(doc: Path) -> str:
    """Document text with fenced code blocks removed."""
    return re.sub(r"```.*?```", "", doc.read_text(), flags=re.DOTALL)


def _ltv_invocations(doc: Path) -> list[list[str]]:
    """Tokenized ``linkml-term-validator`` command lines from fenced bash blocks."""
    invocations = []
    for block in re.findall(r"```bash\n(.*?)```", doc.read_text(), re.DOTALL):
        for line in block.replace("\\\n", " ").splitlines():
            tokens = shlex.split(line, comments=True)
            # Match pinned forms too, e.g. ``uvx linkml-term-validator@1.2.3``.
            programs = [t.split("@", 1)[0] for t in tokens]
            if "linkml-term-validator" in programs:
                invocations.append(tokens[programs.index("linkml-term-validator") + 1 :])
    return invocations


def test_skills_exist():
    assert SKILL_DIRS, f"no skills found under {SKILLS_DIR}"


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda p: p.name)
def test_skill_frontmatter(skill_dir):
    meta = _frontmatter(skill_dir / "SKILL.md")
    assert meta["name"] == skill_dir.name
    assert 0 < len(meta["description"]) <= 1024


@pytest.mark.parametrize(
    "doc", SKILL_DOCS, ids=lambda p: str(p.relative_to(SKILLS_DIR))
)
def test_bash_block_flags_exist_on_their_command(doc):
    for tokens in _ltv_invocations(doc):
        subcommand = tokens[0]
        assert subcommand in COMMANDS, f"{doc.name}: unknown subcommand {subcommand!r}"
        accepted = _options(COMMANDS[subcommand])
        for token in tokens[1:]:
            if token.startswith("-"):
                flag = token.split("=", 1)[0]
                assert flag in accepted, f"{doc.name}: {subcommand} has no {flag}"


@pytest.mark.parametrize(
    "doc", SKILL_DOCS, ids=lambda p: str(p.relative_to(SKILLS_DIR))
)
def test_cited_flags_exist(doc):
    """Every `--flag` quoted in prose exists where the prose puts it.

    A code span that opens with a subcommand (`validate-schema --strict`) is
    checked against that subcommand. A bare flag (`--fail-on any`) names no
    command, so it only has to exist on at least one.
    """
    problems = []
    for span in re.findall(r"`([^`]*)`", _prose(doc)):
        words = span.split()
        command = COMMANDS.get(words[0]) if words else None
        accepted = _options(command) if command else ALL_OPTIONS
        for flag in re.findall(r"--[a-z][a-z0-9-]*", span):
            if flag not in accepted:
                problems.append(f"`{span}`: {flag}")
    assert not problems, f"{doc.name} cites flags that do not exist there: {problems}"


@pytest.mark.parametrize(
    "doc", SKILL_DOCS, ids=lambda p: str(p.relative_to(SKILLS_DIR))
)
def test_cited_subcommands_exist(doc):
    cited = set(re.findall(r"`((?:validate|migrate)[a-z-]*)`", _prose(doc)))
    assert cited <= COMMANDS.keys(), (
        f"{doc.name} cites unknown subcommands: {sorted(cited - COMMANDS.keys())}"
    )
