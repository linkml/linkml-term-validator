"""OAK adapter management and label caching.

This module centralizes the ontology-access infrastructure shared by the
standalone :class:`~linkml_term_validator.validator.EnumValidator` and the
:class:`~linkml_term_validator.plugins.base.BaseOntologyPlugin`. Both hold an
:class:`OntologyAccess` instance rather than reimplementing prefix parsing,
adapter resolution, and label caching.

Example:
    >>> from linkml_term_validator.utils import OntologyAccess, get_prefix, normalize_string
    >>> get_prefix("GO:0008150")
    'GO'
    >>> get_prefix("invalid-no-colon")
    >>> normalize_string("Hello, World!")
    'hello world'
"""

import csv
import logging
import re
import socket
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from oaklib import get_adapter
from ruamel.yaml import YAML

from linkml_term_validator.cache_utils import atomic_write_csv, locked_cache_file

logger = logging.getLogger(__name__)


class OntologyServiceUnavailableError(Exception):
    """Raised when an ontology service could not be reached to resolve a term.

    This is deliberately distinct from a term simply not existing. A remote
    service (e.g. OLS/EBI) that answers with HTTP 404 is a definitive "this term
    does not exist" and is reported as a normal validation error. A DNS failure,
    connection refused, or timeout, by contrast, means we *could not determine*
    anything about the term. Treating that as "not found" mislabels every term as
    invalid data during an outage; instead this exception is raised so validation
    fails fast with an "unable to validate at this time" status.
    """

    def __init__(self, curie: str, original: Optional[BaseException] = None):
        self.curie = curie
        self.original = original
        detail = f": {original}" if original is not None else ""
        super().__init__(f"could not reach ontology service to resolve {curie}{detail}")


class UnreliableReachabilityError(Exception):
    """Base for "a reachable_from verdict could not be trusted" failures.

    Distinct from invalid data: the term's membership could not be reliably
    computed because the configured ontology adapter returned a broken or
    self-contradictory graph. Callers surface this as "unable to validate"
    (a configuration/ontology-graph problem) rather than "term not in enum".
    """


class EmptyReachableClosureError(UnreliableReachabilityError):
    """Raised when a ``reachable_from`` source node resolves but reaches nothing.

    A ``reachable_from`` source node is meant to root a subtree — its descendants
    (or, for ``traverse_up``, its ancestors). When at least one source node
    resolves to a real ontology term yet the whole query expands to an *empty*
    set, two silent failures follow: every candidate term is rejected as "not in
    enum" (a wrong ``False``, not an error), and any greedy expansion is cached as
    an empty-but-``complete`` closure that poisons later runs. That is never a
    useful validation configuration, so this exception is raised to fail loud.
    """

    def __init__(self, source_node: str, traverse_up: bool = False):
        self.source_node = source_node
        self.traverse_up = traverse_up
        direction = "ancestor" if traverse_up else "descendant"
        prefix = get_prefix(source_node) or source_node
        super().__init__(
            f"reachable_from source node {source_node!r} resolves to a valid term but "
            f"the query expands to an empty set (its {direction} closure is empty under "
            f"the configured adapter), so every {prefix} term would be silently rejected. "
            f"This usually means the source node or adapter is misconfigured. Verify the "
            f"source node, or configure a local adapter such as 'sqlite:obo:{prefix.lower()}'."
        )


