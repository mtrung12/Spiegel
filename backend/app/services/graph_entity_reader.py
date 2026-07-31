"""
Entity reading and filtering service.
Reads nodes from the knowledge graph and keeps the ones matching the predefined
entity types.
"""

from typing import Dict, Any, List, Optional, Set
from dataclasses import dataclass, field

from ..utils.logger import get_logger
from ..utils.graphiti_graph import (
    fetch_all_edges,
    fetch_all_nodes,
    fetch_node,
    fetch_node_edges,
)

logger = get_logger('spiegel.graph_entity_reader')


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


class GraphEntityReader:
    """
    Entity reading and filtering service.

    Responsibilities:
    1. Read every node from the graph
    2. Keep the nodes matching the predefined entity types (those whose labels
       are not just "Entity")
    3. Fetch the related edges and connected nodes for each entity
    """

    def get_all_nodes(self, graph_id: str) -> List[Dict[str, Any]]:
        """
        Fetch every node in the graph (paginated).

        Args:
            graph_id: Graph ID

        Returns:
            The nodes
        """
        logger.info(f"fetching all nodes for graph {graph_id}...")

        nodes_data = [
            {
                "uuid": node.uuid,
                "name": node.name or "",
                "labels": node.labels or [],
                "summary": node.summary or "",
                "attributes": node.attributes or {},
            }
            for node in fetch_all_nodes(graph_id)
        ]

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

        edges_data = [self._edge_to_dict(edge) for edge in fetch_all_edges(graph_id)]

        logger.info(f"fetched {len(edges_data)} edges")
        return edges_data

    @staticmethod
    def _edge_to_dict(edge) -> Dict[str, Any]:
        return {
            "uuid": edge.uuid,
            "name": edge.name or "",
            "fact": edge.fact or "",
            "source_node_uuid": edge.source_node_uuid,
            "target_node_uuid": edge.target_node_uuid,
            "attributes": edge.attributes or {},
        }

    def get_node_edges(
        self,
        node_uuid: str,
        *,
        graph_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Fetch the edges attached to a node, in both directions.

        ``graph_id`` is accepted and ignored. It used to force a scan of the
        whole graph, because Zep's per-node edge endpoint silently returned only
        the edges where the node was the source; the graph query underneath this
        matches an undirected pattern, so the workaround is no longer needed.

        Args:
            node_uuid: Node UUID
            graph_id: Unused, kept so existing callers need no change

        Returns:
            The edges
        """
        try:
            return [self._edge_to_dict(edge) for edge in fetch_node_edges(node_uuid)]
        except Exception as e:
            # An empty edge list is valid data. A failure to reach the graph
            # store must not be made indistinguishable from it.
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
            node = fetch_node(entity_uuid)
            if not node:
                return None

            # Fetch the node's edges
            edges = self.get_node_edges(entity_uuid)
            
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
                uuid=node.uuid,
                name=node.name or "",
                labels=node.labels or [],
                summary=node.summary or "",
                attributes=node.attributes or {},
                related_edges=related_edges,
                related_nodes=related_nodes,
            )

        except Exception as e:
            # A missing node already came back as None above. Anything reaching
            # here is a graph store failure, and must propagate so callers
            # cannot prepare a simulation on silently incomplete context.
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
