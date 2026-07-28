"""
Content sentiment digest.

The counted KPIs in ``campaign_metrics`` read sentiment off the action type -
a like is approval, a dislike is rejection. That says nothing about what the
audience actually wrote. This module classifies the text of every post and
comment the agents authored, so the campaign can be read as:

    47% positive / 21% neutral / 32% negative

plus the single loudest post on each side, the objections that keep recurring
among the negatives, and the hooks that keep recurring among the positives.

Classification is a batched LLM pass over the simulation SQLite databases. The
same pass emits a short theme tag per item, so objections and hooks stay
grounded in the text they came from rather than being re-summarised later.

The result is cached to ``sentiment_digest.json`` in the simulation directory
and reused until the feed grows.
"""

import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ..config import Config
from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger
from ..utils.openai_chat_compat import is_gpt5_family
from ..utils.pipeline_logger import llm_caller
from .simulation_runner import SimulationRunner

logger = get_logger('mirofish.content_sentiment')


# ═══════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════

PLATFORMS = ('twitter', 'reddit')

SENTIMENTS = ('positive', 'neutral', 'negative')

# Short codes keep the model's reply small; a batch of 25 fits comfortably
# inside one response even with a theme tag per item.
_CODE_TO_SENTIMENT = {
    'pos': 'positive',
    'neu': 'neutral',
    'neg': 'negative',
    'positive': 'positive',
    'neutral': 'neutral',
    'negative': 'negative',
}

# Cache key components. A digest is reused only when the feed size and the
# classifying model both still match.
CACHE_FILENAME = 'sentiment_digest.json'


def _rate(numerator: float, denominator: float) -> float:
    """Percentage, guarded against division by zero."""
    if not denominator:
        return 0.0
    return round(numerator / denominator * 100, 1)


def _normalize_theme(tag: str) -> str:
    """
    Fold a theme tag to a comparison key.

    Merges "Price too high", "price too high." and "prices too high" onto one
    key so the ranking counts them once. Deliberately conservative - it will
    not merge differently worded complaints, only differently typed ones.
    """
    key = tag.lower().strip()
    key = re.sub(r'[^a-z0-9\s]', '', key)
    key = re.sub(r'\s+', ' ', key).strip()
    # Crude singular fold, applied per word: "prices" -> "price".
    words = []
    for word in key.split():
        if len(word) > 3 and word.endswith('s') and not word.endswith('ss'):
            word = word[:-1]
        words.append(word)
    return ' '.join(words)


# ═══════════════════════════════════════════════════════════════
# Data model
# ═══════════════════════════════════════════════════════════════

@dataclass
class ContentItem:
    """One authored post or comment, before classification."""
    uid: str                    # "twitter:p:12"
    platform: str
    kind: str                   # "post" or "comment"
    item_id: int
    post_id: Optional[int]      # the parent post, for a comment
    author_id: int
    author_name: str
    content: str
    likes: int
    dislikes: int
    created_at: Optional[str]

    # Filled in by the classification pass
    sentiment: str = 'neutral'
    theme: str = ''

    def to_dict(self) -> Dict[str, Any]:
        return {
            "uid": self.uid,
            "platform": self.platform,
            "kind": self.kind,
            "item_id": self.item_id,
            "post_id": self.post_id,
            "author_id": self.author_id,
            "author_name": self.author_name,
            "content": self.content,
            "likes": self.likes,
            "dislikes": self.dislikes,
            "created_at": self.created_at,
            "sentiment": self.sentiment,
            "theme": self.theme,
        }


@dataclass
class SentimentSplit:
    """Positive / neutral / negative counts for one content kind."""
    positive: int = 0
    neutral: int = 0
    negative: int = 0

    @property
    def total(self) -> int:
        return self.positive + self.neutral + self.negative

    def add(self, sentiment: str) -> None:
        if sentiment == 'positive':
            self.positive += 1
        elif sentiment == 'negative':
            self.negative += 1
        else:
            self.neutral += 1

    def to_dict(self) -> Dict[str, Any]:
        total = self.total
        return {
            "total": total,
            "positive": self.positive,
            "neutral": self.neutral,
            "negative": self.negative,
            "positive_pct": _rate(self.positive, total),
            "neutral_pct": _rate(self.neutral, total),
            "negative_pct": _rate(self.negative, total),
            # Single headline number, -1.0 (all negative) to 1.0 (all positive)
            "net_sentiment": round((self.positive - self.negative) / total, 3) if total else 0.0,
        }


