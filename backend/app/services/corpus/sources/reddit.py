"""
Reddit via the official OAuth API.

This is the read-only application-only flow: register a "script" app at
https://www.reddit.com/prefs/apps, put the id and secret in the environment,
and the adapter authenticates itself with no user account involved. Staying on
the official API is what keeps this safe - the unauthenticated ``.json``
endpoints are rate limited into uselessness and scraping the HTML site is a
terms violation.

Reddit is the best source of segment vocabulary: how people in a category
actually describe their problem, in their own words.
"""

import time
from typing import Any, Dict, List, Optional, Tuple

from ....config import Config
from ....utils.logger import get_logger
from ..models import KIND_COMMENT, KIND_POST, CorpusItem, FetchResult, SourceQuery
from .base import SourceAdapter

logger = get_logger('mirofish.corpus.reddit')

TOKEN_URL = 'https://www.reddit.com/api/v1/access_token'
API_BASE = 'https://oauth.reddit.com'
PAGE_SIZE = 100
MAX_PAGES = 10

# Reddit's own guidance for application-only clients.
REDDIT_RATE_PER_SECOND = 1.0


class RedditSource(SourceAdapter):
    """Fetch Reddit posts and their comment trees."""

    name = 'reddit'
    access_note = (
        'Official Reddit OAuth API. Requires REDDIT_CLIENT_ID and '
        'REDDIT_CLIENT_SECRET from https://www.reddit.com/prefs/apps '
        '(free, "script" app type).'
    )
    requires_credentials = True

    def __init__(self, client=None):
        super().__init__(client)
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0

    def is_available(self) -> Tuple[bool, str]:
        if not Config.REDDIT_CLIENT_ID or not Config.REDDIT_CLIENT_SECRET:
            return False, 'REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET not configured'
        return True, ''

    # -- auth --------------------------------------------------------------

    def _access_token(self) -> str:
        """Fetch or reuse the application-only bearer token."""
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token

        response = self.client.request(
            'POST',
            TOKEN_URL,
            auth=(Config.REDDIT_CLIENT_ID, Config.REDDIT_CLIENT_SECRET),
            data={'grant_type': 'client_credentials'},
        )
        response.raise_for_status()
        payload = response.json()

        token = payload.get('access_token')
        if not token:
            raise RuntimeError(f"reddit returned no access_token: {payload}")
        self._token = token
        self._token_expires_at = time.time() + float(payload.get('expires_in', 3600))
        return token

    def _headers(self) -> Dict[str, str]:
        return {'Authorization': f'Bearer {self._access_token()}'}

    # -- fetching ----------------------------------------------------------

    def fetch(self, query: SourceQuery) -> FetchResult:
        """
        Search Reddit for the topic terms, then pull comments on the top posts.

        Comments carry ``parent_id`` so the relevance stage can let a short
        on-topic reply inherit its thread's relevance.
        """
        available, reason = self.is_available()
        if not available:
            return FetchResult(source=self.name, errors=[reason])

        errors: List[str] = []
        requests_made = 0
        posts: List[CorpusItem] = []
        cutoff = self._cutoff(query)

        search = query.query_string()
        if not search:
            return FetchResult(source=self.name, errors=['no search terms provided'])

        time_filter = self._time_filter(query.since_days)
        targets = query.subreddits or [None]

        for subreddit in targets:
            fetched, made, error = self._search(search, subreddit, time_filter, query, cutoff)
            posts.extend(fetched)
            requests_made += made
            if error:
                errors.append(error)
            if len(posts) >= query.max_items:
                break

        # Comments are pulled from the strongest posts, since a thread with no
        # engagement has no discussion worth reading.
        items: List[CorpusItem] = list(posts)
        budget = max(query.max_items - len(posts), 0)
        if budget and query.max_comments_per_post:
            ranked = sorted(posts, key=lambda p: p.reply_count, reverse=True)
            for post in ranked:
                if budget <= 0:
                    break
                comments, made, error = self._comments(post, query)
                requests_made += made
                if error:
                    errors.append(error)
                    continue
                items.extend(comments[:budget])
                budget -= len(comments[:budget])

        return self._finish(items, requests_made, errors, query)

    @staticmethod
    def _time_filter(since_days: Optional[int]) -> str:
        """Map a day window onto Reddit's coarse ``t`` parameter."""
        if not since_days:
            return 'all'
        if since_days <= 1:
            return 'day'
        if since_days <= 7:
            return 'week'
        if since_days <= 31:
            return 'month'
        if since_days <= 365:
            return 'year'
        return 'all'

    def _search(
        self,
        search: str,
        subreddit: Optional[str],
        time_filter: str,
        query: SourceQuery,
        cutoff,
    ) -> Tuple[List[CorpusItem], int, Optional[str]]:
        """Page through one search listing."""
        items: List[CorpusItem] = []
        requests_made = 0
        after: Optional[str] = None

        url = f"{API_BASE}/r/{subreddit}/search" if subreddit else f"{API_BASE}/search"

        for _ in range(MAX_PAGES):
            params: Dict[str, Any] = {
                'q': search,
                'limit': PAGE_SIZE,
                'sort': 'relevance',
                't': time_filter,
                'type': 'link',
                'raw_json': 1,
            }
            if subreddit:
                params['restrict_sr'] = 1
            if after:
                params['after'] = after

            try:
                payload = self.client.get_json(url, params=params, headers=self._headers())
                requests_made += 1
            except Exception as exc:
                return items, requests_made, f"reddit search {subreddit or 'all'}: {exc}"

            data = payload.get('data') or {}
            children = data.get('children') or []
            if not children:
                break

            for child in children:
                item = self._to_post(child.get('data') or {})
                if item is None or self._too_old(item, cutoff):
                    continue
                items.append(item)

            after = data.get('after')
            if not after or len(items) >= query.max_items:
                break

        return items, requests_made, None

    def _comments(
        self,
        post: CorpusItem,
        query: SourceQuery,
    ) -> Tuple[List[CorpusItem], int, Optional[str]]:
        """Fetch the comment tree for one post, flattened."""
        article_id = post.meta.get('article_id')
        if not article_id:
            return [], 0, None

        try:
            payload = self.client.get_json(
                f"{API_BASE}/comments/{article_id}",
                params={
                    'limit': query.max_comments_per_post,
                    'depth': 3,
                    'sort': 'top',
                    'raw_json': 1,
                },
                headers=self._headers(),
            )
        except Exception as exc:
            return [], 1, f"reddit comments {article_id}: {exc}"

        # The response is [post listing, comment listing]
        if not isinstance(payload, list) or len(payload) < 2:
            return [], 1, None

        comments: List[CorpusItem] = []
        self._walk_comments(
            (payload[1].get('data') or {}).get('children') or [],
            post,
            comments,
            query.max_comments_per_post,
        )
        return comments, 1, None

    def _walk_comments(
        self,
        children: List[Dict[str, Any]],
        post: CorpusItem,
        out: List[CorpusItem],
        limit: int,
    ) -> None:
        """Depth-first flatten of the comment tree."""
        for child in children:
            if len(out) >= limit:
                return
            # "more" nodes are pagination stubs, not comments
            if child.get('kind') != 't1':
                continue
            data = child.get('data') or {}
            item = self._to_comment(data, post)
            if item is not None:
                out.append(item)

            replies = data.get('replies')
            if isinstance(replies, dict):
                self._walk_comments(
                    (replies.get('data') or {}).get('children') or [], post, out, limit
                )

    def _to_post(self, data: Dict[str, Any]) -> Optional[CorpusItem]:
        article_id = data.get('id')
        if not article_id:
            return None
        return CorpusItem(
            item_id=f"t3_{article_id}",
            source=self.name,
            kind=KIND_POST,
            text=data.get('selftext') or '',
            title=data.get('title'),
            url=f"https://reddit.com{data.get('permalink', '')}",
            author_pseudonym=self._pseudonym(data.get('author')),
            created_at=data.get('created_utc'),
            channel=f"r/{data.get('subreddit')}",
            score=int(data.get('score') or 0),
            reply_count=int(data.get('num_comments') or 0),
            lang=None,
            meta={
                'article_id': article_id,
                'subreddit': data.get('subreddit'),
                'over_18': bool(data.get('over_18')),
                'upvote_ratio': data.get('upvote_ratio'),
            },
        )

    def _to_comment(self, data: Dict[str, Any], post: CorpusItem) -> Optional[CorpusItem]:
        comment_id = data.get('id')
        body = data.get('body') or ''
        if not comment_id or not body.strip():
            return None
        return CorpusItem(
            item_id=f"t1_{comment_id}",
            source=self.name,
            kind=KIND_COMMENT,
            text=body,
            url=f"https://reddit.com{data.get('permalink', '')}",
            author_pseudonym=self._pseudonym(data.get('author')),
            created_at=data.get('created_utc'),
            # Point at the post so relevance inheritance has something to find
            parent_id=post.item_id,
            channel=post.channel,
            score=int(data.get('score') or 0),
            lang=None,
            meta={'subreddit': post.meta.get('subreddit'), 'depth': data.get('depth')},
        )
