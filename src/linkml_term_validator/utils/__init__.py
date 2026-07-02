"""Shared utilities for ontology access and caching."""

from linkml_term_validator.utils.oak_utils import (
    OntologyAccess,
    get_prefix,
    normalize_string,
    obsolete_term_message,
)

__all__ = ["OntologyAccess", "get_prefix", "normalize_string", "obsolete_term_message"]
