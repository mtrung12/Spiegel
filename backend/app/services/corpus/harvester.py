"""
Corpus harvesting: fetch from every configured source, then filter.

This is the entry point the rest of the backend uses. It stops at a filtered,
deduplicated, ranked list of CorpusItem - turning that list into graph episodes
is a separate concern and deliberately not done here.
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from ...config import Config
from ...utils.logger import get_logger
from .filters import CorpusFilter, FilterStats, QualityConfig
from .http import PoliteClient
from .models import CorpusItem, FetchResult, SourceQuery
from .sources import DEFAULT_SOURCES, get_source

logger = get_logger('spiegel.corpus.harvester')


@dataclass
class HarvestResult:
    """Everything one harvest produced."""

    items: List[CorpusItem] = field(default_factory=list)
    stats: Optional[FilterStats] = None
    per_source: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            'item_count': len(self.items),
            'stats': self.stats.to_dict() if self.stats else None,
            'per_source': self.per_source,
            'errors': self.errors,
            'duration_seconds': round(self.duration_seconds, 2),
        }

    def summary_line(self) -> str:
        """One-line log summary."""
        if not self.stats:
            return f"{len(self.items)} items"
        s = self.stats
        return (
            f"{s.total_in} fetched -> {s.kept} kept "
            f"({s.kept / s.total_in:.0%} retention) | "
            f"noise {s.dropped_noise}, off-topic {s.dropped_irrelevant}, "
            f"dupes {s.dropped_duplicate}, excluded {s.dropped_excluded}"
        )


class CorpusHarvester:
    """
    Fetch public discussion for a topic and reduce it to usable material.

    Sources run in parallel because they are IO-bound and independent; the
    per-host rate limiting inside PoliteClient still applies, so parallelism
    never turns into hammering one API.
    """

    def __init__(
        self,
        sources: Optional[Sequence[str]] = None,
        quality_config: Optional[QualityConfig] = None,
        min_relevance: float = 0.12,
        dedupe_distance: int = 3,
        max_workers: int = 4,
    ):
        # Explicit argument wins, then config/corpus.yml, then every registered
        # source. An empty list is honoured rather than treated as "unset" - a
        # config that switches every source off must harvest nothing.
        if sources is None:
            sources = DEFAULT_SOURCES if Config.CORPUS_SOURCES is None else Config.CORPUS_SOURCES
        self.source_names = list(sources)
        self.quality_config = quality_config
        self.min_relevance = min_relevance
        self.dedupe_distance = dedupe_distance
        self.max_workers = max_workers

    def harvest(self, query: SourceQuery) -> HarvestResult:
        """
        Run the full fetch-and-filter pass.

        Args:
            query: What to fetch. Set ``exclude_terms`` to the campaign's own
                brand and slogan so the corpus describes the category rather
                than leaking the answer.

        Returns:
            A HarvestResult whose items are ranked by quality and relevance.
        """
        started = time.monotonic()
        result = HarvestResult()

        if not query.terms:
            result.errors.append('query has no terms')
            return result

        fetched = self._fetch_all(query, result)

        if not fetched:
            result.duration_seconds = time.monotonic() - started
            logger.info("corpus harvest produced nothing: %s", result.errors)
            return result

        corpus_filter = CorpusFilter(
            topic_terms=query.terms,
            exclude_pattern=query.excludes_pattern(),
            quality_config=self.quality_config,
            min_relevance=self.min_relevance,
            dedupe_distance=self.dedupe_distance,
        )
        kept, stats = corpus_filter.run(fetched)

        result.items = kept
        result.stats = stats
        result.duration_seconds = time.monotonic() - started
        logger.info("corpus harvest: %s", result.summary_line())
        return result

    def _fetch_all(self, query: SourceQuery, result: HarvestResult) -> List[CorpusItem]:
        """Run every source, tolerating individual failures."""
        # One shared client keeps the rate-limit buckets global rather than
        # per-adapter, which is what actually protects the upstream hosts.
        client = PoliteClient()
        fetched: List[CorpusItem] = []

        # Give each source its own slice of the budget rather than letting the
        # first one to finish consume everything.
        per_source_query = SourceQuery(**{
            **query.__dict__,
            'max_items': max(query.max_items // max(len(self.source_names), 1), 50),
        })

        try:
            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                futures = {}
                for name in self.source_names:
                    try:
                        adapter = get_source(name, client=client)
                    except KeyError as exc:
                        result.errors.append(str(exc))
                        continue

                    available, reason = adapter.is_available()
                    if not available:
                        result.errors.append(f"{name} skipped: {reason}")
                        result.per_source.append({
                            'source': name, 'item_count': 0, 'skipped': True, 'reason': reason,
                        })
                        continue

                    futures[pool.submit(adapter.fetch, per_source_query)] = name

                for future in as_completed(futures):
                    name = futures[future]
                    try:
                        source_result: FetchResult = future.result()
                    except Exception as exc:
                        logger.warning("corpus source %s failed: %s", name, exc)
                        result.errors.append(f"{name}: {exc}")
                        result.per_source.append({
                            'source': name, 'item_count': 0, 'skipped': False, 'reason': str(exc),
                        })
                        continue

                    fetched.extend(source_result.items)
                    result.errors.extend(source_result.errors)
                    result.per_source.append(source_result.to_dict())
        finally:
            client.close()

        return fetched
