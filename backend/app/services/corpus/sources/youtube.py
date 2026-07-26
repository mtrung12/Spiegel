"""
YouTube comments via the official Data API v3.

Ad and review videos attract exactly the reaction this project tries to
predict: unprompted opinion about a product's marketing, written by people who
just watched it. That makes YouTube the closest real-world analogue to the
simulated feed.

Requires a free API key from the Google Cloud console. The default daily quota
is 10,000 units and a search costs 100 of them, so the adapter tracks quota
explicitly and spends it on comments rather than on repeated searches.
"""

from typing import Any, Dict, List, Optional, Tuple

from ....config import Config
from ....utils.logger import get_logger
from ..models import KIND_COMMENT, KIND_POST, CorpusItem, FetchResult, SourceQuery
from .base import SourceAdapter

logger = get_logger('mirofish.corpus.youtube')

API_BASE = 'https://www.googleapis.com/youtube/v3'

# Documented quota costs, used to stop before Google does.
COST_SEARCH = 100
COST_COMMENT_THREADS = 1
COST_VIDEOS = 1

# Relevance floor for comments under a video the operator named explicitly.
VIDEO_PROVENANCE = 0.4


class YouTubeSource(SourceAdapter):
    """Fetch video metadata and top-level comments."""

    name = 'youtube'
    access_note = (
        'Official YouTube Data API v3. Requires YOUTUBE_API_KEY. '
        'Free tier is 10,000 quota units per day; each search costs 100.'
    )
    requires_credentials = True

    def __init__(self, client=None):
        super().__init__(client)
        self.quota_spent = 0

    def is_available(self) -> Tuple[bool, str]:
        if not Config.YOUTUBE_API_KEY:
            return False, 'YOUTUBE_API_KEY not configured'
        return True, ''

    def _get(self, path: str, params: Dict[str, Any], cost: int) -> Dict[str, Any]:
        """Call the API and record the quota spend."""
        params = dict(params)
        params['key'] = Config.YOUTUBE_API_KEY
        payload = self.client.get_json(f"{API_BASE}/{path}", params=params)
        self.quota_spent += cost
        return payload

    def fetch(self, query: SourceQuery) -> FetchResult:
        """
        Resolve videos for the query terms, then pull their comment threads.

        ``query.video_ids`` skips the search entirely, which saves 100 quota
        units per run and is the right call for a recurring harvest.
        """
        available, reason = self.is_available()
        if not available:
            return FetchResult(source=self.name, errors=[reason])

        errors: List[str] = []
        items: List[CorpusItem] = []
        requests_made = 0

        # Videos named by the operator are on-topic by construction, so their
        # comments should not have to re-prove it in their own words. Videos
        # found by keyword search get no such credit.
        provenance = VIDEO_PROVENANCE if query.video_ids else 0.0

        video_ids = list(query.video_ids)
        if not video_ids:
            found, made, error = self._search_videos(query)
            video_ids.extend(found)
            requests_made += made
            if error:
                errors.append(error)

        if not video_ids:
            return self._finish(items, requests_made, errors or ['no videos matched'], query)

        # Emit the videos themselves. Their titles and descriptions are usually
        # the only topical text in the thread, and the comments below inherit
        # relevance from them.
        video_items, made, error = self._video_items(video_ids)
        items.extend(video_items)
        requests_made += made
        if error:
            errors.append(error)

        for video_id in video_ids:
            if len(items) >= query.max_items:
                break
            if self.quota_spent >= Config.YOUTUBE_QUOTA_BUDGET:
                errors.append('youtube quota budget exhausted')
                break
            comments, made, error = self._comments(video_id, query)
            items.extend(comments)
            requests_made += made
            if error:
                errors.append(error)

        if provenance:
            for item in items:
                item.provenance_relevance = provenance

        return self._finish(items, requests_made, errors, query)

    def _search_videos(self, query: SourceQuery) -> Tuple[List[str], int, Optional[str]]:
        """One search call, returning up to 50 video ids."""
        cutoff = self._cutoff(query)
        params: Dict[str, Any] = {
            'part': 'id',
            'q': ' '.join(query.terms),
            'type': 'video',
            'maxResults': 50,
            'order': 'relevance',
        }
        if cutoff:
            params['publishedAfter'] = cutoff.strftime('%Y-%m-%dT%H:%M:%SZ')
        if query.lang:
            params['relevanceLanguage'] = query.lang

        try:
            payload = self._get('search', params, COST_SEARCH)
        except Exception as exc:
            return [], 1, f"youtube search: {exc}"

        ids = [
            item['id']['videoId']
            for item in payload.get('items', [])
            if (item.get('id') or {}).get('videoId')
        ]
        return ids, 1, None

    def _video_items(self, video_ids: List[str]) -> Tuple[List[CorpusItem], int, Optional[str]]:
        """Fetch titles and descriptions for up to 50 videos per call."""
        items: List[CorpusItem] = []
        requests_made = 0

        for offset in range(0, len(video_ids), 50):
            batch = video_ids[offset:offset + 50]
            try:
                payload = self._get(
                    'videos',
                    {'part': 'snippet,statistics', 'id': ','.join(batch)},
                    COST_VIDEOS,
                )
                requests_made += 1
            except Exception as exc:
                return items, requests_made, f"youtube videos: {exc}"

            for entry in payload.get('items', []):
                snippet = entry.get('snippet') or {}
                stats = entry.get('statistics') or {}
                video_id = entry.get('id')
                if not video_id:
                    continue
                items.append(CorpusItem(
                    item_id=f"video_{video_id}",
                    source=self.name,
                    kind=KIND_POST,
                    text=snippet.get('description') or '',
                    title=snippet.get('title'),
                    url=f"https://www.youtube.com/watch?v={video_id}",
                    author_pseudonym=self._pseudonym(snippet.get('channelId')),
                    created_at=snippet.get('publishedAt'),
                    channel=snippet.get('channelTitle'),
                    score=int(stats.get('likeCount') or 0),
                    reply_count=int(stats.get('commentCount') or 0),
                    lang=snippet.get('defaultAudioLanguage'),
                    meta={'video_id': video_id, 'view_count': stats.get('viewCount')},
                ))

        return items, requests_made, None

    def _comments(
        self,
        video_id: str,
        query: SourceQuery,
    ) -> Tuple[List[CorpusItem], int, Optional[str]]:
        """Page through one video's top-level comments."""
        items: List[CorpusItem] = []
        requests_made = 0
        page_token: Optional[str] = None
        cutoff = self._cutoff(query)

        # A synthetic parent so comments have something to inherit relevance
        # from - the video title is often the only topical text in the thread.
        parent_id = f"video_{video_id}"

        while len(items) < query.max_comments_per_post:
            params: Dict[str, Any] = {
                'part': 'snippet',
                'videoId': video_id,
                'maxResults': min(100, query.max_comments_per_post),
                'order': 'relevance',
                'textFormat': 'plainText',
            }
            if page_token:
                params['pageToken'] = page_token

            try:
                payload = self._get('commentThreads', params, COST_COMMENT_THREADS)
                requests_made += 1
            except Exception as exc:
                # Disabled comments are normal, not an error worth surfacing
                message = str(exc)
                if 'commentsDisabled' in message or '403' in message:
                    return items, requests_made, None
                return items, requests_made, f"youtube comments {video_id}: {exc}"

            for thread in payload.get('items', []):
                item = self._to_comment(thread, video_id, parent_id)
                if item is None or self._too_old(item, cutoff):
                    continue
                items.append(item)

            page_token = payload.get('nextPageToken')
            if not page_token:
                break

        return items, requests_made, None

    def _to_comment(
        self,
        thread: Dict[str, Any],
        video_id: str,
        parent_id: str,
    ) -> Optional[CorpusItem]:
        snippet = (
            ((thread.get('snippet') or {}).get('topLevelComment') or {}).get('snippet') or {}
        )
        comment_id = ((thread.get('snippet') or {}).get('topLevelComment') or {}).get('id')
        body = snippet.get('textOriginal') or snippet.get('textDisplay') or ''
        if not comment_id or not body.strip():
            return None

        return CorpusItem(
            item_id=comment_id,
            source=self.name,
            kind=KIND_COMMENT,
            text=body,
            url=f"https://www.youtube.com/watch?v={video_id}&lc={comment_id}",
            author_pseudonym=self._pseudonym(snippet.get('authorChannelId', {}).get('value')),
            created_at=snippet.get('publishedAt'),
            parent_id=parent_id,
            channel=f"video:{video_id}",
            score=int(snippet.get('likeCount') or 0),
            reply_count=int((thread.get('snippet') or {}).get('totalReplyCount') or 0),
            meta={'video_id': video_id},
        )
