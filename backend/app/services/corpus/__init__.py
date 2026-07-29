"""
Public-discussion corpus.

Fetches real posts and comments and reduces them to the small set that is
actually worth spending tokens on. The filtering is LLM-free and needs nothing
beyond httpx, so it can run over a large corpus cheaply.

Which sources run is decided by config/corpus.yml; passing ``sources`` here
overrides the file. Sources on official APIs are enabled there; browser-rendered
sources are not, and enabling one is a deliberate decision with terms-of-service
consequences - see ``sources/threads.py``.

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

from .extract import (
    CorpusThemeExtractor,
    ThemeGroup,
    normalize_theme,
    render_distribution,
)
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
    KIND_COMMENT,
    KIND_POST,
    CorpusItem,
    FetchResult,
    SourceQuery,
    pseudonymize_author,
)
from .query_builder import DerivedQuery, derive_query
from .sources import (
    BROWSER_SOURCES,
    CREDENTIALED_SOURCES,
    DEFAULT_SOURCES,
    SOURCE_REGISTRY,
    SourceAdapter,
    describe_sources,
    get_source,
)

__all__ = [
    'CorpusHarvester',
    'HarvestResult',
    'CorpusThemeExtractor',
    'ThemeGroup',
    'normalize_theme',
    'render_distribution',
    'DerivedQuery',
    'derive_query',
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
    'SourceAdapter',
    'SOURCE_REGISTRY',
    'CREDENTIALED_SOURCES',
    'BROWSER_SOURCES',
    'DEFAULT_SOURCES',
    'get_source',
    'describe_sources',
]
