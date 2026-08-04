"""
Which comments reach an agent, and in what order.

The point of the weighted draw is that a comment the crowd reacted to spreads
further than one nobody voted on, without ever making an unrated comment
unreachable. Both halves matter: drop the weighting and the feed is a coin flip,
drop the base weight and a new comment can never get its first like, so nothing
ever climbs.
"""

import os
import random
import sys

import pytest

# scripts/ is not a package - the runner puts it on sys.path the same way.
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts')
)

from comment_feed import (  # noqa: E402
    DEFAULT_BASE_WEIGHT,
    DEFAULT_COMMENTS_PER_POST,
    comment_score,
    comment_weight,
    read_feed_config,
    sample_comments,
)


def make_comment(comment_id: int, likes: int = 0, dislikes: int = 0):
    return {
        'comment_id': comment_id,
        'post_id': 1,
        'user_id': comment_id,
        'content': f'comment {comment_id}',
        'created_at': 0,
        'num_likes': likes,
        'num_dislikes': dislikes,
    }


def test_score_reads_both_comment_shapes():
    """OASIS emits a pre-computed score, or the raw counters, per platform."""
    assert comment_score(make_comment(1, likes=5, dislikes=2)) == 3
    assert comment_score({'score': 7}) == 7
    assert comment_score({}) == 0


def test_weight_floors_at_base_for_disliked_comments():
    """A downvoted comment is as reachable as a new one, never less."""
    assert comment_weight(make_comment(1), DEFAULT_BASE_WEIGHT) == 1.0
    assert comment_weight(make_comment(2, likes=4), DEFAULT_BASE_WEIGHT) == 5.0
    assert comment_weight(make_comment(3, dislikes=9), DEFAULT_BASE_WEIGHT) == 1.0


def test_short_threads_are_shown_whole():
    comments = [make_comment(1), make_comment(2)]
    assert len(sample_comments(comments, comments_per_post=3)) == 2


def test_draw_is_capped_and_ordered_by_reaction():
    comments = [make_comment(i, likes=i) for i in range(1, 11)]
    seen = sample_comments(comments, comments_per_post=3, rng=random.Random(7))

    assert len(seen) == 3
    scores = [comment_score(c) for c in seen]
    assert scores == sorted(scores, reverse=True)


def test_zero_means_show_everything():
    """0 restores OASIS's own behaviour, for a run that wants the full thread."""
    comments = [make_comment(i) for i in range(20)]
    assert len(sample_comments(comments, comments_per_post=0)) == 20


def test_reactions_raise_the_odds_of_being_seen():
    """The whole reason the draw is weighted rather than uniform."""
    popular = make_comment(1, likes=19)  # weight 20
    quiet = [make_comment(i) for i in range(2, 12)]  # weight 1 each
    rng = random.Random(1234)

    hits = sum(
        1
        for _ in range(2000)
        if any(
            c['comment_id'] == 1
            for c in sample_comments([popular] + quiet, comments_per_post=3, rng=rng)
        )
    )

    # Uniform picking would land near 3/11 ~= 27%. Weighted, it is far higher.
    assert hits / 2000 > 0.6


def test_unrated_comments_still_get_through():
    """No comment is starved out, or a thread could never seed its first like."""
    popular = make_comment(1, likes=200)
    newcomer = make_comment(2)
    rng = random.Random(99)

    hits = sum(
        1
        for _ in range(500)
        if any(
            c['comment_id'] == 2
            for c in sample_comments(
                [popular, newcomer, make_comment(3, likes=50)],
                comments_per_post=1,
                rng=rng,
            )
        )
    )

    assert hits > 0


@pytest.mark.parametrize(
    'raw, expected',
    [
        ({}, DEFAULT_COMMENTS_PER_POST),
        ({'feed_config': {}}, DEFAULT_COMMENTS_PER_POST),
        ({'feed_config': {'comments_per_post': 5}}, 5),
        ({'feed_config': {'comments_per_post': '5'}}, 5),
        ({'feed_config': {'comments_per_post': 'nonsense'}}, DEFAULT_COMMENTS_PER_POST),
        ({'feed_config': {'comments_per_post': -2}}, 0),
    ],
)
def test_config_reading_survives_older_config_files(raw, expected):
    """Configs written before feed_config existed must still run."""
    assert read_feed_config(raw)['comments_per_post'] == expected


def test_base_weight_never_reads_as_zero():
    """A zero base would make an unrated comment permanently invisible."""
    config = read_feed_config({'feed_config': {'comment_weight_base': 0}})
    assert config['comment_weight_base'] == DEFAULT_BASE_WEIGHT
