"""
Generic RSS and Atom feeds.

Feeds are published for machine consumption, so reading them is unambiguously
allowed. This is the escape hatch for category press, competitor blogs, review
sites and Google News queries, none of which need a bespoke adapter.

Parsing uses the stdlib XML parser rather than feedparser to avoid adding a
dependency for what amounts to two element names.
"""

import html
import re
import xml.etree.ElementTree as ET
from typing import List, Optional

from ....utils.logger import get_logger
from ..models import KIND_ARTICLE, CorpusItem, FetchResult, SourceQuery
from .base import SourceAdapter

logger = get_logger('mirofish.corpus.rss')

TAG_RE = re.compile(r'<[^>]+>')
ATOM_NS = '{http://www.w3.org/2005/Atom}'


def _strip_html(raw: Optional[str]) -> str:
    if not raw:
        return ''
    return html.unescape(TAG_RE.sub(' ', raw)).strip()


def _text(element: Optional[ET.Element]) -> str:
    return _strip_html(element.text if element is not None else '')


class RssSource(SourceAdapter):
    """Fetch items from arbitrary RSS 2.0 or Atom feeds."""

    name = 'rss'
    access_note = 'Any public feed URL. Honours robots.txt.'
    requires_credentials = False
    # Feeds are served off ordinary web hosts rather than API domains, so the
    # site's robots.txt is the right thing to respect here.
    check_robots = True

    def fetch(self, query: SourceQuery) -> FetchResult:
        """Fetch every feed in ``query.feed_urls``."""
        errors: List[str] = []
        requests_made = 0
        items: List[CorpusItem] = []
        cutoff = self._cutoff(query)

        if not query.feed_urls:
            return FetchResult(source=self.name, errors=['no feed_urls provided'])

        for feed_url in query.feed_urls:
            if len(items) >= query.max_items:
                break
            try:
                body = self.client.get_text(feed_url, check_robots=self.check_robots)
                requests_made += 1
            except PermissionError as exc:
                errors.append(f"rss {feed_url}: {exc}")
                continue
            except Exception as exc:
                errors.append(f"rss {feed_url}: {exc}")
                continue

            try:
                parsed = self._parse(body, feed_url)
            except ET.ParseError as exc:
                errors.append(f"rss {feed_url}: malformed feed ({exc})")
                continue

            for item in parsed:
                if not self._too_old(item, cutoff):
                    items.append(item)

        return self._finish(items, requests_made, errors, query)

    def _parse(self, body: str, feed_url: str) -> List[CorpusItem]:
        """Parse one feed document into items, handling RSS 2.0 and Atom."""
        root = ET.fromstring(body)
        items: List[CorpusItem] = []

        # RSS 2.0
        channel = root.find('channel')
        if channel is not None:
            feed_title = _text(channel.find('title')) or feed_url
            for entry in channel.findall('item'):
                item = self._from_rss_item(entry, feed_title, feed_url)
                if item is not None:
                    items.append(item)
            return items

        # Atom
        feed_title = _text(root.find(f'{ATOM_NS}title')) or feed_url
        for entry in root.findall(f'{ATOM_NS}entry'):
            item = self._from_atom_entry(entry, feed_title, feed_url)
            if item is not None:
                items.append(item)
        return items

    def _from_rss_item(self, entry: ET.Element, feed_title: str, feed_url: str) -> Optional[CorpusItem]:
        title = _text(entry.find('title'))
        body = _text(entry.find('description'))
        link = _text(entry.find('link'))
        guid = _text(entry.find('guid')) or link or title
        if not guid or not (title or body):
            return None

        return CorpusItem(
            item_id=guid,
            source=self.name,
            kind=KIND_ARTICLE,
            text=body,
            title=title or None,
            url=link or None,
            author_pseudonym=self._pseudonym(_text(entry.find('author'))),
            created_at=self._parse_rfc822(_text(entry.find('pubDate'))),
            channel=feed_title,
            meta={'feed_url': feed_url},
        )

    def _from_atom_entry(self, entry: ET.Element, feed_title: str, feed_url: str) -> Optional[CorpusItem]:
        title = _text(entry.find(f'{ATOM_NS}title'))
        body = (
            _text(entry.find(f'{ATOM_NS}content'))
            or _text(entry.find(f'{ATOM_NS}summary'))
        )
        link_el = entry.find(f'{ATOM_NS}link')
        link = link_el.get('href') if link_el is not None else None
        entry_id = _text(entry.find(f'{ATOM_NS}id')) or link or title
        if not entry_id or not (title or body):
            return None

        author_el = entry.find(f'{ATOM_NS}author')
        author = _text(author_el.find(f'{ATOM_NS}name')) if author_el is not None else None

        return CorpusItem(
            item_id=entry_id,
            source=self.name,
            kind=KIND_ARTICLE,
            text=body,
            title=title or None,
            url=link,
            author_pseudonym=self._pseudonym(author),
            created_at=_text(entry.find(f'{ATOM_NS}updated')) or None,
            channel=feed_title,
            meta={'feed_url': feed_url},
        )

    @staticmethod
    def _parse_rfc822(value: str) -> Optional[str]:
        """RSS uses RFC 822 dates, which fromisoformat cannot read."""
        if not value:
            return None
        from email.utils import parsedate_to_datetime
        try:
            return parsedate_to_datetime(value).isoformat()
        except (TypeError, ValueError):
            return None
