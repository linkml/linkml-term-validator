"""Shared utilities for ontology access and caching."""

from linkml_term_validator.utils.oak_utils import (
    DEFAULT_NOT4CURATION_MARKERS,
    OntologyAccess,
    OntologyServiceUnavailableError,
    get_prefix,
    is_connectivity_error,
    normalize_marker_text,
    normalize_not4curation_markers,
    normalize_string,
    not4curation_message,
    obsolete_term_message,
    raise_if_service_unavailable,
)

__all__ = [
    "DEFAULT_NOT4CURATION_MARKERS",
    "OntologyAccess",
    "OntologyServiceUnavailableError",
    "get_prefix",
    "is_connectivity_error",
    "normalize_marker_text",
    "normalize_not4curation_markers",
    "normalize_string",
    "not4curation_message",
    "obsolete_term_message",
    "raise_if_service_unavailable",
]
