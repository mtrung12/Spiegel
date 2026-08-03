"""Synchronous graph reads over one Graphiti partition (``group_id``).

Replaces the Zep paging helpers. The pagination is still explicit, because
Graphiti's ``get_by_group_ids`` is a single indexed query with a UUID cursor
rather than a full scan, and pulling a large graph in one statement is what
makes Neo4j hold the whole result set in memory before the first row arrives.

Nothing here retries. The old helper retried because every call was an HTTP
request to another provider's service that could time out, rate-limit, or 503;
these are Bolt queries to a database in the same deployment, and the Neo4j
driver already retries the transient class of failure itself.
"""

from __future__ import annotations

from typing import Any, List

from graphiti_core.edges import EntityEdge
from graphiti_core.errors import (
    GroupsEdgesNotFoundError,
    GroupsNodesNotFoundError,
    NodeNotFoundError,
)
from graphiti_core.nodes import EntityNode
from graphiti_core.search.search_config import SearchResults
from graphiti_core.search.search_config_recipes import (
    COMBINED_HYBRID_SEARCH_RRF,
    EDGE_HYBRID_SEARCH_RRF,
    NODE_HYBRID_SEARCH_RRF,
)

from .graphiti_client import get_graphiti, run_sync
from .logger import get_logger

logger = get_logger("spiegel.graphiti_graph")

_DEFAULT_PAGE_SIZE = 100

# Zep capped a search query at 400 characters and a result set at 50. Neither
# limit exists here, but both were load-bearing: callers pass whole simulation
# requirements as queries, and an unbounded limit turns one report tool call
# into the entire graph. Keep them as this deployment's own policy.
MAX_SEARCH_QUERY_CHARS = 400
MAX_SEARCH_RESULTS = 50

# RRF fuses the BM25, cosine and breadth-first result sets by rank alone - no
# model call. The cross-encoder recipes rerank with an LLM call per search,
# which on the report agent's fan-out of sub-queries is the difference between
# a few seconds and a few minutes.
_SEARCH_RECIPES = {
    "edges": EDGE_HYBRID_SEARCH_RRF,
    "nodes": NODE_HYBRID_SEARCH_RRF,
    "both": COMBINED_HYBRID_SEARCH_RRF,
}


def normalize_search_query(query: Any) -> str:
    """Return a non-empty query within this deployment's length limit."""

    if not isinstance(query, str):
        raise ValueError("Graph search query must be a string")
    normalized = query.strip()
    if not normalized:
        raise ValueError("Graph search query must not be empty")
    return normalized[:MAX_SEARCH_QUERY_CHARS]


def normalize_search_limit(limit: Any) -> int:
    """Clamp a search result limit to this deployment's cap."""

    try:
        normalized = int(limit)
    except (TypeError, ValueError) as exc:
        raise ValueError("Graph search limit must be an integer") from exc
    if normalized < 1:
        raise ValueError("Graph search limit must be at least 1")
    return min(normalized, MAX_SEARCH_RESULTS)


def search_graph(
    graph_id: str,
    query: str,
    limit: int = 10,
    scope: str = "edges",
) -> SearchResults:
    """Hybrid search (BM25 + vector + graph traversal) over one graph."""

    if not graph_id:
        raise ValueError("graph_id is required")
    recipe = _SEARCH_RECIPES.get(scope)
    if recipe is None:
        raise ValueError(f"Unsupported search scope: {scope!r}")

    return run_sync(
        get_graphiti().search_(
            query=normalize_search_query(query),
            config=recipe.model_copy(
                update={"limit": normalize_search_limit(limit)},
                deep=True,
            ),
            group_ids=[graph_id],
        )
    )


def _fetch_all(
    getter: Any,
    graph_id: str,
    *,
    item_name: str,
    page_size: int,
    max_items: int | None,
) -> List[Any]:
    if not graph_id:
        raise ValueError("graph_id is required")
    if not 1 <= page_size <= 1000:
        raise ValueError("page_size must be between 1 and 1000")
    if max_items is not None and max_items < 1:
        raise ValueError("max_items must be at least 1 when provided")

    driver = get_graphiti().driver
    all_items: List[Any] = []
    cursor: str | None = None

    while True:
        try:
            page = run_sync(
                getter(
                    driver,
                    group_ids=[graph_id],
                    limit=page_size,
                    uuid_cursor=cursor,
                )
            )
        except (GroupsNodesNotFoundError, GroupsEdgesNotFoundError):
            # Graphiti raises rather than returning [] when a page comes back
            # empty. That happens both for a graph with no edges at all and for
            # the page after a final page that was exactly page_size long, and
            # neither is an error - an empty graph is a legitimate state, which
            # is what a build that extracted nothing produces.
            return all_items[:max_items] if max_items is not None else all_items
        all_items.extend(page)

        if max_items is not None and len(all_items) >= max_items:
            logger.warning(
                "Graphiti %s pagination reached explicit max_items=%s for graph %s",
                item_name,
                max_items,
                graph_id,
            )
            return all_items[:max_items]

        # The cursor is `uuid < $cursor` over a `uuid DESC` ordering, so a short
        # page is the only end-of-results signal there is.
        if len(page) < page_size:
            return all_items
        cursor = page[-1].uuid


def fetch_all_nodes(
    graph_id: str,
    page_size: int = _DEFAULT_PAGE_SIZE,
    max_items: int | None = None,
) -> List[EntityNode]:
    """Fetch every entity node in the graph unless an explicit cap is given."""

    return _fetch_all(
        EntityNode.get_by_group_ids,
        graph_id,
        item_name="nodes",
        page_size=page_size,
        max_items=max_items,
    )


def fetch_all_edges(
    graph_id: str,
    page_size: int = _DEFAULT_PAGE_SIZE,
    max_items: int | None = None,
) -> List[EntityEdge]:
    """Fetch every entity edge in the graph unless an explicit cap is given."""

    return _fetch_all(
        EntityEdge.get_by_group_ids,
        graph_id,
        item_name="edges",
        page_size=page_size,
        max_items=max_items,
    )


def fetch_node(node_uuid: str) -> EntityNode | None:
    """Fetch one entity node, or None when it does not exist."""

    try:
        return run_sync(EntityNode.get_by_uuid(get_graphiti().driver, node_uuid))
    except NodeNotFoundError:
        return None


def fetch_node_edges(node_uuid: str) -> List[EntityEdge]:
    """Fetch every edge touching a node, in both directions.

    Zep's ``graph.node.get_edges`` only ever returned edges where the node was
    the source, which is why callers used to work around it by scanning the
    whole graph. This one is genuinely both directions, so the workaround is
    gone.
    """

    return run_sync(EntityEdge.get_by_node_uuid(get_graphiti().driver, node_uuid))
