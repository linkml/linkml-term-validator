# Gallery

Public projects use `linkml-term-validator` in several patterns: CI gates, curation workflows, schema-edit hooks, anti-hallucination checks, and ontology-label validation for mapping tables.

This page is based on public GitHub code search results reviewed on July 2, 2026. It is illustrative rather than exhaustive; private repositories, package-index mirrors, generated traces, and incidental mentions are excluded.

## Projects

| Project | How it uses linkml-term-validator | Evidence |
|---------|-----------------------------------|----------|
| [ai4curation/ai-gene-review](https://github.com/ai4curation/ai-gene-review) | Validates GO and other ontology term IDs/labels in mapping sets and gene-review YAML files. Several `just` recipes run `validate-data` with `--labels` and a project OAK config. | [`justfile`](https://github.com/ai4curation/ai-gene-review/blob/f854c1ea8a57f80588891db7ec5181a6ec95ab2b/justfile), [`src/ai_gene_review/cli.py`](https://github.com/ai4curation/ai-gene-review/blob/f854c1ea8a57f80588891db7ec5181a6ec95ab2b/src/ai_gene_review/cli.py) |
| [monarch-initiative/genesets](https://github.com/monarch-initiative/genesets) | Builds a multi-validator curation pipeline: LinkML structural validation, `linkml-term-validator validate-data`, reference validation, and a project-specific obsolescence sweep. | [`python/genesets-workflows/src/genesets_workflows/curation/validate.py`](https://github.com/monarch-initiative/genesets/blob/758b91d34e1703f4c7cfe414c0853e62679b9405/python/genesets-workflows/src/genesets_workflows/curation/validate.py) |
| [monarch-initiative/mic-ingest](https://github.com/monarch-initiative/mic-ingest) | Uses term validation as an anti-hallucination check for a micronutrient knowledge base. Recipes validate individual nutrient files and all nutrient files with labels and `oak_config.yaml`. | [`justfile`](https://github.com/monarch-initiative/mic-ingest/blob/cb3cc50e6cd99aacf7e2a60389033edbeb95f4ca/justfile) |
| [monarch-initiative/dismech](https://github.com/monarch-initiative/dismech) | Wraps the CLI for stricter CI behavior, treating warning output as fatal when validating data terms. | [`scripts/run_term_validator.sh`](https://github.com/monarch-initiative/dismech/blob/5c000d01104c71fbeae93733eb8cb079d0038abd/scripts/run_term_validator.sh) |
| [cmungall/nmdc-bermap](https://github.com/cmungall/nmdc-bermap) | Validates schema enum meanings, database terms, generated profile terms, and SSSOM profile mappings with project-specific OAK configuration. | [`project.justfile`](https://github.com/cmungall/nmdc-bermap/blob/11492b0f0f173f57ef79390eb121f6af96d0e439/project.justfile) |
| [bridge2ai/data-sheets-schema](https://github.com/bridge2ai/data-sheets-schema) | Runs schema term validation from an editor/agent hook after schema-file edits, reporting ontology term issues during authoring. | [`.claude/hooks/term_validator_hook.py`](https://github.com/bridge2ai/data-sheets-schema/blob/e141852f532e6a0cea47992ffe9afc0f20a7c6a5/.claude/hooks/term_validator_hook.py) |
| [Cellular-Semantics/evidencell](https://github.com/Cellular-Semantics/evidencell) | Declares `linkml-term-validator` as a dependency and includes smoke tests that pin expected CLI availability for project validation recipes. | [`pyproject.toml`](https://github.com/Cellular-Semantics/evidencell/blob/25c2b32ead2ee698e7903c63ec25fce83204f241/pyproject.toml), [`tests/test_tool_interfaces.py`](https://github.com/Cellular-Semantics/evidencell/blob/25c2b32ead2ee698e7903c63ec25fce83204f241/tests/test_tool_interfaces.py) |

## Usage Patterns

These projects show several recurring patterns:

- **Schema authoring checks**: run `validate-schema` after editing LinkML schemas.
- **Data curation gates**: run `validate-data` against curated YAML files before merge or release.
- **Label-pair anti-hallucination**: require both ontology ID and label to match.
- **Project OAK configs**: keep ontology adapter choices in version-controlled `oak_config.yaml`.
- **Strict CI wrappers**: treat warnings or unexpected validator output as release-blocking.

If your public project uses `linkml-term-validator`, open an issue or pull request with a stable repository link and a short description of the validation workflow.
