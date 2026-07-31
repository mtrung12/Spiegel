"""
Zep entity reading and filtering service.
Reads nodes from a Zep graph and keeps the ones matching the predefined entity types.
"""

from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Set, Callable, TypeVar
from dataclasses import dataclass, field
from zep_cloud import NotFoundError

from ..config import Config
from ..utils.logger import get_logger
from ..utils.zep_paging import fetch_all_nodes, fetch_all_edges
from ..utils.zep import call_zep_read_with_retry, get_zep_client

logger = get_logger('spiegel.zep_entity_reader')

# Generic return type
T = TypeVar('T')


def _parse_iso(value: str) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp, tolerating Zep's trailing 'Z'."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    # A naive timestamp cannot be compared with an aware one; assume UTC,
    # which is what both sides of this comparison are written in.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _document_build_cutoff(graph_id: str) -> Optional[datetime]:
    """
    When this graph's document build finished, if it is known.

    Returns None for a graph no project claims, or one built before this
    field existed - in both cases every node is kept, which is the old
    behaviour.
    """
    # Imported here: models.project pulls in the config and filesystem layout,
    # and this module is imported from services/__init__.
    from ..models.project import ProjectManager

    try:
        projects = ProjectManager.find_projects_by_graph_id(graph_id)
    except Exception as e:
        logger.warning(f"could not resolve the project for graph {graph_id}: {e}")
        return None

    stamps = [_parse_iso(p.graph_built_at) for p in projects if p.graph_built_at]
    stamps = [s for s in stamps if s]
    # More than one project on a graph is not expected, but the latest build
    # is the safe reading: it keeps everything any of them contributed.
    return max(stamps) if stamps else None


def _created_after(node: Dict[str, Any], cutoff: datetime) -> bool:
    """Whether a node was written after the document build finished."""
    created = _parse_iso(node.get("created_at", ""))
    # An unparseable timestamp is kept: dropping a real entity is worse than
    # keeping a simulation artefact.
    return bool(created and created > cutoff)


@dataclass
class EntityNode:
    """Entity node data structure."""
    uuid: str
    name: str
    labels: List[str]
    summary: str
    attributes: Dict[str, Any]
    # Related edges
    related_edges: List[Dict[str, Any]] = field(default_factory=list)
    # Related nodes
    related_nodes: List[Dict[str, Any]] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "uuid": self.uuid,
            "name": self.name,
            "labels": self.labels,
            "summary": self.summary,
            "attributes": self.attributes,
            "related_edges": self.related_edges,
            "related_nodes": self.related_nodes,
        }
    
    def get_entity_type(self) -> Optional[str]:
        """Return the entity types, excluding the default Entity label."""
        for label in self.labels:
            if label not in ["Entity", "Node"]:
                return label
        return None


@dataclass
class FilteredEntities:
    """A filtered set of entities."""
    entities: List[EntityNode]
    entity_types: Set[str]
    total_count: int
    filtered_count: int
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "entities": [e.to_dict() for e in self.entities],
            "entity_types": list(self.entity_types),
            "total_count": self.total_count,
            "filtered_count": self.filtered_count,
        }