class InconsistentReachabilityError(UnreliableReachabilityError):
    """Raised when an adapter's ancestor and descendant directions disagree.

    A correct ontology is round-trip consistent: if ``D`` is a descendant of
    ``S`` then ``S`` is among ``D``'s ancestors. When a ``reachable_from`` source
    node ``S`` resolves and *has* descendants, yet those descendants do **not**
    report ``S`` among their ancestors, the adapter's hierarchy is internally
    inconsistent. Ancestor-based membership checks (the progressive per-value
    path) then silently return wrong negatives while the descendant direction
    looks fine — a failure no "term not found" or "empty closure" check catches.

    The motivating case (dismech#7012): OLS4 drops ``MONDO:0000001`` from every
    MONDO *ancestor* closure — the ``human disease`` axiom is rewired to a
    cross-ontology ``AFO_O:0000001`` term — while ``descendants(MONDO:0000001)``
    still returns the whole disease tree. So ``ols:mondo`` silently rejects every
    MONDO term validated by ancestor reachability. Configuring a local,
    deterministic adapter (e.g. ``sqlite:obo:mondo``) restores a consistent graph.
    """

    def __init__(self, source_node: str, descendant: str, traverse_up: bool = False):
        self.source_node = source_node
        self.descendant = descendant
        self.traverse_up = traverse_up
        prefix = get_prefix(source_node) or source_node
        if traverse_up:
            detail = (
                f"ancestor {descendant!r} of source node {source_node!r} does not report "
                f"it among its descendants"
            )
        else:
            detail = (
                f"descendant {descendant!r} of source node {source_node!r} does not report "
                f"it among its ancestors"
            )
        super().__init__(
            f"reachable_from graph is internally inconsistent: {detail}. The adapter's "
            f"ancestor and descendant directions disagree, so ancestor-based membership "
            f"checks silently return wrong negatives for {prefix} terms (the OLS4 MONDO "
            f"defect — MONDO:0000001 is dropped from MONDO ancestor closures; see "
            f"dismech#7012). Configure a local, consistent adapter such as "
            f"'sqlite:obo:{prefix.lower()}' for the {prefix} prefix."
        )


# Well-defined exception TYPES that mean "the ontology service could not be
# reached", matched by isinstance (never by class name). OAK's OLS adapter goes
# label() -> client.get_term() -> requests.get()/raise_for_status(), so a network
# outage surfaces as a requests connection/timeout exception; a missing term
# instead raises requests.HTTPError carrying a .response (handled separately as a
# normal "not found"). The builtin/socket types cover adapters that talk to the
# OS network layer directly. requests/urllib3 are optional imports so this module
# stays usable even if a future adapter drops them.
_CONNECTIVITY_EXC_TYPES: list[type[BaseException]] = [
    ConnectionError,
    TimeoutError,
    socket.gaierror,
]
try:  # pragma: no cover - requests is present via oaklib's OLS client
    import requests.exceptions as _requests_exc

    _CONNECTIVITY_EXC_TYPES += [_requests_exc.ConnectionError, _requests_exc.Timeout]
except Exception:  # pragma: no cover
    pass
try:  # pragma: no cover - urllib3 is present via requests
    import urllib3.exceptions as _urllib3_exc

    _CONNECTIVITY_EXC_TYPES += [
        _urllib3_exc.NewConnectionError,
        _urllib3_exc.MaxRetryError,
        _urllib3_exc.TimeoutError,
    ]
except Exception:  # pragma: no cover
    pass
_CONNECTIVITY_EXC_TUPLE = tuple(_CONNECTIVITY_EXC_TYPES)


def is_connectivity_error(exc: BaseException) -> bool:
    """Return True if an exception (or its cause chain) is a network outage.

    Classifies by ``isinstance`` against well-defined connection/timeout types
    (``requests.exceptions.ConnectionError``/``Timeout``, urllib3 equivalents,
    and the builtin/socket types) rather than by class name. The chain is walked
    so a ``requests.exceptions.ConnectionError`` wrapping a urllib3
    ``NameResolutionError`` (the shape an OLS/EBI outage produces) is recognized
    even if only the inner cause is a recognized type. An implicit context that
    was explicitly suppressed (``raise ... from None``) is not followed, so an
    unrelated in-flight connection error cannot cause a false positive.
    """
    seen: set[int] = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, _CONNECTIVITY_EXC_TUPLE):
            return True
        # An explicit cause wins; otherwise follow the implicit context only when
        # it was not suppressed via ``raise ... from None``.
        next_exc = current.__cause__
        if next_exc is None and not current.__suppress_context__:
            next_exc = current.__context__
        current = next_exc
    return False


