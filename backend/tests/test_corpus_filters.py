"""
Corpus filter behaviour.

These are the tunable thresholds in the ingestion path, so the tests pin the
decisions that matter rather than the exact scores: junk is dropped, real
objections survive, the brand blocklist cannot be evaded by respacing, and
short on-topic replies are not thrown away.
"""

from app.services.corpus import (
    CorpusFilter,
    CorpusItem,
    NoiseFilter,
    SimHashIndex,
    SourceQuery,
    deduplicate,
    text_signals,
)
from app.services.corpus.models import KIND_COMMENT, KIND_POST

TOPIC = ['meal kit delivery', 'recipe box subscription']


def _item(text, *, item_id='x', kind=KIND_COMMENT, **kwargs):
    return CorpusItem(item_id=item_id, source='test', kind=kind, text=text, **kwargs)


def _keeps(text, **kwargs):
    return NoiseFilter().judge(_item(text, **kwargs)).keep


# -- noise ------------------------------------------------------------------

def test_drops_social_noise():
    for text in ('first!', 'This', 'lol same', 'Thanks for sharing', "who's watching in 2026?"):
        assert not _keeps(text), text


def test_drops_platform_boilerplate():
    assert not _keeps('[deleted]')
    assert not _keeps(
        'I am a bot, and this action was performed automatically. '
        'Please contact the moderators of this subreddit.'
    )


def test_drops_spam():
    assert not _keeps(
        'DM me for how to recover your wallet funds, crypto expert here '
        'with 100% profit guaranteed'
    )


def test_drops_emoji_and_character_runs():
    assert not _keeps('🔥🔥🔥🔥🔥🔥🔥🔥')
    assert not _keeps('aaaaaaaaaaaaaaaaaa lmao')


def test_drops_repeated_phrases():
    """Keyword stuffing repeats bigrams; ordinary prose almost never does."""
    assert not _keeps('meal kit delivery meal kit delivery meal kit delivery recipe box recipe box')


def test_drops_contentless_filler():
    assert not _keeps('i mean yeah i guess so but like i dont know really you know what i mean')


def test_keeps_a_real_objection():
    assert _keeps(
        'Cancelled my meal kit subscription after four months. The portions '
        'shrank but the price went up 20%, and half the produce arrived wilted.'
    )


def test_bigram_diversity_signal():
    natural = text_signals('the portions shrank but the price went up and the produce arrived wilted')
    stuffed = text_signals('meal kit delivery meal kit delivery meal kit delivery meal kit')
    assert natural['bigram_ttr'] > 0.9
    assert stuffed['bigram_ttr'] < 0.55


# -- relevance --------------------------------------------------------------

def test_drops_off_topic_but_well_formed_text():
    items = [_item(
        "My landlord still hasn't fixed the boiler after three weeks of "
        "calling him every day about it.",
        item_id='off',
    )]
    kept, stats = CorpusFilter(topic_terms=TOPIC).run(items)
    assert kept == []
    assert stats.dropped_irrelevant == 1


def test_short_reply_inherits_parent_relevance():
    """
    Without inheritance a reply like this scores zero on topic terms and is
    dropped, even though it is the corroborating objection worth keeping.
    """
    post = _item(
        'Anyone else find the meal kit delivery windows impossible? Mine '
        'always shows up between 2 and 6pm when nobody is home.',
        item_id='p1', kind=KIND_POST,
    )
    reply = _item(
        'Mine did exactly the same thing, wilted every single week without fail.',
        item_id='c1', parent_id='p1',
    )
    kept, _ = CorpusFilter(topic_terms=TOPIC).run([post, reply])
    assert {i.item_id for i in kept} == {'p1', 'c1'}


def test_self_referencing_parent_cannot_bootstrap_relevance():
    orphan = _item(
        'Mine did exactly the same thing, wilted every single week without fail.',
        item_id='c1', parent_id='c1',
    )
    kept, _ = CorpusFilter(topic_terms=TOPIC).run([orphan])
    assert kept == []


def test_provenance_relevance_rescues_topical_source():
    """A store review of a targeted app is on-topic even without topic words."""
    review = _item(
        'The box arrived two days late again and everything inside was warm '
        'by the time I got home from work.',
        item_id='r1', provenance_relevance=0.4,
    )
    kept, _ = CorpusFilter(topic_terms=TOPIC).run([review])
    assert [i.item_id for i in kept] == ['r1']


# -- brand blocklist --------------------------------------------------------

def test_blocklist_ignores_separators_and_casing():
    pattern = SourceQuery(terms=['x'], exclude_terms=['HelloFresh']).excludes_pattern()
    for text in ('I tried HelloFresh once', 'hello fresh was ok', 'Hello-Fresh box', 'hello_fresh'):
        assert pattern.search(text), text


def test_blocklist_does_not_overmatch():
    pattern = SourceQuery(terms=['x'], exclude_terms=['HelloFresh']).excludes_pattern()
    assert not pattern.search('fresh hello')
    assert not pattern.search('helloworld fresh')


def test_blocklist_removes_items_before_scoring():
    items = [_item(
        'I used HelloFresh for a year and the meal kit delivery was always late.',
        item_id='brand',
    )]
    pattern = SourceQuery(terms=TOPIC, exclude_terms=['HelloFresh']).excludes_pattern()
    kept, stats = CorpusFilter(topic_terms=TOPIC, exclude_pattern=pattern).run(items)
    assert kept == []
    assert stats.dropped_excluded == 1


def test_empty_blocklist_is_none():
    assert SourceQuery(terms=['x']).excludes_pattern() is None


# -- deduplication ----------------------------------------------------------

def test_near_duplicates_collapse_to_the_highest_scoring_copy():
    base = ('The meal kit portions keep shrinking while the price goes up '
            'every single quarter without any warning')
    items = [
        _item(base, item_id='a', score=5),
        _item(base + '.', item_id='b', score=50),
        _item(base + ' now', item_id='c', score=1),
    ]
    kept, dropped = deduplicate(items)
    assert dropped == 2
    assert [i.item_id for i in kept] == ['b']


def test_distinct_texts_survive_dedup():
    items = [
        _item('The produce arrived wilted three weeks in a row and support ignored me', item_id='a'),
        _item('Delivery windows are impossible when you work a normal office job', item_id='b'),
    ]
    kept, dropped = deduplicate(items)
    assert dropped == 0
    assert len(kept) == 2


def test_simhash_is_stable_and_distance_ordered():
    a = SimHashIndex.simhash('the produce arrived wilted again this week')
    b = SimHashIndex.simhash('the produce arrived wilted again this week')
    c = SimHashIndex.simhash('completely unrelated text about racing tyre strategy')
    assert a == b
    assert SimHashIndex.hamming(a, c) > 3


# -- stats ------------------------------------------------------------------

def test_stats_account_for_every_input():
    items = [
        _item('first!', item_id='n1'),
        _item('The meal kit box arrived late and the recipe card was missing entirely', item_id='k1'),
        _item("My landlord still hasn't fixed the boiler after three weeks", item_id='o1'),
    ]
    kept, stats = CorpusFilter(topic_terms=TOPIC).run(items)
    accounted = (
        stats.kept + stats.dropped_noise + stats.dropped_irrelevant
        + stats.dropped_duplicate + stats.dropped_excluded
    )
    assert stats.total_in == 3
    assert accounted == 3
    assert len(kept) == stats.kept