class ZepEntityReader:
    """
    Zep entity reading and filtering service.

    Responsibilities:
    1. Read every node from a Zep graph
    2. Keep the nodes matching the predefined entity types (those whose labels
       are not just "Entity")
    3. Fetch the related edges and connected nodes for each entity
    """
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or Config.ZEP_API_KEY
        if not self.api_key:
            raise ValueError("ZEP_API_KEY is not configured")
        
        self.client = get_zep_client(self.api_key)
    
    def _call_with_retry(
        self, 
        func: Callable[[], T], 
        operation_name: str,
        max_retries: int = 3,
        initial_delay: float = 2.0
    ) -> T:
        """
        Call the Zep API with retries.

        Args:
            func: Callable to run (a zero-argument lambda or callable)
            operation_name: Operation name, used in log output
            max_retries: Maximum number of retries (default 3)
            initial_delay: Initial delay in seconds

        Returns:
            The API call result
        """
        return call_zep_read_with_retry(
            func,
            operation_name=operation_name,
            max_attempts=max_retries,
            initial_delay=initial_delay,
        )
    
    def get_all_nodes(self, graph_id: str) -> List[Dict[str, Any]]:
        """
        Fetch every node in the graph (paginated).

        Args:
            graph_id: Graph ID

        Returns:
            The nodes
        """
        logger.info(f"fetching all nodes for graph {graph_id}...")

        nodes = fetch_all_nodes(self.client, graph_id)

        nodes_data = []
        for node in nodes:
            nodes_data.append({
                "uuid": getattr(node, 'uuid_', None) or getattr(node, 'uuid', ''),
                "name": node.name or "",
                "labels": node.labels or [],
                "summary": node.summary or "",
                "attributes": node.attributes or {},
                "created_at": getattr(node, 'created_at', '') or "",
            })

        logger.info(f"fetched {len(nodes_data)} nodes")
        return nodes_data

    def get_all_edges(self, graph_id: str) -> List[Dict[str, Any]]:
        """
        Fetch every edge in the graph (paginated).

        Args:
            graph_id: Graph ID

        Returns:
            The edges
        """
        logger.info(f"fetching all edges for graph {graph_id}...")

        edges = fetch_all_edges(self.client, graph_id)

        edges_data = []
        for edge in edges:
            edges_data.append({
                "uuid": getattr(edge, 'uuid_', None) or getattr(edge, 'uuid', ''),
                "name": edge.name or "",
                "fact": edge.fact or "",
                "source_node_uuid": edge.source_node_uuid,
                "target_node_uuid": edge.target_node_uuid,
                "attributes": edge.attributes or {},
            })

        logger.info(f"fetched {len(edges_data)} edges")
        return edges_data
    
    def get_node_edges(
        self,
        node_uuid: str,
        *,
        graph_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Fetch the edges attached to a node.

        In Zep Cloud 3.25, ``graph.node.get_edges`` only returns edges where the
        node is the source, despite the docs describing it as "all edges". Pass
        graph_id to get complete context: the whole graph is paginated and both
        incoming and outgoing edges are selected.
        
        Args:
            node_uuid: Node UUID
            graph_id: Graph ID; when supplied, both directions are returned
            
        Returns:
            The edges
        """
        try:
            if graph_id:
                return [
                    edge
                    for edge in self.get_all_edges(graph_id)
                    if edge["source_node_uuid"] == node_uuid
                    or edge["target_node_uuid"] == node_uuid
                ]

            # Call the Zep API through the retry helper
            edges = self._call_with_retry(
                func=lambda: self.client.graph.node.get_edges(node_uuid=node_uuid),
                operation_name=f"fetch node edges (node={node_uuid[:8]}...)"
            )
            
            edges_data = []
            for edge in edges:
                edges_data.append({
                    "uuid": getattr(edge, 'uuid_', None) or getattr(edge, 'uuid', ''),
                    "name": edge.name or "",
                    "fact": edge.fact or "",
                    "source_node_uuid": edge.source_node_uuid,
                    "target_node_uuid": edge.target_node_uuid,
                    "attributes": edge.attributes or {},
                })
            
            return edges_data
        except Exception as e:
            # An empty edge list is valid data. Authentication, permission and
            # transport failures must not be made indistinguishable from it.
            logger.error(f"failed to fetch edges for node {node_uuid}: {str(e)}")
            raise
    
    def filter_defined_entities(
        self, 
        graph_id: str,
        defined_entity_types: Optional[List[str]] = None,
        enrich_with_edges: bool = True
    ) -> FilteredEntities:
        """
        Keep only the nodes matching the predefined entity types.

        Filtering rules:
        - A node whose labels are just "Entity" does not match any predefined
          type, so it is skipped
        - A node carrying any label other than "Entity" and "Node" matches a
          predefined type and is kept
        - A node created after the document build finished is skipped: a
          running simulation writes its agents' activity back into the same
          graph, and those nodes are the simulation's own output, not the
          audience the brief described

        Args:
            graph_id: Graph ID
            defined_entity_types: Predefined entity types (optional; when given,
                only these types are kept)
            enrich_with_edges: Whether to fetch the related edges for each entity

        Returns:
            FilteredEntities: the filtered entity set
        """
        logger.info(f"filtering entities for graph {graph_id}...")
        
        # Fetch every node
        all_nodes = self.get_all_nodes(graph_id)
        total_count = len(all_nodes)

        cutoff = _document_build_cutoff(graph_id)
        if cutoff:
            before = len(all_nodes)
            all_nodes = [n for n in all_nodes if not _created_after(n, cutoff)]
            if before != len(all_nodes):
                logger.info(
                    f"ignoring {before - len(all_nodes)} node(s) written back into "
                    f"{graph_id} by a simulation after the document build"
                )

        # Fetch every edge (used for relationship lookups below)
        all_edges = self.get_all_edges(graph_id) if enrich_with_edges else []
        
        # Map node UUID to node data
        node_map = {n["uuid"]: n for n in all_nodes}
        
        # Keep the matching entities
        filtered_entities = []
        entity_types_found = set()
        
        for node in all_nodes:
            labels = node.get("labels", [])
            
            # Rule: the labels must include something other than "Entity" and "Node"
            custom_labels = [l for l in labels if l not in ["Entity", "Node"]]
            
            if not custom_labels:
                # Only the default labels: skip
                continue
            
            # When predefined types were supplied, check for a match
            if defined_entity_types:
                matching_labels = [l for l in custom_labels if l in defined_entity_types]
                if not matching_labels:
                    continue
                entity_type = matching_labels[0]
            else:
                entity_type = custom_labels[0]
            
            entity_types_found.add(entity_type)
            
            # Build the entity node object
            entity = EntityNode(
                uuid=node["uuid"],
                name=node["name"],
                labels=labels,
                summary=node["summary"],
                attributes=node["attributes"],
            )
            
            # Fetch the related edges and nodes
            if enrich_with_edges:
                related_edges = []
                related_node_uuids = set()
                
                for edge in all_edges:
                    if edge["source_node_uuid"] == node["uuid"]:
                        related_edges.append({
                            "direction": "outgoing",
                            "edge_name": edge["name"],
                            "fact": edge["fact"],
                            "target_node_uuid": edge["target_node_uuid"],
                        })
                        related_node_uuids.add(edge["target_node_uuid"])
                    elif edge["target_node_uuid"] == node["uuid"]:
                        related_edges.append({
                            "direction": "incoming",
                            "edge_name": edge["name"],
                            "fact": edge["fact"],
                            "source_node_uuid": edge["source_node_uuid"],
                        })
                        related_node_uuids.add(edge["source_node_uuid"])
                
                entity.related_edges = related_edges
                
                # Read the basics of the connected node
                related_nodes = []
                for related_uuid in related_node_uuids:
                    if related_uuid in node_map:
                        related_node = node_map[related_uuid]
                        related_nodes.append({
                            "uuid": related_node["uuid"],
                            "name": related_node["name"],
                            "labels": related_node["labels"],
                            "summary": related_node.get("summary", ""),
                        })
                
                entity.related_nodes = related_nodes
            
            filtered_entities.append(entity)
        
        logger.info(f"filtering complete: {total_count} nodes total, {len(filtered_entities)} matching, "
                   f"entity types: {entity_types_found}")
        
        return FilteredEntities(
            entities=filtered_entities,
            entity_types=entity_types_found,
            total_count=total_count,
            filtered_count=len(filtered_entities),
        )
    
    def get_entity_with_context(
        self, 
        graph_id: str, 
        entity_uuid: str
    ) -> Optional[EntityNode]:
        """
        Fetch one entity with its full context (edges and connected nodes, with retries).

        Args:
            graph_id: Graph ID
            entity_uuid: Entity UUID

        Returns:
            The EntityNode, or None
        """
        try:
            # Fetch the node through the retry helper
            node = self._call_with_retry(
                func=lambda: self.client.graph.node.get(uuid_=entity_uuid),
                operation_name=f"fetch node detail (uuid={entity_uuid[:8]}...)"
            )
            
            if not node:
                return None
            
            # Fetch the node's edges
            edges = self.get_node_edges(entity_uuid, graph_id=graph_id)
            
            # Fetch every node for relationship lookups
            all_nodes = self.get_all_nodes(graph_id)
            node_map = {n["uuid"]: n for n in all_nodes}
            
            # Process the related edges and nodes
            related_edges = []
            related_node_uuids = set()
            
            for edge in edges:
                if edge["source_node_uuid"] == entity_uuid:
                    related_edges.append({
                        "direction": "outgoing",
                        "edge_name": edge["name"],
                        "fact": edge["fact"],
                        "target_node_uuid": edge["target_node_uuid"],
                    })
                    related_node_uuids.add(edge["target_node_uuid"])
                else:
                    related_edges.append({
                        "direction": "incoming",
                        "edge_name": edge["name"],
                        "fact": edge["fact"],
                        "source_node_uuid": edge["source_node_uuid"],
                    })
                    related_node_uuids.add(edge["source_node_uuid"])
            
            # Read the connected node info
            related_nodes = []
            for related_uuid in related_node_uuids:
                if related_uuid in node_map:
                    related_node = node_map[related_uuid]
                    related_nodes.append({
                        "uuid": related_node["uuid"],
                        "name": related_node["name"],
                        "labels": related_node["labels"],
                        "summary": related_node.get("summary", ""),
                    })
            
            return EntityNode(
                uuid=getattr(node, 'uuid_', None) or getattr(node, 'uuid', ''),
                name=node.name or "",
                labels=node.labels or [],
                summary=node.summary or "",
                attributes=node.attributes or {},
                related_edges=related_edges,
                related_nodes=related_nodes,
            )
            
        except NotFoundError:
            return None
        except Exception as e:
            # Only an actual Zep 404 means "entity not found". Propagate 401,
            # 403 and exhausted transport errors so callers cannot prepare a
            # simulation with silently incomplete graph context.
            logger.error(f"failed to fetch entity {entity_uuid}: {str(e)}")
            raise
    
    def get_entities_by_type(
        self, 
        graph_id: str, 
        entity_type: str,
        enrich_with_edges: bool = True
    ) -> List[EntityNode]:
        """
        Fetch every entity of a given type.

        Args:
            graph_id: Graph ID
            entity_type: Entity type (e.g. "Student", "PublicFigure")
            enrich_with_edges: Whether to fetch the related edges

        Returns:
            The entities
        """
        result = self.filter_defined_entities(
            graph_id=graph_id,
            defined_entity_types=[entity_type],
            enrich_with_edges=enrich_with_edges
        )
        return result.entities
