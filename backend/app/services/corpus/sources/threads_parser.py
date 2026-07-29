"""
Threads HTML parsing, with no browser and no network.

Threads does not render post text into the DOM in any stable way. What it does
ship is the data that hydrates the page: one or more
``<script type="application/json" data-sjs>`` blobs holding the same objects the
mobile app receives. Reading those is far more durable than chasing CSS
selectors, and it is the only part of Threads ingestion that can be unit tested
offline - which is why it lives in its own module, away from the Playwright
adapter that feeds it.

Everything here is a pure function: HTML string in, plain dicts out.
"""

import json
import re
from typing import Any, Dict, List, Optional

# lxml parses the malformed markup Threads actually serves far better than the
# regex fallback, but it is only worth installing for deployments that run this
# source. Absent, the regex path still finds the blobs in practice.
try:
    from lxml import html as _lxml_html
except ImportError:  # pragma: no cover - exercised by deployments without lxml
    _lxml_html = None

_SCRIPT_RE = re.compile(
    r'<script[^>]*type="application/json"[^>]*data-sjs[^>]*>(.*?)</script>',
    re.DOTALL,
)

# Authors continuing a story across posts label the parts. Strip the label
# before stitching so the corpus holds prose rather than "1/3" scaffolding.
_PART_MARKER_LEADING_RE = re.compile(
    r"""^\s*
    [(\[]?\s*
    (?:part\s+)?
    \d+
    \s*(?:/|of|\.)\s*
    \d+
    \s*[)\]]?
    \s*[:.\-)]?\s*
    """,
    re.IGNORECASE | re.VERBOSE,
)
_PART_LABEL_LEADING_RE = re.compile(r'^\s*part\s+\d+\s*[:.\-)]?\s*', re.IGNORECASE)
_PART_MARKER_TRAILING_RE = re.compile(
    r'\s*[(\[]?\s*\d+\s*(?:/|of)\s*\d+\s*[)\]]?\s*$', re.IGNORECASE
)


def strip_part_marker(text: str) -> str:
    """
    Remove a leading or trailing part marker from one chunk of a story.

    Only the head and tail are touched, so a mid-sentence "I made $1/2 in tips"
    survives intact.
    """
    if not text:
        return text
    stripped = _PART_MARKER_LEADING_RE.sub('', text, count=1)
    stripped = _PART_LABEL_LEADING_RE.sub('', stripped, count=1)
    stripped = _PART_MARKER_TRAILING_RE.sub('', stripped, count=1)
    return stripped.strip()


def _walk_lookup(key: str, data: Any, out: List[Any]) -> None:
    """Collect every value stored under ``key``, at any depth."""
    if isinstance(data, dict):
        for k, v in data.items():
            if k == key:
                out.append(v)
            _walk_lookup(key, v, out)
    elif isinstance(data, list):
        for item in data:
            _walk_lookup(key, item, out)


def nested_lookup(key: str, data: Any) -> List[Any]:
    """Every value under ``key`` anywhere in a nested structure."""
    out: List[Any] = []
    _walk_lookup(key, data, out)
    return out


def _safe(data: Any, *path: Any, default: Any = None) -> Any:
    """Nested getter that tolerates missing keys, wrong types and short lists."""
    cur = data
    for step in path:
        if cur is None:
            return default
        if isinstance(step, int):
            if not isinstance(cur, list) or step >= len(cur) or step < -len(cur):
                return default
            cur = cur[step]
        else:
            if not isinstance(cur, dict):
                return default
            cur = cur.get(step)
    return cur if cur is not None else default


def iter_sjs_blobs(html: str) -> List[str]:
    """Extract the raw text of every ``data-sjs`` JSON script tag."""
    if _lxml_html is not None:
        try:
            tree = _lxml_html.fromstring(html)
            blobs = [
                node.text_content()
                for node in tree.xpath('//script[@type="application/json" and @data-sjs]')
                if node.text_content()
            ]
            if blobs:
                return blobs
        except Exception:
            # Malformed enough to defeat lxml; the regex is less fussy.
            pass
    return _SCRIPT_RE.findall(html)


