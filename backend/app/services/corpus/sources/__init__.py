"""
Source adapter registry.

Sources are grouped by how much trust they need. The keyless group works out of
the box; the credentialed group needs free API keys but stays on official APIs.
Anything that would require scraping a site that does not offer an API is
deliberately absent.
"""

from typing import Any, Dict, List, Type

from .apple_reviews import AppleReviewsSource
from .base import SourceAdapter
from .hackernews import HackerNewsSource
from .reddit import RedditSource
from .rss import RssSource
from .youtube import YouTubeSource

#: Adapters that need no credentials at all.
KEYLESS_SOURCES: Dict[str, Type[SourceAdapter]] = {
    AppleReviewsSource.name: AppleReviewsSource,
    HackerNewsSource.name: HackerNewsSource,
    RssSource.name: RssSource,
}

#: Adapters on official APIs that need a free key.
CREDENTIALED_SOURCES: Dict[str, Type[SourceAdapter]] = {
    RedditSource.name: RedditSource,
    YouTubeSource.name: YouTubeSource,
}

SOURCE_REGISTRY: Dict[str, Type[SourceAdapter]] = {
    **KEYLESS_SOURCES,
    **CREDENTIALED_SOURCES,
}

#: Sensible default when the caller does not name any sources: everything that
#: works without setup.
DEFAULT_SOURCES: List[str] = list(KEYLESS_SOURCES)


def get_source(name: str, **kwargs: Any) -> SourceAdapter:
    """
    Instantiate one adapter by registry name.

    Raises:
        KeyError: unknown source name
    """
    if name not in SOURCE_REGISTRY:
        raise KeyError(
            f"unknown corpus source {name!r}; known sources: {sorted(SOURCE_REGISTRY)}"
        )
    return SOURCE_REGISTRY[name](**kwargs)


def describe_sources() -> List[Dict[str, Any]]:
    """
    Report every adapter and whether it can run right now.

    Intended for a settings screen: it tells the user which sources are live and
    exactly what is missing for the others.
    """
    described: List[Dict[str, Any]] = []
    for name, adapter_cls in SOURCE_REGISTRY.items():
        adapter = adapter_cls()
        try:
            available, reason = adapter.is_available()
        finally:
            adapter.close()
        described.append({
            'name': name,
            'available': available,
            'reason': reason,
            'requires_credentials': adapter_cls.requires_credentials,
            'access_note': adapter_cls.access_note,
        })
    return described


__all__ = [
    'SourceAdapter',
    'AppleReviewsSource',
    'HackerNewsSource',
    'RssSource',
    'RedditSource',
    'YouTubeSource',
    'SOURCE_REGISTRY',
    'KEYLESS_SOURCES',
    'CREDENTIALED_SOURCES',
    'DEFAULT_SOURCES',
    'get_source',
    'describe_sources',
]
