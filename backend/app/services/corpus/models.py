"""
Corpus data models.

A CorpusItem is the normalized shape every source adapter emits, so the
filtering and compaction stages never need to know where the text came from.
"""

import hashlib
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Item kinds. Comments are kept separate from posts because they are filtered
# far more aggressively - most of the noise lives in comment threads.
KIND_POST = 'post'
KIND_COMMENT = 'comment'
KIND_REVIEW = 'review'
KIND_ARTICLE = 'article'


def pseudonymize_author(author: Optional[str], salt: str) -> Optional[str]:
    """
    Turn an author handle into a stable pseudonym.

    Raw handles are personal data and are never stored. The salted digest still
    lets the pipeline count "how many distinct people said this" without
    keeping anything that identifies them.

    Args:
        author: Raw author handle from the source
        salt: Per-deployment salt

    Returns:
        A short opaque id, or None when there was no author
    """
    if not author:
        return None
    normalized = author.strip().lower()
    if not normalized or normalized in ('[deleted]', '[removed]', 'deleted', 'automoderator'):
        return None
    digest = hashlib.sha256(f"{salt}:{normalized}".encode('utf-8')).hexdigest()
    return f"u_{digest[:16]}"


def _parse_timestamp(value: Any) -> Optional[str]:
    """Coerce assorted source timestamp formats to an RFC3339 UTC string."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        # Normalize the trailing Z that most APIs emit
        candidate = text[:-1] + '+00:00' if text.endswith('Z') else text
        try:
            dt = datetime.fromisoformat(candidate)
        except ValueError:
            return None
        dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    return None


# Splits on non-alphanumerics and on camelCase / PascalCase boundaries, so a
# brand written as one word still yields its component tokens.
_BRAND_TOKEN_RE = re.compile(r'[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+')

# What may sit between two brand tokens in the wild.
_BRAND_SEPARATOR = r'[\s\-_\.]*'


def _brand_pattern(term: str) -> Optional[str]:
    """Build a separator-tolerant regex fragment for one blocklisted brand."""
    tokens = _BRAND_TOKEN_RE.findall(term or '')
    if not tokens:
        return None
    return _BRAND_SEPARATOR.join(re.escape(token) for token in tokens)


@dataclass
class CorpusItem:
    """One normalized piece of public discussion."""

    # Identity
    item_id: str                       # unique within (source, item_id)
    source: str                        # adapter name, e.g. "reddit"
    kind: str = KIND_POST

    # Content
    text: str = ""
    title: Optional[str] = None

    # Provenance
    url: Optional[str] = None
    author_pseudonym: Optional[str] = None
    created_at: Optional[str] = None   # RFC3339 UTC
    parent_id: Optional[str] = None    # set on comments
    channel: Optional[str] = None      # subreddit, video id, app id, feed title

    # Signals used later for weighting
    score: int = 0                     # upvotes / likes / helpful votes
    reply_count: int = 0
    rating: Optional[float] = None     # 1-5 for store reviews
    lang: Optional[str] = None

    # Relevance the item gets from where it came from rather than from its own
    # words. A review of an app the operator explicitly targeted is on-topic
    # even when it never repeats the category terms - "box arrived late again"
    # is exactly the objection worth keeping. Adapters set this; 0 means the
    # item has to earn its relevance from its text alone.
    provenance_relevance: float = 0.0

    # Populated by the filter stage
    quality_score: float = 0.0
    relevance_score: float = 0.0
    signals: Dict[str, Any] = field(default_factory=dict)

    meta: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.created_at = _parse_timestamp(self.created_at)

    @property
    def full_text(self) -> str:
        """Title and body joined, which is what the filters actually score."""
        if self.title and self.title.strip() and self.title.strip() != self.text.strip():
            return f"{self.title.strip()}\n{self.text.strip()}".strip()
        return self.text.strip()

    @property
    def uid(self) -> str:
        """Globally unique id across sources."""
        return f"{self.source}:{self.item_id}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SourceQuery:
    """What to fetch, shared by every adapter."""

    # Free-text topic - product category, competitor names, pain points
    terms: List[str] = field(default_factory=list)

    # Terms that must never be fetched. Use this to keep the campaign's own
    # brand and slogan out of the corpus, otherwise the simulation reads the
    # answer instead of predicting it.
    exclude_terms: List[str] = field(default_factory=list)

    # Per-source scoping. Adapters ignore keys they do not understand.
    subreddits: List[str] = field(default_factory=list)
    app_ids: List[str] = field(default_factory=list)
    video_ids: List[str] = field(default_factory=list)
    feed_urls: List[str] = field(default_factory=list)
    countries: List[str] = field(default_factory=lambda: ['us'])

    # Limits
    max_items: int = 500
    max_comments_per_post: int = 50
    since_days: Optional[int] = 365
    lang: Optional[str] = 'en'

    def query_string(self) -> str:
        """Join terms into a single search expression."""
        return ' OR '.join(f'"{t}"' if ' ' in t else t for t in self.terms if t.strip())

    def excludes_pattern(self) -> Optional[re.Pattern]:
        """
        Compiled matcher for the brand blocklist, or None when empty.

        This is the contamination guard: it keeps posts about the campaign's own
        brand out of a corpus that is supposed to describe the category, so the
        simulation predicts a reaction instead of reading one that already
        happened. That makes near-misses expensive, so matching is deliberately
        loose about separators - "HelloFresh" also blocks "hello fresh",
        "Hello-Fresh" and "hello_fresh".
        """
        alternatives = [
            pattern for term in self.exclude_terms
            if (pattern := _brand_pattern(term))
        ]
        if not alternatives:
            return None
        return re.compile(r'(?<!\w)(' + '|'.join(alternatives) + r')(?!\w)', re.IGNORECASE)


@dataclass
class FetchResult:
    """What one adapter returned for one query."""

    source: str
    items: List[CorpusItem] = field(default_factory=list)
    requests_made: int = 0
    errors: List[str] = field(default_factory=list)
    truncated: bool = False            # hit max_items or a rate limit

    def to_dict(self) -> Dict[str, Any]:
        return {
            'source': self.source,
            'item_count': len(self.items),
            'requests_made': self.requests_made,
            'errors': self.errors,
            'truncated': self.truncated,
        }
