"""
Threads via a headless browser.

Unlike every other adapter here, this one does not sit on a documented API,
because Threads does not publish one for public search - the official Threads API
covers posting and reading your own account, not reading the platform. Reaching
public discussion therefore means rendering pages written for browsers, which is
a different thing to consuming an API and is treated as such:

* The source ships **disabled** in ``config/corpus.yml``. Enabling it is a
  deliberate act.
* ``robots.txt`` is honoured by default. Threads disallows most automated paths,
  so with the default settings this adapter will decline to fetch and say so.
  ``THREADS_RESPECT_ROBOTS=false`` overrides that, and whoever sets it owns the
  decision - it is a terms-of-service question, not a technical one.
* Author handles are pseudonymised like everywhere else, and the permalink is
  deliberately not stored, because on Threads the URL contains the handle that
  ``pseudonymize_author`` exists to keep out of the corpus.

What it is good for: Threads is where a lot of category conversation happens in
markets that under-use Reddit, so it fills a real gap in segment vocabulary.
"""

from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from ....config import Config
from ....utils.logger import get_logger
from ..models import KIND_COMMENT, KIND_POST, CorpusItem, FetchResult, SourceQuery
from . import threads_parser as parser
from . import threads_browser as browser
from .base import SourceAdapter

logger = get_logger('spiegel.corpus.threads')

SEARCH_URL = 'https://www.threads.net/search?q={query}&serp_type=default'

#: Terms are searched one at a time - Threads has no boolean search syntax, so a
#: joined `a OR b` query is read as literal text and matches almost nothing.
MAX_SEARCH_TERMS = 8


