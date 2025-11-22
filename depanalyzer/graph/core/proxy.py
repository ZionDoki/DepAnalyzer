"""Proxy nodes for external graph references.

GraphRef/Proxy nodes represent references to nodes in other transaction graphs,
without merging the graph bodies. This enables parent transactions to reference
third-party dependencies without loading their full graphs.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger("depanalyzer.graph.core.proxy")


@dataclass
class GraphRef:
    """Reference to a node in an external graph.

    Attributes:
        graph_id: External transaction graph ID.
        node_id: Node ID within the external graph.
        node_type: Type of the referenced node.
        metadata: Optional summary metadata from external graph.
    """

    graph_id: str
    node_id: str
    node_type: str
    metadata: Optional[Dict[str, Any]] = None

    def to_proxy_node_id(self) -> str:
        """Generate proxy node ID for this reference.

        Returns:
            str: Proxy node ID in format //proxy:{graph_id}:{node_id}
        """
        return f"//proxy:{self.graph_id}:{self.node_id}"

    @classmethod
    def from_proxy_node_id(cls, proxy_node_id: str) -> Optional["GraphRef"]:
        """Parse GraphRef from proxy node ID.

        Args:
            proxy_node_id: Proxy node ID string.

        Returns:
            Optional[GraphRef]: Parsed GraphRef or None if invalid format.
        """
        if not proxy_node_id.startswith("//proxy:"):
            return None

        parts = proxy_node_id[8:].split(":", 2)
        if len(parts) < 2:
            return None

        return cls(
            graph_id=parts[0],
            node_id=parts[1],
            node_type="proxy",  # Default type, should be updated from metadata
        )


def create_proxy_node(
    graph_ref: GraphRef,
    confidence: float = 0.5,
    evidence: Optional[list[str]] = None,
) -> Dict[str, Any]:
    """Create proxy node attributes.

    Args:
        graph_ref: Reference to external graph node.
        confidence: Confidence score for proxy link.
        evidence: Evidence for proxy relationship.

    Returns:
        Dict[str, Any]: Node attributes dictionary.
    """
    attrs = {
        "type": "proxy",
        "proxy_graph_id": graph_ref.graph_id,
        "proxy_node_id": graph_ref.node_id,
        "proxy_node_type": graph_ref.node_type,
        "confidence": confidence,
        "over_approx": True,  # Proxies are over-approximations by nature
    }

    if evidence:
        attrs["evidence"] = evidence

    if graph_ref.metadata:
        attrs["proxy_metadata"] = graph_ref.metadata

    return attrs


def is_proxy_node(node_id: str) -> bool:
    """Check if node ID represents a proxy node.

    Args:
        node_id: Node identifier.

    Returns:
        bool: True if node_id is a proxy.
    """
    return node_id.startswith("//proxy:")


def get_proxy_graph_id(node_id: str) -> Optional[str]:
    """Extract graph_id from proxy node ID.

    Args:
        node_id: Proxy node identifier.

    Returns:
        Optional[str]: Graph ID or None if not a proxy.
    """
    graph_ref = GraphRef.from_proxy_node_id(node_id)
    if graph_ref:
        return graph_ref.graph_id
    return None