def raise_if_service_unavailable(curie: str, exc: BaseException) -> None:
    """Re-raise a lookup failure as OntologyServiceUnavailableError if it is a
    service problem rather than a definitive answer about the term.

    A connection-level failure (DNS/connect/timeout) or a transient HTTP status
    means the ontology service is unreachable or erroring, so the term's status
    is unknown and validation should fail fast. Transient statuses are the 5xx
    range plus 408 (Request Timeout) and 429 (Too Many Requests) - a rate-limited
    or timed-out lookup is "try again", not a statement that the term is absent.
    A definitive 4xx (notably 404) is left for the caller to treat as "term not
    found".
    """
    if is_connectivity_error(exc):
        raise OntologyServiceUnavailableError(curie, exc) from exc
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int) and (status >= 500 or status in (408, 429)):
        raise OntologyServiceUnavailableError(curie, exc) from exc


def obsolete_term_message(curie: str) -> str:
    """Return the canonical message for an obsolete ontology term.

    Shared across the plugins and the standalone validator so every site emits
    the same phrasing.

    Examples:
        >>> obsolete_term_message("GO:0000005")
        'Ontology term GO:0000005 is obsolete'
    """
    return f"Ontology term {curie} is obsolete"


def get_prefix(curie: str) -> Optional[str]:
    """Extract the prefix from a CURIE.

    Args:
        curie: A CURIE like "GO:0008150"

    Returns:
        The prefix (e.g., "GO") or None if there is no prefix separator

    Examples:
        >>> get_prefix("GO:0008150")
        'GO'
        >>> get_prefix("CHEBI:12345")
        'CHEBI'
        >>> get_prefix("invalid")
    """
    if ":" not in curie:
        return None
    return curie.split(":", 1)[0]


def normalize_string(s: str) -> str:
    """Normalize a string for comparison.

    Removes punctuation and collapses whitespace, lowercasing the result.

    Args:
        s: String to normalize

    Returns:
        Normalized string

    Examples:
        >>> normalize_string("Hello, World!")
        'hello world'
        >>> normalize_string("T-Cell Receptor")
        't cell receptor'
        >>> normalize_string("Multi  Spaces")
        'multi spaces'
    """
    normalized = re.sub(r"[^\w\s]", " ", s.lower())
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


