"""
Graph building service.
Endpoint 2: build a standalone graph through the Zep API.
"""

import hashlib
import uuid
import time
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass

from zep_cloud import BatchAddItem, EntityEdgeSourceTarget, NotFoundError

from ..config import Config
from ..utils.zep_paging import fetch_all_nodes, fetch_all_edges
from ..utils.ontology import (
    MAX_ONTOLOGY_TYPES,
    RESERVED_ONTOLOGY_ATTRIBUTE_NAMES,
    normalize_ontology_attributes,
    normalize_ontology_source_targets,
)
from ..utils.zep import (
    ZEP_INGESTION_WAIT_TIMEOUT_SECONDS,
    call_zep_read_with_retry,
    get_zep_client,
    is_retryable_zep_error,
)
from ..utils.locale import t


@dataclass(frozen=True)
class BatchSubmission:
    """Durable identity for one Zep Batch API ingestion operation."""

    batch_id: str
    operation_id: str
    episode_uuids: List[str]
    item_count: int


class GraphBuilderService:
    """
    Graph building service.
    Drives the Zep API to build the knowledge graph.
    """
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or Config.ZEP_API_KEY
        if not self.api_key:
            raise ValueError("ZEP_API_KEY is not configured")
        
        self.client = get_zep_client(self.api_key)

    def create_graph(
        self,
        name: str,
        *,
        graph_id: str | None = None,
        graph_id_callback: Optional[Callable[[str], None]] = None,
    ) -> str:
        """Create a graph with a caller-durable ID and reconcile lost replies."""

        graph_id = graph_id or f"spiegel_{uuid.uuid4().hex[:16]}"
        # Persist the client-generated ID before the non-idempotent POST so a
        # later reset can clean up a graph whose successful response was lost.
        if graph_id_callback:
            graph_id_callback(graph_id)

        try:
            self.client.graph.create(
                graph_id=graph_id,
                name=name,
                description="Spiegel Social Simulation Graph"
            )
        except Exception as error:
            if not is_retryable_zep_error(error):
                raise
            reconciliation_error = None
            for attempt in range(3):
                try:
                    call_zep_read_with_retry(
                        lambda: self.client.graph.get(graph_id),
                        operation_name=f"reconcile graph create {graph_id}",
                    )
                    reconciliation_error = None
                    break
                except NotFoundError as not_found:
                    reconciliation_error = not_found
                    if attempt < 2:
                        time.sleep(attempt + 1)
                except Exception as read_error:
                    reconciliation_error = read_error
                    break
            if reconciliation_error is not None:
                raise error from reconciliation_error

        return graph_id

    @staticmethod
    def build_operation_id(graph_id: str, chunks: List[str]) -> str:
        payload_hash = hashlib.sha256("\0".join(chunks).encode("utf-8")).hexdigest()
        return hashlib.sha256(
            f"{graph_id}:{payload_hash}".encode("utf-8")
        ).hexdigest()

    def _find_batch_by_operation_id(
        self,
        graph_id: str,
        operation_id: str,
        *,
        max_attempts: int = 3,
    ) -> Any | None:
        """Find one server-created batch after an ambiguous create reply."""

        for attempt in range(1, max_attempts + 1):
            matches: List[Any] = []
            cursor: int | None = None
            seen_cursors: set[int] = set()
            while True:
                page = call_zep_read_with_retry(
                    lambda: self.client.batch.list(limit=100, cursor=cursor),
                    operation_name=f"reconcile batch create {operation_id}",
                )
                for batch in getattr(page, "batches", None) or []:
                    metadata = getattr(batch, "metadata", None) or {}
                    if (
                        metadata.get("spiegel_operation_id") == operation_id
                        and metadata.get("graph_id") == graph_id
                    ):
                        matches.append(batch)
                next_cursor = getattr(page, "next_cursor", None)
                if next_cursor is None:
                    break
                if next_cursor == cursor or next_cursor in seen_cursors:
                    raise RuntimeError("Zep batch list cursor did not advance")
                seen_cursors.add(next_cursor)
                cursor = next_cursor

            if len(matches) > 1:
                raise RuntimeError(
                    f"Multiple Zep batches match operation {operation_id}; refusing ambiguity"
                )
            if matches:
                return matches[0]
            if attempt < max_attempts:
                time.sleep(attempt)
        return None
    
    def set_ontology(self, graph_id: str, ontology: Dict[str, Any]):
        """Install the graph ontology (public entry point)."""
        import warnings
        from typing import Optional
        from pydantic import Field
        from zep_cloud.external_clients.ontology import EntityModel, EntityText, EdgeModel
        
        # Suppress the Pydantic v2 warning about Field(default=None).
        # The Zep SDK requires this usage; the warning comes from dynamic class
        # creation and is safe to ignore.
        warnings.filterwarnings('ignore', category=UserWarning, module='pydantic')
        
        def safe_attr_name(attr_name: str) -> str:
            """Rewrite a reserved name into a safe one."""
            if attr_name.lower() in RESERVED_ONTOLOGY_ATTRIBUTE_NAMES:
                return f"entity_{attr_name}"
            return attr_name
        
        # Build the entity types dynamically
        entity_types = {}
        for entity_def in ontology.get("entity_types", [])[:MAX_ONTOLOGY_TYPES]:
            name = entity_def["name"]
            description = entity_def.get("description", f"A {name} entity.")
            
            # Build the attribute dict and type annotations (required by Pydantic v2)
            attrs = {"__doc__": description}
            annotations = {}
            
            for normalized in normalize_ontology_attributes(
                entity_def.get("attributes", [])
            ):
                attr_name = safe_attr_name(normalized["name"])  # Use the safe name
                attr_desc = normalized["description"]
                # The Zep API requires a Field description
                attrs[attr_name] = Field(description=attr_desc, default=None)
                annotations[attr_name] = Optional[EntityText]  # Type annotation
            
            attrs["__annotations__"] = annotations
            
            # Create the class dynamically
            entity_class = type(name, (EntityModel,), attrs)
            entity_class.__doc__ = description
            entity_types[name] = entity_class
        
        # Build the edge types dynamically
        edge_definitions = {}
        for edge_def in ontology.get("edge_types", [])[:MAX_ONTOLOGY_TYPES]:
            name = edge_def["name"]
            description = edge_def.get("description", f"A {name} relationship.")
            
            # Build the attribute dict and type annotations
            attrs = {"__doc__": description}
            annotations = {}
            
            for normalized in normalize_ontology_attributes(
                edge_def.get("attributes", [])
            ):
                attr_name = safe_attr_name(normalized["name"])  # Use the safe name
                attr_desc = normalized["description"]
                # The Zep API requires a Field description
                attrs[attr_name] = Field(description=attr_desc, default=None)
                annotations[attr_name] = Optional[str]  # Edge attributes use str
            
            attrs["__annotations__"] = annotations
            
            # Create the class dynamically
            class_name = ''.join(word.capitalize() for word in name.split('_'))
            edge_class = type(class_name, (EdgeModel,), attrs)
            edge_class.__doc__ = description
            
            # Build source_targets
            source_targets = []
            for st in normalize_ontology_source_targets(
                edge_def.get("source_targets", [])
            ):
                source_targets.append(
                    EntityEdgeSourceTarget(
                        source=st.get("source", "Entity"),
                        target=st.get("target", "Entity")
                    )
                )
            
            if source_targets:
                edge_definitions[name] = (edge_class, source_targets)
        
        # Install the ontology through the Zep API
        if entity_types or edge_definitions:
            self.client.graph.set_ontology(
                graph_ids=[graph_id],
                # Zep iterates entities.items(), so edge-only ontologies must
                # pass an empty dictionary rather than None.
                entities=entity_types,
                edges=edge_definitions if edge_definitions else None,
            )
    
    def add_text_batches(
        self,
        graph_id: str,
        chunks: List[str],
        batch_size: int = 350,
        progress_callback: Optional[Callable] = None,
        batch_created_callback: Optional[Callable[[str | None, str], None]] = None,
    ) -> BatchSubmission:
        """Submit document chunks through Zep's current Batch API.

        Mutating calls are deliberately not retried: create/add are not
        documented as idempotent, and an ambiguous replay can duplicate graph
        episodes. The returned batch identity allows callers to persist and
        reconcile the operation instead.
        """

        if not graph_id:
            raise ValueError("graph_id is required")
        self.validate_batch_chunks(chunks, batch_size=batch_size)

        total_chunks = len(chunks)
        operation_id = self.build_operation_id(graph_id, chunks)
        if batch_created_callback:
            # Journal the deterministic operation before the server-generated
            # batch ID POST. This leaves enough identity for later diagnosis
            # even if both the response and immediate list reconciliation fail.
            batch_created_callback(None, operation_id)

        try:
            batch = self.client.batch.create(
                metadata={
                    "spiegel_operation_id": operation_id,
                    "graph_id": graph_id,
                    "chunk_count": total_chunks,
                }
            )
        except Exception as error:
            if not is_retryable_zep_error(error):
                raise
            batch = self._find_batch_by_operation_id(graph_id, operation_id)
            if batch is None:
                raise RuntimeError(
                    "Zep batch creation is unconfirmed and no matching operation was found"
                ) from error
        batch_id = getattr(batch, "batch_id", None)
        if not batch_id:
            raise RuntimeError("Zep Batch API returned no batch_id")
        if batch_created_callback:
            batch_created_callback(batch_id, operation_id)

        episode_uuids: List[str] = []
        for i in range(0, total_chunks, batch_size):
            batch_chunks = chunks[i:i + batch_size]
            batch_num = i // batch_size + 1
            total_batches = (total_chunks + batch_size - 1) // batch_size
            
            if progress_callback:
                progress = (i + len(batch_chunks)) / total_chunks
                progress_callback(
                    t('progress.sendingBatch', current=batch_num, total=total_batches, chunks=len(batch_chunks)),
                    progress
                )
            
            items = [
                BatchAddItem(
                    type="graph_episode",
                    graph_id=graph_id,
                    data=chunk,
                    data_type="text",
                    source_description="Spiegel source document chunk",
                    metadata={
                        "spiegel_operation_id": operation_id,
                        "chunk_index": i + offset,
                        "chunk_sha256": hashlib.sha256(
                            chunk.encode("utf-8")
                        ).hexdigest(),
                    },
                )
                for offset, chunk in enumerate(batch_chunks)
            ]

            expected_item_count = i + len(items)
            try:
                item_details = self.client.batch.add(
                    batch_id=batch_id,
                    items=items,
                )
            except Exception as e:
                if progress_callback:
                    progress_callback(t('progress.batchFailed', batch=batch_num, error=str(e)), 0)
                if is_retryable_zep_error(e):
                    recovered_items = self._reconcile_batch_item_count(
                        batch_id,
                        expected_item_count,
                    )
                    recovered_indexes = {
                        getattr(item, "sequence_index", None)
                        for item in recovered_items
                    }
                    if (
                        len(recovered_items) == expected_item_count
                        and recovered_indexes == set(range(expected_item_count))
                    ):
                        item_details = recovered_items[i:expected_item_count]
                    else:
                        raise RuntimeError(
                            f"Zep batch {batch_id} item submission is unconfirmed; "
                            "the draft was not processed or replayed"
                        ) from e
                else:
                    raise RuntimeError(
                        f"Zep batch {batch_id} item submission failed"
                    ) from e

            if len(item_details or []) != len(items):
                recovered_items = self._reconcile_batch_item_count(
                    batch_id,
                    expected_item_count,
                )
                recovered_indexes = {
                    getattr(item, "sequence_index", None)
                    for item in recovered_items
                }
                if (
                    len(recovered_items) == expected_item_count
                    and recovered_indexes == set(range(expected_item_count))
                ):
                    item_details = recovered_items[i:expected_item_count]
                else:
                    raise RuntimeError(
                        f"Zep batch {batch_id} acknowledged {len(item_details or [])} "
                        f"of {len(items)} items"
                    )
            for item in item_details:
                episode_uuid = getattr(item, "episode_uuid", None)
                if episode_uuid:
                    episode_uuids.append(episode_uuid)

        try:
            self.client.batch.process(batch_id=batch_id)
        except Exception as error:
            # A process response can be lost after the server accepted it.
            # Reconcile with a safe GET instead of issuing a second POST.
            summary = call_zep_read_with_retry(
                lambda: self.client.batch.get(batch_id=batch_id),
                operation_name=f"reconcile batch {batch_id}",
            )
            if getattr(summary, "status", None) in {None, "draft"}:
                raise RuntimeError(
                    f"Zep batch {batch_id} processing is unconfirmed"
                ) from error

        return BatchSubmission(
            batch_id=batch_id,
            operation_id=operation_id,
            episode_uuids=episode_uuids,
            item_count=total_chunks,
        )

    @staticmethod
    def validate_batch_chunks(chunks: List[str], *, batch_size: int = 350) -> None:
        """Validate every Batch API limit before the first Cloud mutation."""

        if not chunks:
            raise ValueError("At least one text chunk is required")
        if not 1 <= batch_size <= 350:
            raise ValueError("batch_size must be between 1 and 350")
        if len(chunks) > 50_000:
            raise ValueError("A Zep batch cannot contain more than 50,000 items")
        oversized = [index for index, chunk in enumerate(chunks) if len(chunk) > 10_000]
        if oversized:
            raise ValueError(
                f"Zep batch item exceeds 10,000 characters at chunk {oversized[0]}"
            )

    def _list_batch_items(self, batch_id: str) -> List[Any]:
        items: List[Any] = []
        cursor: int | None = None
        seen_cursors: set[int] = set()
        while True:
            page = call_zep_read_with_retry(
                lambda: self.client.batch.list_items(
                    batch_id=batch_id,
                    limit=100,
                    cursor=cursor,
                ),
                operation_name=f"list batch items {batch_id}",
            )
            items.extend(getattr(page, "items", None) or [])
            next_cursor = getattr(page, "next_cursor", None)
            if next_cursor is None:
                break
            if next_cursor == cursor or next_cursor in seen_cursors:
                raise RuntimeError(f"Zep batch {batch_id} item cursor did not advance")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        return items

    def _reconcile_batch_item_count(
        self,
        batch_id: str,
        expected_item_count: int,
        *,
        max_attempts: int = 3,
    ) -> List[Any]:
        """Allow a short propagation window after an ambiguous add reply."""

        items: List[Any] = []
        for attempt in range(1, max_attempts + 1):
            items = self._list_batch_items(batch_id)
            if len(items) >= expected_item_count:
                return items
            if attempt < max_attempts:
                time.sleep(attempt)
        return items

    def get_batch_summary(self, batch_id: str) -> Any:
        """Read a persisted batch identity for restart reconciliation."""

        return call_zep_read_with_retry(
            lambda: self.client.batch.get(batch_id=batch_id),
            operation_name=f"get batch {batch_id}",
        )

    def _wait_for_batch(
        self,
        submission: BatchSubmission,
        progress_callback: Optional[Callable] = None,
        timeout: int | None = None,
    ) -> List[str]:
        """Wait for a Batch API terminal state and validate every item."""

        timeout = timeout or ZEP_INGESTION_WAIT_TIMEOUT_SECONDS
        start_time = time.time()
        terminal_states = {"succeeded", "partial", "failed", "invalid", "canceled"}

        while True:
            if time.time() - start_time > timeout:
                raise TimeoutError(
                    f"Zep batch {submission.batch_id} did not finish within {timeout}s"
                )

            summary = call_zep_read_with_retry(
                lambda: self.client.batch.get(batch_id=submission.batch_id),
                operation_name=f"poll batch {submission.batch_id}",
            )
            status = getattr(summary, "status", None)
            progress = getattr(summary, "progress", None)
            percent = float(getattr(progress, "percent_complete", 0) or 0) / 100
            if progress_callback:
                completed = int(getattr(progress, "succeeded_items", 0) or 0)
                progress_callback(
                    t(
                        'progress.zepProcessing',
                        completed=completed,
                        total=submission.item_count,
                        pending=max(submission.item_count - completed, 0),
                        elapsed=int(time.time() - start_time),
                    ),
                    min(max(percent, 0.0), 1.0),
                )

            if status in terminal_states:
                break
            time.sleep(3)

        items = self._list_batch_items(submission.batch_id)
        if status != "succeeded":
            failed_items = [
                item for item in items
                if getattr(item, "status", None) not in {"succeeded", "skipped"}
            ]
            first_error = getattr(failed_items[0], "error", None) if failed_items else None
            raise RuntimeError(
                f"Zep batch {submission.batch_id} ended as {status}; "
                f"failed_items={len(failed_items)}; first_error={first_error}"
            )
        if len(items) != submission.item_count:
            raise RuntimeError(
                f"Zep batch {submission.batch_id} contains {len(items)} items, "
                f"expected {submission.item_count}"
            )

        ordered_items = sorted(
            items,
            key=lambda item: getattr(item, "sequence_index", 0) or 0,
        )
        episode_uuids: List[str] = []
        for item in ordered_items:
            item_status = getattr(item, "status", None)
            episode_uuid = getattr(item, "episode_uuid", None)
            source_uuid = getattr(item, "source_uuid", None)
            if item_status != "succeeded" or not episode_uuid:
                raise RuntimeError(
                    f"Zep batch {submission.batch_id} returned an incomplete item"
                )
            if source_uuid and source_uuid != episode_uuid:
                raise RuntimeError(
                    f"Zep batch {submission.batch_id} returned mismatched episode UUIDs"
                )
            episode_uuids.append(episode_uuid)

        if progress_callback:
            progress_callback(
                t(
                    'progress.processingComplete',
                    completed=len(episode_uuids),
                    total=submission.item_count,
                ),
                1.0,
            )
        return episode_uuids
    
    def _wait_for_episodes(
        self,
        episode_uuids: List[str],
        progress_callback: Optional[Callable] = None,
        timeout: int = ZEP_INGESTION_WAIT_TIMEOUT_SECONDS
    ):
        """Wait for every episode to finish processing (polls each episode's processed flag)."""
        if not episode_uuids:
            if progress_callback:
                progress_callback(t('progress.noEpisodesWait'), 1.0)
            return
        
        start_time = time.time()
        pending_episodes = set(episode_uuids)
        completed_count = 0
        total_episodes = len(episode_uuids)
        
        if progress_callback:
            progress_callback(t('progress.waitingEpisodes', count=total_episodes), 0)
        
        while pending_episodes:
            if time.time() - start_time > timeout:
                if progress_callback:
                    progress_callback(
                        t('progress.episodesTimeout', completed=completed_count, total=total_episodes),
                        completed_count / total_episodes
                    )
                raise TimeoutError(
                    f"Zep episode processing timed out with "
                    f"{len(pending_episodes)} episode(s) still pending"
                )
            
            # Check the processing state of every episode
            for ep_uuid in list(pending_episodes):
                episode = call_zep_read_with_retry(
                    lambda: self.client.graph.episode.get(uuid_=ep_uuid),
                    operation_name=f"poll episode {ep_uuid}",
                )
                is_processed = getattr(episode, 'processed', False)

                if is_processed:
                    pending_episodes.remove(ep_uuid)
                    completed_count += 1
            
            elapsed = int(time.time() - start_time)
            if progress_callback:
                progress_callback(
                    t('progress.zepProcessing', completed=completed_count, total=total_episodes, pending=len(pending_episodes), elapsed=elapsed),
                    completed_count / total_episodes if total_episodes > 0 else 0
                )
            
            if pending_episodes:
                time.sleep(3)  # Poll every 3 seconds
        
        if progress_callback:
            progress_callback(t('progress.processingComplete', completed=completed_count, total=total_episodes), 1.0)
    
    def get_graph_data(self, graph_id: str) -> Dict[str, Any]:
        """
        Fetch the full graph payload, including detail fields.

        Args:
            graph_id: Graph ID

        Returns:
            A dict of nodes and edges carrying timestamps, attributes and other detail
        """
        nodes = fetch_all_nodes(self.client, graph_id)
        edges = fetch_all_edges(self.client, graph_id)

        # Build a node lookup so edges can resolve node names
        node_map = {}
        for node in nodes:
            node_map[node.uuid_] = node.name or ""
        
        nodes_data = []
        for node in nodes:
            # Read the creation time
            created_at = getattr(node, 'created_at', None)
            if created_at:
                created_at = str(created_at)
            
            nodes_data.append({
                "uuid": node.uuid_,
                "name": node.name,
                "labels": node.labels or [],
                "summary": node.summary or "",
                "attributes": node.attributes or {},
                "created_at": created_at,
            })
        
        edges_data = []
        for edge in edges:
            # Read the timestamps
            created_at = getattr(edge, 'created_at', None)
            valid_at = getattr(edge, 'valid_at', None)
            invalid_at = getattr(edge, 'invalid_at', None)
            expired_at = getattr(edge, 'expired_at', None)
            
            # Read the episodes
            episodes = getattr(edge, 'episodes', None) or getattr(edge, 'episode_ids', None)
            if episodes and not isinstance(episodes, list):
                episodes = [str(episodes)]
            elif episodes:
                episodes = [str(e) for e in episodes]
            
            # Read fact_type
            fact_type = getattr(edge, 'fact_type', None) or edge.name or ""
            
            edges_data.append({
                "uuid": edge.uuid_,
                "name": edge.name or "",
                "fact": edge.fact or "",
                "fact_type": fact_type,
                "source_node_uuid": edge.source_node_uuid,
                "target_node_uuid": edge.target_node_uuid,
                "source_node_name": node_map.get(edge.source_node_uuid, ""),
                "target_node_name": node_map.get(edge.target_node_uuid, ""),
                "attributes": edge.attributes or {},
                "created_at": str(created_at) if created_at else None,
                "valid_at": str(valid_at) if valid_at else None,
                "invalid_at": str(invalid_at) if invalid_at else None,
                "expired_at": str(expired_at) if expired_at else None,
                "episodes": episodes or [],
            })
        
        return {
            "graph_id": graph_id,
            "nodes": nodes_data,
            "edges": edges_data,
            "node_count": len(nodes_data),
            "edge_count": len(edges_data),
        }
    
    def delete_graph(self, graph_id: str):
        """Delete the graph."""
        self.client.graph.delete(graph_id=graph_id)
