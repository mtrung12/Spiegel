"""
Source adapter contract.

Every adapter turns one external service into a list of CorpusItem. Adapters
own their pagination and auth; they do not filter, score or deduplicate.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from ....config import Config
from ....utils.logger import get_logger
from ..http import PoliteClient
from ..models import CorpusItem, FetchResult, SourceQuery, pseudonymize_author

logger = get_logger('spiegel.corpus.source')


class SourceAdapter(ABC):
    """Base class for all corpus sources."""

    #: Registry key and the value stored on ``CorpusItem.source``
    name: str = 'base'

    #: Human-readable note shown in the UI and in availability errors
    access_note: str = ''

    #: True when the adapter needs credentials that may not be configured
    requires_credentials: bool = False

    #: Consult robots.txt before fetching. Off for documented APIs, where
    #: robots.txt governs crawlers rather than API clients, and on for anything
    #: that reads pages or feeds published for browsers.
    check_robots: bool = False

    def __init__(self, client: Optional[PoliteClient] = None):
        self._client = client
        self._owns_client = client is None

    @property
    def client(self) -> PoliteClient:
        if self._client is None:
            self._client = PoliteClient()
        return self._client

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    # -- availability ------------------------------------------------------

    def is_available(self) -> Tuple[bool, str]:
        """
        Whether this adapter can run right now.

        Returns:
            ``(available, reason)``. The reason is only meaningful when
            unavailable.
        """
        return True, ''

    # -- fetching ----------------------------------------------------------

    @abstractmethod
    def fetch(self, query: SourceQuery) -> FetchResult:
        """Fetch items for a query. Must not raise for ordinary API errors."""

    # -- helpers for subclasses -------------------------------------------

    def _pseudonym(self, author: Optional[str]) -> Optional[str]:
        return pseudonymize_author(author, Config.CORPUS_AUTHOR_SALT)

    @staticmethod
    def _cutoff(query: SourceQuery) -> Optional[datetime]:
        """The oldest timestamp the query accepts, or None for no limit."""
        if not query.since_days:
            return None
        return datetime.now(timezone.utc) - timedelta(days=query.since_days)

    @staticmethod
    def _too_old(item: CorpusItem, cutoff: Optional[datetime]) -> bool:
        if cutoff is None or not item.created_at:
            return False
        try:
            return datetime.fromisoformat(item.created_at) < cutoff
        except ValueError:
            return False

    def _finish(
        self,
        items: List[CorpusItem],
        requests_made: int,
        errors: List[str],
        query: SourceQuery,
    ) -> FetchResult:
        """Apply the shared max_items cap and build the result."""
        truncated = len(items) > query.max_items
        return FetchResult(
            source=self.name,
            items=items[:query.max_items],
            requests_made=requests_made,
            errors=errors,
            truncated=truncated,
        )
