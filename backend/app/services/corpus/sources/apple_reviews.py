"""
Apple App Store customer reviews.

Apple publishes reviews through a documented public RSS-as-JSON endpoint. No
key, no account, no scraping - this is the safest high-signal source available
and the one worth wiring up first. Reviews are also the densest source of
purchase objections, which is exactly the KPI the report currently has to
invent.

Endpoint: ``itunes.apple.com/{country}/rss/customerreviews/...``
Limits:   10 pages x 50 reviews per app per country.
"""

from typing import Any, Dict, List, Optional, Tuple

from ....utils.logger import get_logger
from ..models import KIND_REVIEW, CorpusItem, FetchResult, SourceQuery
from .base import SourceAdapter

logger = get_logger('mirofish.corpus.apple')

REVIEWS_URL = (
    'https://itunes.apple.com/{country}/rss/customerreviews/'
    'page={page}/id={app_id}/sortby=mostrecent/json'
)
SEARCH_URL = 'https://itunes.apple.com/search'

# Apple caps the review feed at 10 pages regardless of what you ask for.
MAX_PAGES = 10

# Reviews are attached to an app the operator (or the term search) already
# picked as representative of the category, so they are on-topic by provenance.
# Without this floor the relevance stage discards the objections that name a
# symptom without repeating the category words, which is most of them.
REVIEW_PROVENANCE = 0.4


def _label(node: Any) -> str:
    """Pull the ``label`` out of Apple's uniformly wrapped fields."""
    if isinstance(node, dict):
        return str(node.get('label', '') or '')
    if isinstance(node, list) and node:
        return _label(node[0])
    return str(node or '')


def _as_list(node: Any) -> List[Any]:
    """Apple returns a bare object instead of a list when there is one entry."""
    if node is None:
        return []
    return node if isinstance(node, list) else [node]


class AppleReviewsSource(SourceAdapter):
    """Fetch App Store reviews for one or more app ids."""

    name = 'apple_reviews'
    access_note = 'Public Apple RSS endpoint. No credentials required.'
    requires_credentials = False

    def search_apps(self, term: str, country: str = 'us', limit: int = 10) -> List[Dict[str, Any]]:
        """
        Resolve a product name to App Store ids.

        Use this to turn "budget meal delivery" into the concrete app ids whose
        reviews describe the category.
        """
        try:
            payload = self.client.get_json(
                SEARCH_URL,
                params={
                    'term': term,
                    'entity': 'software',
                    'country': country,
                    'limit': limit,
                },
            )
        except Exception as exc:
            logger.warning("apple app search failed for %r: %s", term, exc)
            return []

        return [
            {
                'app_id': str(entry.get('trackId')),
                'name': entry.get('trackName'),
                'seller': entry.get('sellerName'),
                'rating': entry.get('averageUserRating'),
                'rating_count': entry.get('userRatingCount'),
                'genres': entry.get('genres', []),
            }
            for entry in payload.get('results', [])
            if entry.get('trackId')
        ]

    def fetch(self, query: SourceQuery) -> FetchResult:
        """
        Fetch reviews for ``query.app_ids`` across ``query.countries``.

        When no app ids are given, the terms are resolved to apps first so the
        caller can pass a plain category description.
        """
        errors: List[str] = []
        requests_made = 0
        items: List[CorpusItem] = []
        cutoff = self._cutoff(query)

        app_ids = list(query.app_ids)
        if not app_ids and query.terms:
            for term in query.terms[:3]:
                matches = self.search_apps(term, country=query.countries[0] if query.countries else 'us')
                requests_made += 1
                app_ids.extend(m['app_id'] for m in matches[:3])
            app_ids = list(dict.fromkeys(app_ids))
            if not app_ids:
                errors.append('no apps matched the query terms')

        for app_id in app_ids:
            for country in query.countries or ['us']:
                fetched, made, error = self._fetch_app(app_id, country, query, cutoff)
                items.extend(fetched)
                requests_made += made
                if error:
                    errors.append(error)
                if len(items) >= query.max_items:
                    break
            if len(items) >= query.max_items:
                break

        return self._finish(items, requests_made, errors, query)

    def _fetch_app(
        self,
        app_id: str,
        country: str,
        query: SourceQuery,
        cutoff,
    ) -> Tuple[List[CorpusItem], int, Optional[str]]:
        """Page through one app's review feed for one storefront."""
        items: List[CorpusItem] = []
        requests_made = 0

        for page in range(1, MAX_PAGES + 1):
            url = REVIEWS_URL.format(country=country, page=page, app_id=app_id)
            try:
                payload = self.client.get_json(url, check_robots=self.check_robots)
                requests_made += 1
            except Exception as exc:
                return items, requests_made, f"apple app {app_id}/{country} page {page}: {exc}"

            entries = _as_list((payload.get('feed') or {}).get('entry'))
            if not entries:
                break

            stale_on_page = 0
            for entry in entries:
                item = self._to_item(entry, app_id, country)
                if item is None:
                    continue
                if self._too_old(item, cutoff):
                    stale_on_page += 1
                    continue
                items.append(item)

            # The feed is sorted newest first, so a fully stale page means every
            # later page is stale too.
            if stale_on_page == len(entries):
                break
            if len(items) >= query.max_items:
                break

        return items, requests_made, None

    def _to_item(self, entry: Dict[str, Any], app_id: str, country: str) -> Optional[CorpusItem]:
        """Convert one feed entry to a CorpusItem, or None if it is not a review."""
        body = _label(entry.get('content'))
        review_id = _label(entry.get('id'))
        if not body.strip() or not review_id:
            return None

        rating_text = _label(entry.get('im:rating'))
        try:
            rating = float(rating_text) if rating_text else None
        except ValueError:
            rating = None

        vote_text = _label(entry.get('im:voteSum'))
        try:
            votes = int(vote_text) if vote_text else 0
        except ValueError:
            votes = 0

        return CorpusItem(
            item_id=f"{app_id}-{country}-{review_id}",
            source=self.name,
            kind=KIND_REVIEW,
            text=body,
            title=_label(entry.get('title')) or None,
            url=_label((entry.get('link') or {}).get('attributes', {}).get('href')) or None,
            author_pseudonym=self._pseudonym(_label((entry.get('author') or {}).get('name'))),
            created_at=_label(entry.get('updated')) or None,
            channel=f"app:{app_id}",
            score=votes,
            rating=rating,
            lang=country,
            provenance_relevance=REVIEW_PROVENANCE,
            meta={
                'country': country,
                'app_version': _label(entry.get('im:version')) or None,
            },
        )
