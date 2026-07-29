"""
Source adapter registry.

Sources are split by how they reach their data, because the two kinds carry
different obligations. API sources consume a documented, rate-limited endpoint
and are safe to leave enabled. Browser sources render pages published for people,
which is a terms-of-service question rather than a technical one - they ship
disabled in config/corpus.yml and refuse to fetch until an operator says
otherwise.
"""

from typing import Any, Dict, List, Type

from .base import SourceAdapter
from .reddit import RedditSource
from .threads import ThreadsSource

#: Adapters on official APIs that need a free key.
CREDENTIALED_SOURCES: Dict[str, Type[SourceAdapter]] = {
    RedditSource.name: RedditSource,
}

#: Adapters that render pages instead of calling an API. Off by default; see the
#: module docstring in threads.py for what enabling one commits you to.
BROWSER_SOURCES: Dict[str, Type[SourceAdapter]] = {
    ThreadsSource.name: ThreadsSource,
}

SOURCE_REGISTRY: Dict[str, Type[SourceAdapter]] = {
    **CREDENTIALED_SOURCES,
    **BROWSER_SOURCES,
}

#: Sensible default when the caller does not name any sources: everything
#: registered. A credentialed source without its keys reports itself
#: unavailable and is skipped with a reason, so the effective default is still
#: "whatever works right now" - but keys you did configure actually get used.
DEFAULT_SOURCES: List[str] = list(SOURCE_REGISTRY)


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
    'RedditSource',
    'ThreadsSource',
    'SOURCE_REGISTRY',
    'CREDENTIALED_SOURCES',
    'BROWSER_SOURCES',
    'DEFAULT_SOURCES',
    'get_source',
    'describe_sources',
]