class OntologyAccess:
    """Resolve ontology labels through OAK adapters with multi-level caching.

    Owns the infrastructure that was previously duplicated between the
    standalone validator and the plugin base class:

    - Per-prefix OAK adapter management (with optional ``oak_config.yaml``)
    - Multi-level label caching (in-memory + file-based CSV)
    - Unknown-prefix tracking

    Examples:
        >>> access = OntologyAccess(cache_labels=False)
        >>> access.get_unknown_prefixes()
        set()
        >>> access.is_prefix_configured("GO")
        False
    """

    def __init__(
        self,
        oak_adapter_string: str = "sqlite:obo:",
        cache_labels: bool = True,
        cache_dir: Path | str = Path("cache"),
        oak_config_path: Optional[Path | str] = None,
        offline: bool = False,
    ):
        """Initialize ontology access.

        Args:
            oak_adapter_string: Default OAK adapter string (e.g., "sqlite:obo:")
            cache_labels: Whether to cache ontology labels to disk
            cache_dir: Directory for label cache files
            oak_config_path: Path to oak_config.yaml for per-prefix adapters
            offline: If True, never build OAK adapters (guaranteeing no external
                access); resolve everything exclusively from the file cache.
        """
        self.oak_adapter_string = oak_adapter_string
        self.cache_labels = cache_labels
        self.offline = offline
        self.cache_dir = Path(cache_dir) if isinstance(cache_dir, str) else cache_dir
        self.oak_config_path = (
            Path(oak_config_path) if isinstance(oak_config_path, str) else oak_config_path
        )

        # In-memory caches and state
        self._label_cache: dict[str, Optional[str]] = {}
        self._adapter_cache: dict[str, object | None] = {}
        self._unknown_prefixes: set[str] = set()
        # Per-prefix obsolete-entity sets (for adapters where a whole-ontology
        # ``obsoletes()`` scan is cheap); None means "could not determine".
        self._obsolete_cache: dict[str, Optional[set[str]]] = {}
        # Per-CURIE OLS term payloads, so a term's label and obsolescence are
        # resolved with a single network fetch. None caches "not resolvable".
        self._ols_term_cache: dict[str, Optional[dict]] = {}

        # ontology_adapters mapping plus the full parsed config (so callers can
        # read additional keys without re-reading the file).
        self.oak_config: dict[str, str] = {}
        self.loaded_config: dict[str, Any] = {}
        if self.oak_config_path and self.oak_config_path.exists():
            self._load_oak_config()

    def _load_oak_config(self) -> None:
        """Load the ``ontology_adapters`` mapping from oak_config.yaml."""
        if self.oak_config_path is None:
            return
        yaml = YAML(typ="safe")
        with open(self.oak_config_path) as f:
            config = yaml.load(f)
        self.loaded_config = config or {}
        if "ontology_adapters" in self.loaded_config:
            self.oak_config = self.loaded_config["ontology_adapters"]

    # =========================================================================
    # Prefix helpers
    # =========================================================================

    def is_prefix_configured(self, prefix: str) -> bool:
        """Check if a prefix has a non-empty adapter configured in oak_config.

        Args:
            prefix: Ontology prefix (e.g., "GO")

        Returns:
            True if the prefix maps to a non-empty adapter string
        """
        return prefix in self.oak_config and bool(self.oak_config[prefix])

    # =========================================================================
    # Label cache (file-based)
    # =========================================================================

    def get_cache_file(self, prefix: str) -> Path:
        """Get the cache file path for a prefix, creating the directory."""
        prefix_dir = self.cache_dir / prefix.lower()
        prefix_dir.mkdir(parents=True, exist_ok=True)
        return prefix_dir / "terms.csv"

    def load_cache(self, prefix: str) -> dict[str, str]:
        """Load cached labels for a prefix as a CURIE -> label dict."""
        cache_file = self.get_cache_file(prefix)
        if not cache_file.exists():
            return {}

        cached = {}
        with open(cache_file) as f:
            reader = csv.DictReader(f)
            for row in reader:
                cached[row["curie"]] = row["label"]
        return cached

    def load_cache_with_timestamps(self, prefix: str) -> dict[str, dict[str, str]]:
        """Load cached labels with timestamps for a prefix.

        Returns:
            Dict mapping CURIEs to {"label": ..., "retrieved_at": ...}
        """
        cache_file = self.get_cache_file(prefix)
        if not cache_file.exists():
            return {}

        cached: dict[str, dict[str, str]] = {}
        with open(cache_file) as f:
            reader = csv.DictReader(f)
            for row in reader:
                cached[row["curie"]] = {
                    "label": row["label"],
                    "retrieved_at": row.get("retrieved_at", ""),
                }
        return cached

    def save_to_cache(self, prefix: str, curie: str, label: str) -> None:
        """Save a label to the cache.

        Preserves existing timestamps for unchanged entries; only new or changed
        entries get a fresh timestamp. Entries are sorted by CURIE for
        deterministic output. The write is locked and atomic so parallel
        validators do not lose entries or leave truncated files.

        Args:
            prefix: Ontology prefix
            curie: Full CURIE
            label: Label to cache
        """
        if not self.cache_labels:
            return

        cache_file = self.get_cache_file(prefix)

        with locked_cache_file(cache_file):
            existing = self.load_cache_with_timestamps(prefix)

            now = datetime.now().isoformat()
            if curie not in existing or existing[curie]["label"] != label:
                existing[curie] = {"label": label, "retrieved_at": now}

            atomic_write_csv(
                cache_file,
                ["curie", "label", "retrieved_at"],
                (
                    {
                        "curie": cached_curie,
                        "label": existing[cached_curie]["label"],
                        "retrieved_at": existing[cached_curie]["retrieved_at"],
                    }
                    for cached_curie in sorted(existing)
                ),
            )

    # =========================================================================
    # Adapters and label resolution
    # =========================================================================

    def get_adapter(self, prefix: str) -> object | None:
        """Get an OAK adapter for a prefix.

        In offline mode no adapter is ever built, guaranteeing no external
        access: resolution falls back entirely to the file cache.

        Resolution order:
        - If oak_config is loaded, use its mapping (None for an empty/missing
          entry; never fall back to the default adapter).
        - Otherwise, for the default ``sqlite:obo:`` adapter string, build a
          per-prefix ``sqlite:obo:<prefix>`` adapter.
        - Otherwise, use the configured default adapter string directly.

        Args:
            prefix: Ontology prefix

        Returns:
            OAK adapter or None if unavailable
        """
        # Offline mode: never construct an adapter. Building one can download an
        # ontology database or hit a remote service, so returning None here is
        # the single chokepoint that guarantees no external access.
        if self.offline:
            return None

        if prefix in self._adapter_cache:
            return self._adapter_cache[prefix]

        adapter_string = None

        if prefix in self.oak_config:
            configured = self.oak_config[prefix]
            if not configured:
                self._adapter_cache[prefix] = None
                return None
            adapter_string = self._adapter_string_for_prefix(prefix, configured)
        elif self.oak_config:
            # oak_config is loaded but prefix not in it - don't fall back
            self._adapter_cache[prefix] = None
            return None
        else:
            adapter_string = self._adapter_string_for_prefix(prefix, self.oak_adapter_string)

        if adapter_string:
            adapter = get_adapter(adapter_string)
            self._configure_adapter_for_prefix(adapter, prefix)
            self._adapter_cache[prefix] = adapter
            return adapter

        self._adapter_cache[prefix] = None
        return None

    @staticmethod
    def _adapter_string_for_prefix(prefix: str, adapter_string: str) -> str | None:
        """Resolve adapter shorthands that need a per-prefix ontology slug."""
        adapter_string = adapter_string.strip()
        if not adapter_string:
            return None

        if adapter_string == "sqlite:obo:":
            return f"sqlite:obo:{prefix.lower()}"

        # OAK's OLS adapter needs a focus ontology. Accept ``ols:`` as a
        # convenient per-prefix shorthand in config files and CLI defaults.
        if adapter_string in {"ols", "ols:"}:
            return f"ols:{prefix.lower()}"

        return adapter_string

    @staticmethod
    def _configure_adapter_for_prefix(adapter: object, prefix: str) -> None:
        """Patch adapter focus metadata when OAK leaves it unset."""
        if not hasattr(adapter, "focus_ontology"):
            return
        if getattr(adapter, "focus_ontology", None):
            return

        resource = getattr(adapter, "resource", None)
        scheme = getattr(resource, "scheme", None)
        slug = getattr(resource, "slug", None)
        if scheme == "ols":
            setattr(adapter, "focus_ontology", slug or prefix.lower())

    def get_label(self, curie: str) -> Optional[str]:
        """Get the label for an ontology term.

        Uses multi-level caching: in-memory, then file, then adapter.

        Args:
            curie: A CURIE like "GO:0008150"

        Returns:
            The label or None if not found
        """
        if curie in self._label_cache:
            return self._label_cache[curie]

        prefix = get_prefix(curie)
        if not prefix:
            return None

        # Offline mode always consults the file cache, even when label caching
        # (writing) is disabled, since the cache is the only permitted source.
        if self.cache_labels or self.offline:
            cached = self.load_cache(prefix)
            if curie in cached:
                cached_label = cached[curie]
                self._label_cache[curie] = cached_label
                return cached_label

        adapter = self.get_adapter(prefix)
        if adapter is None:
            if not self.is_prefix_configured(prefix):
                self._unknown_prefixes.add(prefix)
            self._label_cache[curie] = None
            return None

        # Remote adapters (e.g. OLS) raise for a non-existent term instead of
        # returning None: OLS answers a missing IRI with HTTP 404. A 404 is a
        # definitive "missing term" and is treated as "no label" so a single
        # fake/unknown CURIE surfaces as a clean validation result. A service
        # problem (connection/timeout failure, or an HTTP 5xx) is NOT a missing
        # term - we could not determine anything - so it is re-raised as
        # OntologyServiceUnavailableError to fail fast rather than mislabeling
        # every term as invalid data. Anything else is logged loudly and treated
        # as not found (preserving prior behavior for unexpected adapter errors).
        try:
            label = self._get_adapter_label(adapter, curie)
        except OntologyServiceUnavailableError:
            raise
        except Exception as e:  # noqa: BLE001 - adapters raise varied errors
            raise_if_service_unavailable(curie, e)
            self._log_lookup_failure(curie, e)
            label = None
        self._label_cache[curie] = label

        if label and self.cache_labels:
            self.save_to_cache(prefix, curie, label)

        return label

    def _get_adapter_label(self, adapter: object, curie: str) -> Optional[str]:
        """Get a label from an adapter, including OLS4 response compatibility."""
        label = adapter.label(curie)  # type: ignore[attr-defined]
        if label is not None:
            return label

        return self._get_ols4_embedded_label(adapter, curie)

    def _get_ols4_embedded_label(self, adapter: object, curie: str) -> Optional[str]:
        """Extract labels from OLS4 payloads returned by older OAK adapters."""
        term = self._ols_term_dict(adapter, curie)
        if term is None:
            return None
        label = term.get("label")
        return label if isinstance(label, str) else None

    @staticmethod
    def _log_lookup_failure(curie: str, exc: Exception) -> None:
        """Log an adapter lookup failure, distinguishing 404 from transient errors."""
        response = getattr(exc, "response", None)
        if getattr(response, "status_code", None) == 404:
            logger.debug("Term %s not found (HTTP 404): %s", curie, exc)
        else:
            logger.warning(
                "Label lookup for %s failed unexpectedly (treated as not found): %s",
                curie,
                exc,
            )

    def get_unknown_prefixes(self) -> set[str]:
        """Get the set of prefixes encountered but not configured."""
        return self._unknown_prefixes

    # =========================================================================
    # Obsolescence
    # =========================================================================

    def is_obsolete(self, curie: str) -> Optional[bool]:
        """Return whether an ontology term is obsolete.

        Obsolete (deprecated) terms still exist in an ontology and still resolve
        to a label, so they are not caught by a plain "does this term exist"
        check. This surfaces them explicitly.

        Args:
            curie: A CURIE like "GO:0000005"

        Returns:
            ``True``/``False`` when obsolescence can be determined, or ``None``
            when it cannot (no adapter, offline, or an adapter that does not
            expose obsolescence information). Note the two adapter strategies
            answer a *non-existent* term differently: the local ``obsoletes()``
            path reports ``False`` (it is simply absent from the obsolete set),
            while the OLS path reports ``None`` (the term does not resolve).
            Both are falsy, so callers treat "obsolete" as strictly ``True``.
        """
        prefix = get_prefix(curie)
        if not prefix:
            return None

        adapter = self.get_adapter(prefix)
        if adapter is None:
            return None

        # OLS: read the per-term ``is_obsolete`` flag. A whole-ontology
        # ``obsoletes()`` scan would page through every deprecated term in the
        # ontology, so it is not usable per-value against a remote service.
        if self._is_ols_adapter(adapter):
            return self._ols_is_obsolete(adapter, curie)

        # Local/SQLite adapters: a one-time ``obsoletes()`` scan is cheap and is
        # cached per prefix.
        obsoletes = self._get_obsoletes_set(prefix, adapter)
        if obsoletes is None:
            return None
        return curie in obsoletes

    def _get_obsoletes_set(self, prefix: str, adapter: object) -> Optional[set[str]]:
        """Return (and cache) the set of obsolete CURIEs for a prefix's adapter."""
        if prefix in self._obsolete_cache:
            return self._obsolete_cache[prefix]

        result: Optional[set[str]] = None
        obsoletes = getattr(adapter, "obsoletes", None)
        if callable(obsoletes):
            try:
                result = set(obsoletes())
            except Exception as e:  # noqa: BLE001 - adapters raise varied errors
                raise_if_service_unavailable(f"{prefix}:*", e)
                logger.debug("obsoletes() failed for prefix %s: %s", prefix, e)
                result = None
        self._obsolete_cache[prefix] = result
        return result

    @staticmethod
    def _is_ols_adapter(adapter: object) -> bool:
        """Detect OLS adapters, whose per-term metadata carries obsolescence."""
        resource = getattr(adapter, "resource", None)
        if getattr(resource, "scheme", None) == "ols":
            return True
        # Fall back to the OLS4 client shape only when the scheme is unavailable.
        return bool(
            getattr(adapter, "client", None)
            and getattr(adapter, "focus_ontology", None)
            and getattr(adapter, "curie_to_uri", None)
        )

    def _ols_is_obsolete(self, adapter: object, curie: str) -> Optional[bool]:
        """Read the OLS ``is_obsolete`` flag for a term (None if undeterminable)."""
        term = self._ols_term_dict(adapter, curie)
        if term is None:
            return None
        value = term.get("is_obsolete")
        return value if isinstance(value, bool) else None

    def _ols_term_dict(self, adapter: object, curie: str) -> Optional[dict]:
        """Return an OLS term payload for a CURIE, fetching it at most once.

        The payload carries both the label and the ``is_obsolete`` flag, so
        caching it per CURIE lets the label lookup and obsolescence check share a
        single network round-trip.
        """
        if curie in self._ols_term_cache:
            return self._ols_term_cache[curie]
        term = self._fetch_ols_term_dict(adapter, curie)
        self._ols_term_cache[curie] = term
        return term

    @staticmethod
    def _fetch_ols_term_dict(adapter: object, curie: str) -> Optional[dict]:
        """Fetch an OLS term payload, unwrapping the ``_embedded.terms`` envelope."""
        client = getattr(adapter, "client", None)
        focus_ontology = getattr(adapter, "focus_ontology", None)
        curie_to_uri = getattr(adapter, "curie_to_uri", None)
        if not client or not focus_ontology or not curie_to_uri:
            return None

        # curie_to_uri and get_term are both inside the guard: a malformed CURIE
        # can make curie_to_uri raise, and a missing term answers with HTTP 404.
        # Either way the term is simply "not resolvable", not a fatal error. A
        # network outage, however, means the term's status is unknown, so it is
        # re-raised to fail fast rather than masquerading as "not resolvable".
        try:
            iri = curie_to_uri(curie)
            if not iri:
                return None
            term = client.get_term(ontology=focus_ontology, iri=iri)
        except Exception as e:  # noqa: BLE001 - adapters raise varied errors
            raise_if_service_unavailable(curie, e)
            return None
        if not isinstance(term, dict):
            return None

        if "_embedded" in term:
            embedded = term.get("_embedded")
            terms = embedded.get("terms") if isinstance(embedded, dict) else None
            if isinstance(terms, list) and terms and isinstance(terms[0], dict):
                return terms[0]
            return None
        return term
