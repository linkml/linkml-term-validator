"""Shared utilities for ontology access and caching."""

from linkml_term_validator.utils.oak_utils import (
    EmptyReachableClosureError,
    OntologyAccess,
    OntologyServiceUnavailableError,
    get_prefix,
    is_connectivity_error,
    normalize_string,
    obsolete_term_message,
    raise_if_service_unavailable,
)

__all__ = [
    "EmptyReachableClosureError",
    "OntologyAccess",
    "OntologyServiceUnavailableError",
    "get_prefix",
    "is_connectivity_error",
    "normalize_string",
    "obsolete_term_message",
    "raise_if_service_unavailable",
]