class ThreadsSource(SourceAdapter):
    """Fetch Threads posts and their replies through a rendered page."""

    name = 'threads'
    access_note = (
        'Renders threads.net in a headless browser; there is no public search '
        'API. Needs the optional playwright dependency, a session cookie file at '
        'THREADS_COOKIES_FILE, and THREADS_RESPECT_ROBOTS=false to fetch paths '
        'robots.txt disallows.'
    )
    requires_credentials = True
    check_robots = True

    def __init__(self, client=None, renderer: Optional[Any] = None):
        super().__init__(client)
        # Injectable so tests can drive the adapter without a browser.
        self._renderer = renderer

    # -- availability ------------------------------------------------------

    def is_available(self) -> Tuple[bool, str]:
        if self._renderer is not None:
            return True, ''
        if not browser.playwright_available():
            return False, (
                'playwright is not installed '
                '(pip install playwright && playwright install chromium)'
            )
        if not Config.THREADS_COOKIES_FILE:
            return False, 'THREADS_COOKIES_FILE not configured'
        if not browser.load_cookies(Config.THREADS_COOKIES_FILE):
            return False, (
                f'no usable Threads session in {Config.THREADS_COOKIES_FILE}; '
                'Threads shows a login wall to signed-out clients'
            )
        return True, ''

    # -- fetching ----------------------------------------------------------

    def fetch(self, query: SourceQuery) -> FetchResult:
        """
        Search each term, then pull replies from the posts with the most of them.

        Replies matter more here than on Reddit: a Threads post is short, and the
        reply chain is usually where the objection actually gets stated.
        """
        available, reason = self.is_available()
        if not available:
            return FetchResult(source=self.name, errors=[reason])

        terms = [term.strip() for term in query.terms if term.strip()][:MAX_SEARCH_TERMS]
        if not terms:
            return FetchResult(source=self.name, errors=['no search terms provided'])

        search_urls = [SEARCH_URL.format(query=quote(term)) for term in terms]

        blocked = self._robots_block(search_urls[0])
        if blocked:
            return FetchResult(source=self.name, errors=[blocked])

        renderer = self._build_renderer()
        errors: List[str] = []
        cutoff = self._cutoff(query)

        # -- posts ---------------------------------------------------------
        try:
            pages = browser.run_sync(
                lambda: renderer.render_many(search_urls, scrolls=Config.THREADS_SCROLLS)
            )
        except Exception as exc:
            return FetchResult(
                source=self.name,
                errors=[f'threads search failed: {exc}'],
                requests_made=getattr(renderer, 'pages_rendered', 0),
            )

        posts: List[CorpusItem] = []
        raw_by_id: Dict[str, Dict[str, Any]] = {}
        for url in search_urls:
            html = pages.get(url)
            if html is None:
                errors.append(f'threads search page did not render: {url}')
                continue
            for thread in parser.extract_thread_list(html):
                item = self._to_post(thread)
                if item is None or item.item_id in raw_by_id:
                    continue
                if self._too_old(item, cutoff):
                    continue
                raw_by_id[item.item_id] = thread
                posts.append(item)

        if not posts and not errors:
            errors.append(
                'threads returned no posts; usually an expired session cookie '
                'or a login wall'
            )

        # -- replies -------------------------------------------------------
        items: List[CorpusItem] = list(posts)
        budget = max(query.max_items - len(posts), 0)
        if budget and query.max_comments_per_post:
            items.extend(
                self._fetch_replies(posts, raw_by_id, renderer, query, budget, errors)
            )

        return self._finish(items, getattr(renderer, 'pages_rendered', 0), errors, query)

    def _fetch_replies(
        self,
        posts: List[CorpusItem],
        raw_by_id: Dict[str, Dict[str, Any]],
        renderer: Any,
        query: SourceQuery,
        budget: int,
        errors: List[str],
    ) -> List[CorpusItem]:
        """Open the busiest posts and harvest their reply chains."""
        ranked = sorted(posts, key=lambda p: p.reply_count, reverse=True)
        targets: List[Tuple[str, CorpusItem]] = []
        for post in ranked[:Config.THREADS_MAX_DETAIL_POSTS]:
            url = parser.post_url(raw_by_id.get(post.item_id, {}))
            if url:
                targets.append((url, post))

        if not targets:
            return []

        try:
            pages = browser.run_sync(
                lambda: renderer.render_many(
                    [url for url, _ in targets], scrolls=Config.THREADS_REPLY_SCROLLS
                )
            )
        except Exception as exc:
            errors.append(f'threads reply fetch failed: {exc}')
            return []

        replies: List[CorpusItem] = []
        for url, post in targets:
            if budget <= 0:
                break
            html = pages.get(url)
            if html is None:
                continue
            bundle = parser.extract_post_page(html)
            allowed = min(budget, query.max_comments_per_post)
            for raw in (bundle.get('replies') or [])[:allowed]:
                item = self._to_reply(raw, post)
                if item is None:
                    continue
                replies.append(item)
                budget -= 1
                if budget <= 0:
                    break
        return replies

    # -- helpers -----------------------------------------------------------

    def _build_renderer(self) -> Any:
        if self._renderer is not None:
            return self._renderer
        return browser.ThreadsRenderer(
            cookies=browser.load_cookies(Config.THREADS_COOKIES_FILE),
            browser_mode=Config.THREADS_BROWSER_MODE,
            nav_timeout_ms=int(Config.CORPUS_HTTP_TIMEOUT * 1000),
        )

    def _robots_block(self, url: str) -> Optional[str]:
        """The reason robots.txt forbids this fetch, or None when it allows it."""
        if not Config.THREADS_RESPECT_ROBOTS:
            logger.warning(
                'threads: robots.txt check disabled by THREADS_RESPECT_ROBOTS'
            )
            return None
        try:
            if self.client.allows(url):
                return None
        except Exception as exc:
            # A robots.txt that cannot be read is not permission to ignore it.
            return f'threads robots.txt check failed: {exc}'
        return (
            'threads robots.txt disallows this path; set THREADS_RESPECT_ROBOTS=false '
            'to override, which is a terms-of-service decision for the operator'
        )

    def _to_post(self, thread: Dict[str, Any]) -> Optional[CorpusItem]:
        post_id = thread.get('id') or thread.get('pk')
        text = (thread.get('text') or '').strip()
        if not post_id or not text:
            return None
        return CorpusItem(
            item_id=f"th_{post_id}",
            source=self.name,
            kind=KIND_POST,
            text=text,
            # Threads posts have no title; the filters read `text` alone.
            title=None,
            # The permalink embeds the author handle, which is exactly what
            # pseudonymisation keeps out of the corpus. `code` is kept instead:
            # enough to re-find the post, useless for re-identifying anyone.
            url=None,
            author_pseudonym=self._pseudonym(thread.get('username')),
            created_at=thread.get('published_on'),
            channel='threads',
            score=int(thread.get('like_count') or 0),
            reply_count=int(thread.get('reply_count') or 0),
            lang=None,
            meta={
                'code': thread.get('code'),
                'repost_count': int(thread.get('repost_count') or 0),
                'has_media': bool(thread.get('images') or thread.get('videos')),
                'author_verified': bool(thread.get('user_verified')),
            },
        )

    def _to_reply(self, raw: Dict[str, Any], post: CorpusItem) -> Optional[CorpusItem]:
        reply_id = raw.get('id') or raw.get('pk')
        text = (raw.get('text') or '').strip()
        if not reply_id or not text:
            return None
        return CorpusItem(
            item_id=f"thc_{reply_id}",
            source=self.name,
            kind=KIND_COMMENT,
            text=text,
            url=None,
            author_pseudonym=self._pseudonym(raw.get('username')),
            created_at=raw.get('published_on'),
            # Point at the post so relevance inheritance has something to find:
            # a two-word Threads reply is on-topic when its parent is.
            parent_id=post.item_id,
            channel=post.channel,
            score=int(raw.get('like_count') or 0),
            reply_count=int(raw.get('reply_count') or 0),
            lang=None,
            meta={'code': raw.get('code'), 'parent_code': post.meta.get('code')},
        )