def parse_thread(data: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten one ``thread_items`` entry into the fields the adapter needs."""
    post = data.get('post') or {}
    user = post.get('user') or {}
    text_info = post.get('text_post_app_info') or {}

    images: List[str] = []
    for media in post.get('carousel_media') or []:
        candidates = _safe(media, 'image_versions2', 'candidates', default=[]) or []
        # Index 1 is the mid-size render when present; index 0 is the original.
        if len(candidates) > 1 and candidates[1].get('url'):
            images.append(candidates[1]['url'])
        elif candidates and candidates[0].get('url'):
            images.append(candidates[0]['url'])

    videos = [v.get('url') for v in (post.get('video_versions') or []) if v.get('url')]

    return {
        'text': _safe(post, 'caption', 'text') or '',
        'published_on': post.get('taken_at'),
        'id': post.get('id'),
        'pk': post.get('pk'),
        'code': post.get('code'),
        'username': user.get('username'),
        'user_pk': user.get('pk'),
        'user_verified': user.get('is_verified'),
        'like_count': post.get('like_count'),
        'reply_count': text_info.get('direct_reply_count'),
        'repost_count': text_info.get('repost_count'),
        'images': images or None,
        'videos': videos or None,
    }


def extract_thread_list(html: str) -> List[Dict[str, Any]]:
    """
    Parse every thread visible on a page into one dict each.

    Threads represents a chain as an array of ``thread_items``. Where the author
    continued their own chain, the follow-ups belong to the same story, so they
    are stitched onto the root - a corpus item that stops mid-sentence at the
    "1/3" boundary is worse than useless for reading how people talk.
    """
    threads: List[Dict[str, Any]] = []
    for blob in iter_sjs_blobs(html):
        if 'thread_items' not in blob:
            continue
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue

        for chain in nested_lookup('thread_items', data):
            if not isinstance(chain, list):
                continue
            items = [parse_thread(entry) for entry in chain if entry]
            items = [item for item in items if item.get('text')]
            if not items:
                continue

            root = items[0]
            root_author = root.get('user_pk')
            parts = [strip_part_marker(root['text'])]
            for follow_up in items[1:]:
                if follow_up.get('user_pk') == root_author and follow_up.get('text'):
                    parts.append(strip_part_marker(follow_up['text']))

            root['text'] = '\n\n'.join(part for part in parts if part)
            threads.append(root)
    return threads


def extract_post_page(html: str) -> Dict[str, Any]:
    """
    Parse a single post page into ``{'thread': ..., 'replies': [...]}``.

    A multi-part story sometimes arrives as several sibling chains rather than
    one, all authored by the same account. Siblings written by the root author
    are treated as continuations and folded into the story in timestamp order;
    everything else is a genuine reply.
    """
    threads = extract_thread_list(html)
    if not threads:
        return {'thread': None, 'replies': []}

    main = threads[0]
    main_author = main.get('user_pk')

    continuations: List[Dict[str, Any]] = []
    replies: List[Dict[str, Any]] = []
    for thread in threads[1:]:
        if main_author and thread.get('user_pk') == main_author and thread.get('text'):
            continuations.append(thread)
        else:
            replies.append(thread)

    if continuations:
        continuations.sort(key=lambda t: t.get('published_on') or 0)
        parts = [main.get('text') or ''] + [t['text'] for t in continuations]
        main['text'] = '\n\n'.join(part for part in parts if part)

    return {'thread': main, 'replies': replies}


def post_url(thread: Dict[str, Any]) -> Optional[str]:
    """Canonical permalink for a parsed thread, when it has enough to build one."""
    username = thread.get('username')
    code = thread.get('code')
    if username and code:
        return f"https://www.threads.net/@{username}/post/{code}"
    return None
