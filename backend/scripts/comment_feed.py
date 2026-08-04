"""
What an agent actually reads under a post.

OASIS attaches *every* comment on a post to the feed it hands an agent. Two
things are wrong with that. The prompt grows without bound as a thread heats up,
and a comment's own reactions do not change its odds of being read - a reply
nobody upvoted arrives with the same weight as the one the crowd pushed to the
top, which is not how any real feed behaves.

This module wraps ``PlatformUtils._add_comments_to_posts`` and keeps
``comments_per_post`` comments per post, drawn without replacement with

    weight = base_weight + max(0, likes - dislikes)

so a well-received comment is the one most likely to reach the next agent, and a
fresh comment with no votes yet still carries ``base_weight`` worth of a chance
rather than being invisible until someone else finds it first.

The draw is Efraimidis-Spirakis: give each comment the key ``u ** (1 / weight)``
for a uniform ``u``, keep the top k. That is a weighted sample without
replacement in one pass, no rejection loop, and it degrades sanely when every
weight is equal (a plain uniform sample).

Wrapping rather than replacing keeps OASIS's own post/repost/quote assembly -
this only ever trims the ``comments`` list the original built.
"""

import random
from typing import Any, Dict, List, Optional, Sequence

# Comments an agent sees per post. 0 means "no limit", i.e. OASIS's own
# behaviour of showing every comment.
DEFAULT_COMMENTS_PER_POST = 3

# Weight floor, so a comment with no reactions yet can still be drawn. At 1.0 a
# comment on +4 is five times as likely to be seen as an unrated one.
DEFAULT_BASE_WEIGHT = 1.0


def comment_score(comment: Dict[str, Any]) -> int:
    """Net reaction on a comment.

    OASIS emits either a pre-computed ``score`` (when the platform is built with
    show_score) or the raw like/dislike counters, so both shapes are read here.
    """
    if 'score' in comment:
        try:
            return int(comment.get('score') or 0)
        except (TypeError, ValueError):
            return 0
    try:
        likes = int(comment.get('num_likes') or 0)
    except (TypeError, ValueError):
        likes = 0
    try:
        dislikes = int(comment.get('num_dislikes') or 0)
    except (TypeError, ValueError):
        dislikes = 0
    return likes - dislikes


def comment_weight(
    comment: Dict[str, Any],
    base_weight: float = DEFAULT_BASE_WEIGHT,
) -> float:
    """Draw weight for one comment: the base plus its net reaction.

    A downvoted comment floors at the base weight rather than going negative -
    being disliked should not make a comment *more* invisible than a brand new
    one, only less visible than a liked one.
    """
    base = base_weight if base_weight > 0 else DEFAULT_BASE_WEIGHT
    return base + max(0, comment_score(comment))


def sample_comments(
    comments: Sequence[Dict[str, Any]],
    comments_per_post: int = DEFAULT_COMMENTS_PER_POST,
    base_weight: float = DEFAULT_BASE_WEIGHT,
    rng: Optional[random.Random] = None,
) -> List[Dict[str, Any]]:
    """Pick the comments one agent sees under one post.

    Returns them ordered by net reaction, highest first, which is the order a
    reader meets them in on either platform.
    """
    picked = list(comments or [])

    if comments_per_post > 0 and len(picked) > comments_per_post:
        draw = rng or random
        # Efraimidis-Spirakis keys. random() never returns exactly 0.0, so the
        # log/pow is always defined.
        keyed = [
            (draw.random() ** (1.0 / comment_weight(c, base_weight)), c)
            for c in picked
        ]
        keyed.sort(key=lambda item: item[0], reverse=True)
        picked = [c for _, c in keyed[:comments_per_post]]

    picked.sort(key=comment_score, reverse=True)
    return picked


def install(
    comments_per_post: int = DEFAULT_COMMENTS_PER_POST,
    base_weight: float = DEFAULT_BASE_WEIGHT,
    rng: Optional[random.Random] = None,
) -> bool:
    """Patch OASIS so every feed refresh goes through the weighted draw.

    Both platform simulations share one process, so a single install covers
    Twitter and Reddit. Idempotent: a second call is a no-op.

    Returns True if the patch was applied, False if it was already in place.
    """
    from oasis.social_platform.platform_utils import PlatformUtils

    original = PlatformUtils._add_comments_to_posts
    if getattr(original, '_comment_feed_patched', False):
        return False

    def _add_comments_to_posts(self, posts_results):
        posts = original(self, posts_results)
        for post in posts:
            all_comments = post.get('comments') or []
            visible = sample_comments(
                all_comments, comments_per_post, base_weight, rng
            )
            post['comments'] = visible
            if len(all_comments) > len(visible):
                # Tell the agent the thread is bigger than what it was shown,
                # so "everyone is talking about this" stays legible even when
                # only three replies made it into the prompt.
                post['total_comments'] = len(all_comments)
        return posts

    _add_comments_to_posts._comment_feed_patched = True
    _add_comments_to_posts._comment_feed_original = original
    PlatformUtils._add_comments_to_posts = _add_comments_to_posts
    return True


def uninstall() -> bool:
    """Undo :func:`install`. Only used by tests."""
    from oasis.social_platform.platform_utils import PlatformUtils

    current = PlatformUtils._add_comments_to_posts
    original = getattr(current, '_comment_feed_original', None)
    if original is None:
        return False
    PlatformUtils._add_comments_to_posts = original
    return True


def read_feed_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Pull the feed knobs out of simulation_config.json.

    Configs written before feed_config existed simply get the defaults.
    """
    feed_config = config.get('feed_config') or {}
    try:
        comments_per_post = int(
            feed_config.get('comments_per_post', DEFAULT_COMMENTS_PER_POST)
        )
    except (TypeError, ValueError):
        comments_per_post = DEFAULT_COMMENTS_PER_POST
    try:
        base_weight = float(
            feed_config.get('comment_weight_base', DEFAULT_BASE_WEIGHT)
        )
    except (TypeError, ValueError):
        base_weight = DEFAULT_BASE_WEIGHT

    return {
        'comments_per_post': max(0, comments_per_post),
        'comment_weight_base': base_weight if base_weight > 0 else DEFAULT_BASE_WEIGHT,
    }