@dataclass
class ThemeGroup:
    """One recurring objection or hook."""
    theme: str
    count: int = 0
    likes: int = 0
    examples: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self, denominator: int) -> Dict[str, Any]:
        return {
            "theme": self.theme,
            "count": self.count,
            "share_pct": _rate(self.count, denominator),
            "total_likes": self.likes,
            "examples": self.examples,
        }


# ═══════════════════════════════════════════════════════════════
# Service
# ═══════════════════════════════════════════════════════════════

class ContentSentimentService:
    """Classify authored content and aggregate it into a readable digest."""

    BATCH_SIZE = 25
    # Long posts are truncated for classification only; the stored excerpt and
    # the highlight cards keep more text.
    CLASSIFY_CHAR_LIMIT = 600
    EXCERPT_CHAR_LIMIT = 400
    MAX_THEMES = 8
    MAX_THEME_EXAMPLES = 3

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self._llm = llm_client

    @property
    def llm(self) -> LLMClient:
        if self._llm is None:
            self._llm = LLMClient()
        return self._llm

    # ── Reading the feed ────────────────────────────────────────

    @classmethod
    def _sim_dir(cls, simulation_id: str) -> str:
        return os.path.join(SimulationRunner.RUN_STATE_DIR, simulation_id)

    @classmethod
    def _seed_contents(cls, simulation_id: str) -> set:
        """
        The campaign creative itself, seeded into the feed before round 0.

        These are the brand's own words, not audience reaction, so they are
        excluded from the sentiment split. Matched by exact content because
        that is what the runner writes into the platform.
        """
        config_path = os.path.join(cls._sim_dir(simulation_id), "simulation_config.json")
        if not os.path.exists(config_path):
            return set()
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"读取模拟配置失败，种子帖子将不被排除: {e}")
            return set()

        seeds = set()
        for post in config.get("event_config", {}).get("initial_posts", []):
            content = (post.get("content") or "").strip()
            if content:
                seeds.add(content)
        return seeds

    @classmethod
    def _read_platform(cls, simulation_id: str, platform: str) -> Tuple[List[ContentItem], List[ContentItem]]:
        """Read the authored posts and comments out of one platform database."""
        db_path = os.path.join(cls._sim_dir(simulation_id), f"{platform}_simulation.db")
        if not os.path.exists(db_path):
            return [], []

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        posts: List[ContentItem] = []
        comments: List[ContentItem] = []

        try:
            cursor.execute("""
                SELECT p.post_id, p.user_id, p.content, p.created_at,
                       p.num_likes, p.num_dislikes,
                       u.name AS author_name, u.user_name AS author_user_name
                FROM post p
                LEFT JOIN user u ON u.user_id = p.user_id
            """)
            for row in cursor.fetchall():
                content = (row["content"] or "").strip()
                if not content:
                    continue
                posts.append(ContentItem(
                    uid=f"{platform}:p:{row['post_id']}",
                    platform=platform,
                    kind="post",
                    item_id=row["post_id"],
                    post_id=row["post_id"],
                    author_id=row["user_id"],
                    author_name=row["author_name"] or row["author_user_name"] or f"agent_{row['user_id']}",
                    content=content,
                    likes=row["num_likes"] or 0,
                    dislikes=row["num_dislikes"] or 0,
                    created_at=row["created_at"],
                ))
        except sqlite3.OperationalError as e:
            logger.warning(f"{platform} 帖子表读取失败: {e}")

        try:
            cursor.execute("""
                SELECT c.comment_id, c.post_id, c.user_id, c.content, c.created_at,
                       c.num_likes, c.num_dislikes,
                       u.name AS author_name, u.user_name AS author_user_name
                FROM comment c
                LEFT JOIN user u ON u.user_id = c.user_id
            """)
            for row in cursor.fetchall():
                content = (row["content"] or "").strip()
                if not content:
                    continue
                comments.append(ContentItem(
                    uid=f"{platform}:c:{row['comment_id']}",
                    platform=platform,
                    kind="comment",
                    item_id=row["comment_id"],
                    post_id=row["post_id"],
                    author_id=row["user_id"],
                    author_name=row["author_name"] or row["author_user_name"] or f"agent_{row['user_id']}",
                    content=content,
                    likes=row["num_likes"] or 0,
                    dislikes=row["num_dislikes"] or 0,
                    created_at=row["created_at"],
                ))
        except sqlite3.OperationalError as e:
            logger.warning(f"{platform} 评论表读取失败: {e}")

        conn.close()
        return posts, comments

    @classmethod
    def _collect(cls, simulation_id: str) -> Tuple[List[ContentItem], List[ContentItem]]:
        """Every authored item across both platforms, with the seed posts removed."""
        seeds = cls._seed_contents(simulation_id)

        all_posts: List[ContentItem] = []
        all_comments: List[ContentItem] = []
        seed_items: List[ContentItem] = []

        for platform in PLATFORMS:
            posts, comments = cls._read_platform(simulation_id, platform)
            for post in posts:
                if post.content in seeds:
                    seed_items.append(post)
                else:
                    all_posts.append(post)
            all_comments.extend(comments)

        return all_posts + all_comments, seed_items

    # ── Classification ──────────────────────────────────────────

    def _classify_batch(
        self,
        batch: List[ContentItem],
        campaign_context: str,
        known_themes: Optional[List[str]] = None,
    ) -> None:
        """Label one batch in place. A failed batch is left neutral, not dropped."""
        lines = []
        for idx, item in enumerate(batch):
            text = item.content[:self.CLASSIFY_CHAR_LIMIT].replace('\n', ' ')
            lines.append(f'[{idx}] ({item.kind}) {text}')
        numbered = '\n'.join(lines)

        # Themes only rank if the same complaint gets the same tag in every
        # batch. Without the running vocabulary the model re-invents wording
        # per batch and a recurring objection splits into several 1x entries.
        vocabulary = ""
        if known_themes:
            listed = '\n'.join(f'- {t}' for t in known_themes)
            vocabulary = (
                "\n## Theme tags already in use\n"
                "Reuse one of these VERBATIM whenever it fits. Only invent a new "
                "tag when none of them describes the item.\n"
                f"{listed}\n"
            )

        prompt = f"""{campaign_context}{vocabulary}

Below are {len(batch)} pieces of content written by members of the target
audience after they saw the campaign. Classify each one.

{numbered}

For every item return:
- "s": sentiment TOWARD THE CAMPAIGN OR BRAND - "pos", "neu" or "neg".
  Judge the attitude the writer expresses, not whether the wording is polite.
  Sarcasm counts as negative. An item that only reports a fact, asks a neutral
  question, or is off-topic is "neu".
- "tag": the single dominant theme, 2-4 words, lower case.
  For a negative item this is the OBJECTION ("price too high", "claim not
  credible", "shipping too slow").
  For a positive item this is the HOOK - what won them over ("packaging
  design", "founder story", "value for money").
  For a neutral item give the topic.
  Reuse the exact same wording whenever two items share a theme, so they can be
  counted together.

Return JSON only:
{{"items": [{{"i": 0, "s": "neg", "tag": "price too high"}}]}}

Every index from 0 to {len(batch) - 1} must appear exactly once."""

        # A GPT-5 model spends part of its budget on reasoning tokens before it
        # writes anything, so the cap that comfortably fits 25 labels on a
        # classic chat model can leave it with no room for the answer.
        max_tokens = 8192 if is_gpt5_family(Config.LLM_MODEL_NAME) else 2048

        try:
            with llm_caller('ContentSentiment', f"batch of {len(batch)}"):
                result = self.llm.chat_json(
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are a market research analyst coding open-ended survey "
                                "responses. Return pure JSON. Be consistent: identical themes "
                                "must get identical tags."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.0,
                    max_tokens=max_tokens,
                    max_attempts=2,
                )
        except Exception as e:
            logger.warning(f"情感分类批次失败，该批次记为中性: {type(e).__name__}: {str(e)[:120]}")
            return

        for entry in result.get("items", []) or []:
            if not isinstance(entry, dict):
                continue
            try:
                idx = int(entry.get("i"))
            except (TypeError, ValueError):
                continue
            if not 0 <= idx < len(batch):
                continue

            code = str(entry.get("s", "neu")).strip().lower()
            batch[idx].sentiment = _CODE_TO_SENTIMENT.get(code, 'neutral')

            tag = str(entry.get("tag", "") or "").strip()
            batch[idx].theme = tag[:60]

    # How many previously used tags to show the next batch. Enough to cover the
    # recurring themes without turning the prompt into a taxonomy dump.
    MAX_CARRIED_THEMES = 25

    def _classify(self, items: List[ContentItem], campaign_context: str) -> None:
        """
        Label every item, one batch at a time.

        Each batch is shown the tags earlier batches produced, so a complaint
        that recurs across the feed accumulates under one theme instead of
        splitting into near-duplicates.
        """
        total_batches = (len(items) + self.BATCH_SIZE - 1) // self.BATCH_SIZE
        theme_counts: Dict[str, int] = {}

        for batch_idx in range(total_batches):
            start = batch_idx * self.BATCH_SIZE
            batch = items[start:start + self.BATCH_SIZE]
            logger.info(f"分类批次 {batch_idx + 1}/{total_batches} ({len(batch)} 条)")

            carried = sorted(theme_counts, key=theme_counts.get, reverse=True)
            self._classify_batch(
                batch, campaign_context, carried[:self.MAX_CARRIED_THEMES]
            )

            for item in batch:
                if item.theme:
                    theme_counts[item.theme] = theme_counts.get(item.theme, 0) + 1

    # ── Aggregation ─────────────────────────────────────────────

    def _excerpt(self, item: ContentItem) -> Dict[str, Any]:
        """A highlight card: enough to read, not the whole thread."""
        return {
            "uid": item.uid,
            "platform": item.platform,
            "kind": item.kind,
            "item_id": item.item_id,
            "post_id": item.post_id,
            "author_id": item.author_id,
            "author_name": item.author_name,
            "content": item.content[:self.EXCERPT_CHAR_LIMIT],
            "truncated": len(item.content) > self.EXCERPT_CHAR_LIMIT,
            "likes": item.likes,
            "dislikes": item.dislikes,
            "sentiment": item.sentiment,
            "theme": item.theme,
        }

    def _rank_themes(self, items: List[ContentItem]) -> List[Dict[str, Any]]:
        """Group items by normalised theme and rank by how often it recurs."""
        groups: Dict[str, ThemeGroup] = {}
        display_counts: Dict[str, Dict[str, int]] = {}

        for item in items:
            if not item.theme:
                continue
            key = _normalize_theme(item.theme)
            if not key:
                continue

            group = groups.setdefault(key, ThemeGroup(theme=item.theme))
            group.count += 1
            group.likes += item.likes

            # Keep the most frequently used spelling as the display label.
            variants = display_counts.setdefault(key, {})
            variants[item.theme] = variants.get(item.theme, 0) + 1

            group.examples.append(self._excerpt(item))

        ranked = sorted(groups.values(), key=lambda g: (g.count, g.likes), reverse=True)
        ranked = ranked[:self.MAX_THEMES]

        out = []
        for group in ranked:
            key = _normalize_theme(group.theme)
            variants = display_counts.get(key, {})
            if variants:
                group.theme = max(variants.items(), key=lambda kv: kv[1])[0]
            # Loudest examples first, so the card shows the ones that landed.
            group.examples.sort(key=lambda e: e["likes"], reverse=True)
            group.examples = group.examples[:self.MAX_THEME_EXAMPLES]
            out.append(group.to_dict(len(items)))
        return out

    @staticmethod
    def _loudest(items: List[ContentItem], kind: str, sentiment: str) -> Optional[ContentItem]:
        """The most-liked item of one kind and sentiment."""
        pool = [i for i in items if i.kind == kind and i.sentiment == sentiment]
        if not pool:
            return None
        return max(pool, key=lambda i: (i.likes, -i.dislikes))

    # ── Entry point ─────────────────────────────────────────────

    @classmethod
    def _cache_path(cls, simulation_id: str) -> str:
        return os.path.join(cls._sim_dir(simulation_id), CACHE_FILENAME)

    @classmethod
    def _load_cache(cls, simulation_id: str, fingerprint: str) -> Optional[Dict[str, Any]]:
        path = cls._cache_path(simulation_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path, 'r', encoding='utf-8') as f:
                cached = json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
        if cached.get("fingerprint") != fingerprint:
            return None
        cached["cached"] = True
        return cached

    @classmethod
    def _save_cache(cls, simulation_id: str, digest: Dict[str, Any]) -> None:
        path = cls._cache_path(simulation_id)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(digest, f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.warning(f"情感摘要缓存写入失败: {e}")

    def compute(
        self,
        simulation_id: str,
        campaign_requirement: str = "",
        force: bool = False,
    ) -> Dict[str, Any]:
        """
        Build the sentiment digest for one simulation.

        Args:
            simulation_id: Simulation ID
            campaign_requirement: Campaign background, so the model judges
                sentiment toward this campaign rather than in the abstract
            force: Reclassify even when a matching cached digest exists

        Returns:
            The digest, with zeroed counters when nothing has been authored yet
        """
        items, seed_items = self._collect(simulation_id)

        posts = [i for i in items if i.kind == "post"]
        comments = [i for i in items if i.kind == "comment"]
        fingerprint = f"{len(posts)}:{len(comments)}:{Config.LLM_MODEL_NAME}"

        if not items:
            return {
                "simulation_id": simulation_id,
                "generated_at": datetime.now().isoformat(),
                "fingerprint": fingerprint,
                "cached": False,
                "model": Config.LLM_MODEL_NAME,
                "totals": {
                    "posts": 0, "comments": 0, "classified": 0,
                    "seed_posts_excluded": len(seed_items),
                },
                "posts": SentimentSplit().to_dict(),
                "comments": SentimentSplit().to_dict(),
                "overall": SentimentSplit().to_dict(),
                "highlights": {},
                "problems": [],
                "quirks": [],
                "seed_posts": [self._excerpt(s) for s in seed_items],
            }

        if not force:
            cached = self._load_cache(simulation_id, fingerprint)
            if cached:
                logger.info(f"命中情感摘要缓存: {simulation_id}")
                return cached

        context = (
            f"## Campaign under test\n{campaign_requirement.strip()}\n"
            if campaign_requirement.strip()
            else "## Campaign under test\n(no brief supplied - judge sentiment toward the product or brand being discussed)\n"
        )

        logger.info(f"开始分类 {len(items)} 条内容 ({len(posts)} 帖 / {len(comments)} 评论)")
        self._classify(items, context)

        post_split = SentimentSplit()
        comment_split = SentimentSplit()
        overall_split = SentimentSplit()
        for item in items:
            overall_split.add(item.sentiment)
            (post_split if item.kind == "post" else comment_split).add(item.sentiment)

        negatives = [i for i in items if i.sentiment == 'negative']
        positives = [i for i in items if i.sentiment == 'positive']

        highlights = {}
        for kind in ("post", "comment"):
            for sentiment in ("positive", "negative"):
                loudest = self._loudest(items, kind, sentiment)
                if loudest:
                    highlights[f"top_{sentiment}_{kind}"] = self._excerpt(loudest)

        digest = {
            "simulation_id": simulation_id,
            "generated_at": datetime.now().isoformat(),
            "fingerprint": fingerprint,
            "cached": False,
            "model": Config.LLM_MODEL_NAME,
            "totals": {
                "posts": len(posts),
                "comments": len(comments),
                "classified": len(items),
                "seed_posts_excluded": len(seed_items),
            },
            "posts": post_split.to_dict(),
            "comments": comment_split.to_dict(),
            "overall": overall_split.to_dict(),
            "highlights": highlights,
            # What keeps going wrong, and what keeps landing.
            "problems": self._rank_themes(negatives),
            "quirks": self._rank_themes(positives),
            "seed_posts": [self._excerpt(s) for s in seed_items],
        }

        self._save_cache(simulation_id, digest)
        return digest
