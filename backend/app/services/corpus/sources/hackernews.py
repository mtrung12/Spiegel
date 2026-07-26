"""
Hacker News via the Algolia search API.

Free, keyless, documented, and generous with its rate limit. Useful for
technical and B2B categories where the buying objections show up in comment
threads rather than in store reviews.

Endpoint: ``hn.algolia.com/api/v1/search``
"""

import html
import re
from typing import Any, Dict, List, Optional

from ....utils.logger import get_logger
from ..models import KIND_COMMENT, KIND_POST, CorpusItem, FetchResult, SourceQuery
from .base import SourceAdapter

logger = get_logger('mirofish.corpus.hn')

SEARCH_URL = 'https://hn.algolia.com/api/v1/search'
HITS_PER_PAGE = 100
MAX_PAGES = 20

TAG_RE = re.compile(r'<[^>]+>')


def _clean_html(raw: Optional[str]) -> str:
    """HN comment bodies are HTML fragments. Flatten them to plain text."""
    if not raw:
        return ''
    text = raw.replace('<p>', '\n').replace('</p>', '')
    text = TAG_RE.sub(' ', text)
    return html.unescape(text).strip()


class HackerNewsSource(SourceAdapter):
    """Fetch HN stories and comments matching the topic terms."""

    name = 'hackernews'
    access_note = 'Public Algolia HN API. No credentials required.'
    requires_credentials = False

    def fetch(self, query: SourceQuery) -> FetchResult:
        """Fetch both stories and their comments for the query terms."""
        errors: List[str] = []
        requests_made = 0
        items: List[CorpusItem] = []

        terms = [t.strip() for t in query.terms if t.strip()]
        if not terms:
            return FetchResult(source=self.name, errors=['no search terms provided'])

        numeric_filters = ''
        cutoff = self._cutoff(query)
        if cutoff:
            numeric_filters = f"created_at_i>{int(cutoff.timestamp())}"

        # Algolia treats ``query`` as plain text, not as a boolean expression -
        # an "a OR b" string is searched literally and matches nothing. Each
        # term therefore gets its own search, and duplicates are merged here.
        seen: set = set()

        for term in terms:
            if len(items) >= query.max_items:
                break
            # Stories first so comments can inherit their relevance later.
            for tags, kind in (('story', KIND_POST), ('comment', KIND_COMMENT)):
                for page in range(MAX_PAGES):
                    if len(items) >= query.max_items:
                        break
                    params: Dict[str, Any] = {
                        'query': term,
                        'tags': tags,
                        'hitsPerPage': HITS_PER_PAGE,
                        'page': page,
                    }
                    if numeric_filters:
                        params['numericFilters'] = numeric_filters

                    try:
                        payload = self.client.get_json(SEARCH_URL, params=params)
                        requests_made += 1
                    except Exception as exc:
                        errors.append(f"hn {term!r} {tags} page {page}: {exc}")
                        break

                    hits = payload.get('hits') or []
                    if not hits:
                        break

                    for hit in hits:
                        item = self._to_item(hit, kind)
                        if item is None or item.item_id in seen:
                            continue
                        seen.add(item.item_id)
                        items.append(item)

                    if page + 1 >= (payload.get('nbPages') or 0):
                        break

        return self._finish(items, requests_made, errors, query)

    def _to_item(self, hit: Dict[str, Any], kind: str) -> Optional[CorpusItem]:
        """Convert one Algolia hit."""
        object_id = str(hit.get('objectID') or '')
        if not object_id:
            return None

        if kind == KIND_COMMENT:
            body = _clean_html(hit.get('comment_text'))
            title = None
            parent_id = str(hit.get('story_id')) if hit.get('story_id') else None
        else:
            body = _clean_html(hit.get('story_text')) or (hit.get('url') or '')
            title = hit.get('title')
            parent_id = None

        if not body.strip() and not (title or '').strip():
            return None

        return CorpusItem(
            item_id=object_id,
            source=self.name,
            kind=kind,
            text=body,
            title=title,
            url=f"https://news.ycombinator.com/item?id={object_id}",
            author_pseudonym=self._pseudonym(hit.get('author')),
            created_at=hit.get('created_at_i') or hit.get('created_at'),
            parent_id=parent_id,
            channel='hackernews',
            score=int(hit.get('points') or 0),
            reply_count=int(hit.get('num_comments') or 0),
            lang='en',
            meta={'story_title': hit.get('story_title')},
        )
