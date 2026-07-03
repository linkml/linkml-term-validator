"""Shared utilities for ontology access and caching."""

from linkml_term_validator.utils.oak_utils import (
    OntologyAccess,
    OntologyServiceUnavailableError,
    get_prefix,
    is_connectivity_error,
    normalize_string,
    obsolete_term_message,
)

__all__ = [
    "OntologyAccess",
    "OntologyServiceUnavailableError",
    "get_prefix",
    "is_connectivity_error",
    "normalize_string",
    "obsolete_term_message",
]
