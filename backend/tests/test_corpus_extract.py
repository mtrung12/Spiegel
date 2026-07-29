"""
Theme extraction and audience-prior apportionment.

Two things here decide whether the harvested corpus actually shapes the
simulation: the aggregation has to count merged themes once, and the allocator
has to turn a share into that share of the agent pool. Both are pinned below.
The LLM calls are stubbed - what is under test is the arithmetic around them.
"""

import pytest

from app.models.task import TaskCancelled
from app.services.corpus import CorpusItem, CorpusThemeExtractor, render_distribution
from app.services.corpus.extract import normalize_theme
from app.services.corpus.models import KIND_COMMENT
from app.services.oasis_profile_generator import OasisProfileGenerator


def _item(text, *, item_id, score=0):
    return CorpusItem(
        item_id=item_id, source='test', kind=KIND_COMMENT, text=text, score=score
    )


class _StubExtractor(CorpusThemeExtractor):
    """Extractor with both LLM passes replaced by fixed answers."""

    def __init__(self, labels, merge_map=None, **kwargs):
        super().__init__(**kwargs)
        self._labels = labels
        self._merge_map = merge_map or {}

    def _classify(self, items, topic_context, should_cancel=None):
        return {i.uid: self._labels[i.item_id] for i in items if i.item_id in self._labels}

    def _merge_synonyms(self, tags):
        return self._merge_map


# -- theme normalisation ----------------------------------------------------

def test_normalize_folds_typography_and_plurals():
    assert normalize_theme('Price too high') == normalize_theme('prices too high.')


def test_normalize_keeps_distinct_themes_apart():
    assert normalize_theme('price too high') != normalize_theme('value for money')


# -- aggregation ------------------------------------------------------------

def test_merged_synonyms_count_as_one_theme():
    items = [
        _item('costs way too much for what you get', item_id='a'),
        _item('the price is simply not justifiable', item_id='b'),
        _item('saves me a couple of hours every week', item_id='c'),
    ]
    extractor = _StubExtractor(
        labels={
            'a': 'negative|price too high',
            'b': 'negative|too expensive',
            'c': 'positive|saves time',
        },
        merge_map={'too expensive': 'price too high'},
    )

    result = extractor.extract(items)
    themes = {t['theme']: t for t in result['themes']}

    assert 'price too high' in themes
    assert 'too expensive' not in themes
    assert themes['price too high']['count'] == 2
    assert themes['price too high']['share_pct'] == round(2 / 3 * 100, 1)
    assert themes['price too high']['dominant_sentiment'] == 'negative'
    assert result['sentiment'] == {'positive': 1, 'neutral': 0, 'negative': 2}


def test_engagement_ranks_themes_without_letting_one_thread_dominate():
    # One 5000-score item against three ordinary ones. Weighting linearly would
    # put the viral theme first; log damping must not.
    items = [_item('viral complaint about delivery', item_id='v', score=5000)]
    items += [
        _item(f'ordinary price complaint {n}', item_id=f'p{n}', score=3)
        for n in range(3)
    ]
    labels = {'v': 'negative|shipping too slow'}
    labels.update({f'p{n}': 'negative|price too high' for n in range(3)})

    result = _StubExtractor(labels=labels).extract(items)
    assert result['themes'][0]['theme'] == 'price too high'


def test_empty_corpus_yields_empty_distribution():
    result = _StubExtractor(labels={}).extract([])
    assert result['themes'] == []
    assert result['n_classified'] == 0
    assert render_distribution(result) == ''


# -- cancellation -----------------------------------------------------------

class _CountingExtractor(CorpusThemeExtractor):
    """Counts batches instead of calling the model."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.batches = 0

    def _classify_batch(self, batch, topic_context, known_themes=None):
        self.batches += 1
        return {}


def test_classification_stops_between_batches_when_cancelled():
    # Enough items for several batches, cancelled once the first is done.
    items = [_item(f'a real complaint about the price {n}', item_id=str(n)) for n in range(80)]
    extractor = _CountingExtractor()

    seen = {'n': 0}

    def cancel_after_one_batch():
        seen['n'] += 1
        return seen['n'] > 1

    with pytest.raises(TaskCancelled):
        extractor.extract(items, should_cancel=cancel_after_one_batch)

    # It must stop early rather than coding the whole corpus and then raising.
    assert extractor.batches < 4


def test_classification_runs_to_completion_when_not_cancelled():
    items = [_item(f'a real complaint about the price {n}', item_id=str(n)) for n in range(80)]
    extractor = _CountingExtractor()
    extractor.extract(items, should_cancel=lambda: False)
    assert extractor.batches == 4


# -- apportionment ----------------------------------------------------------

def _distribution(*shares):
    return {
        'themes': [
            {
                'theme': name,
                'share_pct': share,
                'dominant_sentiment': 'negative',
                'examples': [],
            }
            for name, share in shares
        ]
    }


def _allocate(distribution, count):
    generator = OasisProfileGenerator.__new__(OasisProfileGenerator)
    generator.corpus_distribution = distribution
    return generator.allocate_themes(count)


def test_allocation_matches_the_distribution():
    allocation = _allocate(_distribution(('price', 50.0), ('shipping', 30.0), ('quality', 20.0)), 10)

    counts = {}
    for theme in allocation:
        counts[theme['theme']] = counts.get(theme['theme'], 0) + 1

    assert len(allocation) == 10
    assert counts == {'price': 5, 'shipping': 3, 'quality': 2}


def test_every_agent_is_allocated_when_shares_do_not_divide_evenly():
    # Thirds into 10 seats: largest remainder must still fill all ten.
    allocation = _allocate(_distribution(('a', 33.3), ('b', 33.3), ('c', 33.4)), 10)
    assert len(allocation) == 10
    assert all(theme is not None for theme in allocation)


def test_small_theme_survives_rounding():
    # 5% of 20 agents is exactly one seat; it must not round away to zero.
    allocation = _allocate(_distribution(('major', 95.0), ('minor', 5.0)), 20)
    assert sum(1 for t in allocation if t['theme'] == 'minor') == 1


def test_no_distribution_allocates_nothing():
    assert _allocate(None, 5) == [None] * 5
    assert _allocate({'themes': []}, 5) == [None] * 5


def test_allocation_spreads_themes_across_the_pool():
    # A truncated or partly failed run should still cover several themes, so the
    # first few agents must not all carry the single largest one.
    allocation = _allocate(_distribution(('price', 60.0), ('shipping', 40.0)), 10)
    assert len({t['theme'] for t in allocation[:4]}) > 1


# -- prompt rendering -------------------------------------------------------

def test_rendered_prior_names_the_share_and_quotes_the_source():
    distribution = {
        'n_classified': 100,
        'themes': [{
            'theme': 'price too high',
            'share_pct': 22.8,
            'dominant_sentiment': 'negative',
            'examples': [{'text': 'forty euro for three meals is absurd', 'score': 9}],
        }],
    }
    rendered = render_distribution(distribution)
    assert 'price too high' in rendered
    assert '22.8%' in rendered
    assert 'forty euro for three meals is absurd' in rendered
