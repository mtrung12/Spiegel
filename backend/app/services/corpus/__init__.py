"""
Public-discussion corpus.

Fetches real posts and comments from official APIs and reduces them to the
small set that is actually worth spending tokens on. Everything here is
LLM-free and dependency-free beyond httpx, so it can run over a large corpus
cheaply.

Which sources run is decided by config/corpus.yml; passing ``sources`` here
overrides the file.

Typical use::

    from app.services.corpus import CorpusHarvester, SourceQuery

    query = SourceQuery(
        terms=['meal kit delivery', 'recipe box subscription'],
        exclude_terms=['OurBrand', 'OurSlogan'],   # keep the answer out
        subreddits=['cooking', 'MealPrepSunday'],
        max_items=2000,
        since_days=365,
    )
    result = CorpusHarvester().harvest(query)
    print(result.summary_line())
"""

from .filters import (
    CorpusFilter,
    FilterStats,
    FilterVerdict,
    NoiseFilter,
    QualityConfig,
    RelevanceScorer,
    SimHashIndex,
    deduplicate,
    text_signals,
)
from .harvester import CorpusHarvester, HarvestResult
from .http import PoliteClient, RateLimitedError
from .models import (
    KIND_ARTICLE,
    KIND_COMMENT,
    KIND_POST,
    CorpusItem,
    FetchResult,
    SourceQuery,
    pseudonymize_author,
)
from .sources import (
    CREDENTIALED_SOURCES,
    DEFAULT_SOURCES,
    KEYLESS_SOURCES,
    SOURCE_REGISTRY,
    SourceAdapter,
    describe_sources,
    get_source,
)

__all__ = [
    'CorpusHarvester',
    'HarvestResult',
    'CorpusFilter',
    'FilterStats',
    'FilterVerdict',
    'NoiseFilter',
    'QualityConfig',
    'RelevanceScorer',
    'SimHashIndex',
    'deduplicate',
    'text_signals',
    'PoliteClient',
    'RateLimitedError',
    'CorpusItem',
    'FetchResult',
    'SourceQuery',
    'pseudonymize_author',
    'KIND_POST',
    'KIND_COMMENT',
    'KIND_ARTICLE',
    'SourceAdapter',
    'SOURCE_REGISTRY',
    'KEYLESS_SOURCES',
    'CREDENTIALED_SOURCES',
    'DEFAULT_SOURCES',
    'get_source',
    'describe_sources',
]
