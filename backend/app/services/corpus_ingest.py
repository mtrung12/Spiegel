"""
Corpus ingestion step for the graph build.

Glues the crawler to the pipeline: derive a query from the brief, harvest,
extract a theme distribution, and render the result as text the graph build can
chunk alongside the brief itself.

Everything here is best-effort. A missing Reddit key, a rate limit, a brief that
yields no category terms - none of those should fail a graph build that would
otherwise succeed, so the caller gets an empty result and carries on with the
brief alone.
"""

from typing import Any, Callable, Dict, Optional

from ..models.task import TaskCancelled
from ..utils.logger import get_logger
from .corpus import CorpusHarvester, CorpusThemeExtractor, derive_query, render_distribution

logger = get_logger('spiegel.corpus.ingest')

# How many harvested items reach the LLM coding pass. The filters rank by
# quality and relevance, so this is the head of a much larger list.
MAX_CODED_ITEMS = 300

# Fetch budget, split across whatever sources are enabled.
MAX_FETCHED_ITEMS = 800


def _empty(reason: str) -> Dict[str, Any]:
    return {'distribution': None, 'episode_text': '', 'summary': {'skipped': reason}}


def ingest_corpus(
    brief_text: str,
    progress: Optional[Callable[..., None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """
    Run the full crawl-and-code pass for one campaign brief.

    Args:
        brief_text: The extracted campaign brief
        progress: Optional callback for user-visible status messages, called as
            ``(message, state=..., **fields)``
        should_cancel: Optional predicate polled between stages; when it returns
            True the pass raises ``TaskCancelled`` rather than finishing

    Returns:
        ``{"distribution": dict|None, "episode_text": str, "summary": dict}``.
        ``episode_text`` is empty whenever there is nothing worth adding to the
        graph.

    Raises:
        TaskCancelled: the caller asked for the pipeline to stop
    """
    def say(message: str, state: Optional[str] = None, **fields) -> None:
        logger.info(message)
        if progress:
            progress(message, state=state, **fields)

    def check() -> None:
        if should_cancel and should_cancel():
            raise TaskCancelled("corpus ingestion cancelled")

    check()

    try:
        derived = derive_query(brief_text, max_items=MAX_FETCHED_ITEMS)
    except TaskCancelled:
        raise
    except Exception as e:
        logger.warning("corpus query derivation raised: %s: %s", type(e).__name__, e)
        return _empty('query_derivation_failed')

    if derived is None:
        return _empty('no_query_terms')

    check()
    say(
        f"searching public discussion: {', '.join(derived.query.terms[:3])}",
        state='searching',
    )

    try:
        harvest = CorpusHarvester().harvest(derived.query)
    except TaskCancelled:
        raise
    except Exception as e:
        logger.warning("corpus harvest raised: %s: %s", type(e).__name__, e)
        return _empty('harvest_failed')

    check()

    summary: Dict[str, Any] = {
        'terms': derived.query.terms,
        'exclude_terms': derived.query.exclude_terms,
        'subreddits': derived.query.subreddits,
        'context': derived.context,
        **harvest.to_dict(),
    }

    if not harvest.items:
        logger.info("corpus harvest returned nothing: %s", harvest.errors)
        return {'distribution': None, 'episode_text': '', 'summary': summary}

    say(
        f"coding {min(len(harvest.items), MAX_CODED_ITEMS)} of {len(harvest.items)} discussions",
        state='coding',
    )

    try:
        distribution = CorpusThemeExtractor(max_items=MAX_CODED_ITEMS).extract(
            harvest.items,
            topic_context=derived.context,
            should_cancel=should_cancel,
        )
    except TaskCancelled:
        raise
    except Exception as e:
        logger.warning("corpus theme extraction raised: %s: %s", type(e).__name__, e)
        return {'distribution': None, 'episode_text': '', 'summary': summary}

    episode_text = render_distribution(distribution)
    summary['themes'] = len(distribution.get('themes') or [])
    summary['coded'] = distribution.get('n_classified', 0)

    if episode_text:
        say(f"found {summary['themes']} recurring themes across {summary['coded']} discussions")

    return {
        'distribution': distribution,
        'episode_text': episode_text,
        'summary': summary,
    }
