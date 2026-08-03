"""
Graph building service.
Endpoint 2: build a standalone knowledge graph with Graphiti.

Ingestion runs in this process now rather than on Zep Cloud's queue, which is
what removed most of this module. There is no batch to create, no server-issued
batch ID to persist, no ambiguous POST reply to reconcile against a listing, and
no episode ``processed`` flag to poll: when ``add_episode_bulk`` returns, the
episodes are in Neo4j. A build that dies mid-way leaves a partial graph and is
restarted, not resumed.
"""

import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Callable

from graphiti_core.nodes import EpisodeType
from graphiti_core.utils.bulk_utils import RawEpisode
from graphiti_core.utils.maintenance.graph_data_operations import clear_data

from ..utils.graphiti_client import get_graphiti, run_sync
from ..utils.graphiti_graph import fetch_all_edges, fetch_all_nodes
from ..utils.ontology import (
    SEGMENT_EXTRACTION_INSTRUCTIONS,
    build_graphiti_ontology,
)
from ..utils.locale import t

# One episode is one text chunk. Beyond roughly this length extraction quality
# falls off sharply - the model starts summarising instead of enumerating - and
# the chunker is configured well below it anyway (Config.DEFAULT_CHUNK_SIZE).
# Kept as a guard against a caller passing whole documents as one chunk.
MAX_EPISODE_CHARS = 10_000


class GraphBuilderService:
    """
    Graph building service.
    Drives Graphiti to build the knowledge graph.
    """

    def __init__(self):
        self.client = get_graphiti()

    def create_graph(
        self,
        name: str,
        *,
        graph_id: str | None = None,
        graph_id_callback: Optional[Callable[[str], None]] = None,
    ) -> str:
        """Mint the graph identifier this project's episodes are partitioned by.

        Graphiti partitions by ``group_id``, which is created implicitly by the
        first episode written to it. So unlike Zep's ``graph.create`` there is no
        remote call here and nothing to reconcile - but the ID is still handed to
        the callback before any ingestion, so a later reset can clean up a graph
        whose build then failed.
        """

        graph_id = graph_id or f"spiegel_{uuid.uuid4().hex[:16]}"
        if graph_id_callback:
            graph_id_callback(graph_id)
        return graph_id

    def add_text_batches(
        self,
        graph_id: str,
        chunks: List[str],
        ontology: Optional[Dict[str, Any]] = None,
        batch_size: int = 20,
        progress_callback: Optional[Callable] = None,
    ) -> int:
        """Ingest document chunks as episodes, returning how many were written.

        Uses ``add_episode_bulk``, which extracts and deduplicates across the
        whole slice at once. It deliberately skips the edge-invalidation pass
        that sequential ``add_episode`` runs: the source documents are one static
        corpus captured at a single point in time, so there are no contradicting
        later facts for it to expire. Streaming simulation activity is the
        opposite case and goes through ``add_episode`` one at a time - see
        graph_memory_updater.
        """

        if not graph_id:
            raise ValueError("graph_id is required")
        self.validate_batch_chunks(chunks, batch_size=batch_size)

        entity_types, edge_types, edge_type_map = build_graphiti_ontology(ontology)
        total_chunks = len(chunks)
        reference_time = datetime.now(timezone.utc)
        written = 0

        for start in range(0, total_chunks, batch_size):
            batch_chunks = chunks[start:start + batch_size]
            batch_num = start // batch_size + 1
            total_batches = (total_chunks + batch_size - 1) // batch_size

            if progress_callback:
                progress_callback(
                    t(
                        'progress.sendingBatch',
                        current=batch_num,
                        total=total_batches,
                        chunks=len(batch_chunks),
                    ),
                    start / total_chunks,
                )

            episodes = [
                RawEpisode(
                    name=f"chunk_{start + offset}",
                    content=chunk,
                    source=EpisodeType.text,
                    source_description="Spiegel source document chunk",
                    reference_time=reference_time,
                )
                for offset, chunk in enumerate(batch_chunks)
            ]

            run_sync(
                self.client.add_episode_bulk(
                    bulk_episodes=episodes,
                    group_id=graph_id,
                    entity_types=entity_types,
                    edge_types=edge_types,
                    edge_type_map=edge_type_map,
                    custom_extraction_instructions=SEGMENT_EXTRACTION_INSTRUCTIONS,
                )
            )
            written += len(batch_chunks)

        if progress_callback:
            progress_callback(
                t('progress.processingComplete', completed=written, total=total_chunks),
                1.0,
            )
        return written

    @staticmethod
    def validate_batch_chunks(chunks: List[str], *, batch_size: int = 20) -> None:
        """Validate the ingestion input before the first write."""

        if not chunks:
            raise ValueError("At least one text chunk is required")
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        oversized = [
            index for index, chunk in enumerate(chunks)
            if len(chunk) > MAX_EPISODE_CHARS
        ]
        if oversized:
            raise ValueError(
                f"Episode text exceeds {MAX_EPISODE_CHARS} characters "
                f"at chunk {oversized[0]}"
            )

    def get_graph_data(self, graph_id: str) -> Dict[str, Any]:
        """
        Fetch the full graph payload, including detail fields.

        Args:
            graph_id: Graph ID

        Returns:
            A dict of nodes and edges carrying timestamps, attributes and other detail
        """
        nodes = fetch_all_nodes(graph_id)
        edges = fetch_all_edges(graph_id)

        # Build a node lookup so edges can resolve node names
        node_map = {node.uuid: node.name or "" for node in nodes}

        nodes_data = [
            {
                "uuid": node.uuid,
                "name": node.name,
                "labels": node.labels or [],
                "summary": node.summary or "",
                "attributes": node.attributes or {},
                "created_at": str(node.created_at) if node.created_at else None,
            }
            for node in nodes
        ]

        edges_data = []
        for edge in edges:
            # Zep exposed a fact_type separate from the edge name; in Graphiti the
            # edge name *is* the ontology edge type, so the frontend's fact_type
            # keeps working by reading the same value.
            edges_data.append({
                "uuid": edge.uuid,
                "name": edge.name or "",
                "fact": edge.fact or "",
                "fact_type": edge.name or "",
                "source_node_uuid": edge.source_node_uuid,
                "target_node_uuid": edge.target_node_uuid,
                "source_node_name": node_map.get(edge.source_node_uuid, ""),
                "target_node_name": node_map.get(edge.target_node_uuid, ""),
                "attributes": edge.attributes or {},
                "created_at": str(edge.created_at) if edge.created_at else None,
                "valid_at": str(edge.valid_at) if edge.valid_at else None,
                "invalid_at": str(edge.invalid_at) if edge.invalid_at else None,
                "expired_at": str(edge.expired_at) if edge.expired_at else None,
                "episodes": [str(episode) for episode in (edge.episodes or [])],
            })

        return {
            "graph_id": graph_id,
            "nodes": nodes_data,
            "edges": edges_data,
            "node_count": len(nodes_data),
            "edge_count": len(edges_data),
        }

    def delete_graph(self, graph_id: str):
        """Delete every node, edge and episode in this graph's partition."""
        if not graph_id:
            raise ValueError("graph_id is required")
        run_sync(clear_data(self.client.driver, group_ids=[graph_id]))
