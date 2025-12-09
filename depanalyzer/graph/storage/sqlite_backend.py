"""SQLite-based graph backend.

Implements the GraphBackend protocol using SQLite for storage.
"""

import json
import logging
from typing import Any, Dict, Iterable, Optional, Set

from depanalyzer.graph.core.backend import GraphBackend
from depanalyzer.graph.storage.sqlite_store import SQLiteStore

logger = logging.getLogger("depanalyzer.graph.storage.sqlite_backend")


class SQLiteGraphBackend(GraphBackend, SQLiteStore):
    """SQLite-based graph backend.

    Stores nodes and edges in SQLite tables.
    """

    def __init__(self, db_path: str):
        """Initialize SQLite graph backend.

        Args:
            db_path: Path to the SQLite database file.
        """
        # Initialize SQLiteStore
        super().__init__(db_path)
        logger.debug("SQLiteGraphBackend initialized at %s", db_path)

    def _init_db(self) -> None:
        """Initialize database schema."""
        conn = self._get_conn()
        
        # Nodes table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY,
                type TEXT,
                data TEXT
            )
        """)
        
        # Edges table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS edges (
                source TEXT,
                target TEXT,
                key INTEGER DEFAULT 0,
                kind TEXT,
                data TEXT,
                PRIMARY KEY (source, target, key)
            )
        """)
        
        # Indexes for performance
        conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_source ON edges (source)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_target ON edges (target)")

    @property
    def native_graph(self) -> Any:
        """Get native graph object.
        
        For SQLite backend, this returns self as it manages the data directly.
        """
        return self

    def set_native_graph(self, graph: Any) -> None:
        """Replace the underlying graph instance.
        
        Not supported for SQLite backend as it manages persistence.
        """
        raise NotImplementedError("Cannot set native graph for SQLite backend")

    def add_node(self, node_id: str, **attributes: Any) -> None:
        """Add node to graph."""
        node_type = attributes.get("type", "unknown")
        data_json = json.dumps(attributes)
        
        with self.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO nodes (id, type, data) VALUES (?, ?, ?)",
                (node_id, node_type, data_json)
            )

    def add_edge(self, source: str, target: str, **attributes: Any) -> int:
        """Add edge to graph.
        
        Returns:
            int: Edge key (always 0 for simple multigraph emulation in this version, 
                 or incrementing if we query first, but for bulk speed we might use 0)
        """
        edge_kind = attributes.get("kind", "depends_on")
        data_json = json.dumps(attributes)
        
        # Simple key generation: check max key or just use 0 if not exists
        # For true multigraph support, we need to find the next available key
        # This implementation assumes a simple approach: find max key + 1
        
        # Note: In a high-concurrency scenario, this read-then-write might race without strict locking.
        # SQLiteStore uses IMMEDIATE transaction which helps.
        
        with self.transaction() as conn:
            cursor = conn.execute(
                "SELECT MAX(key) FROM edges WHERE source = ? AND target = ?",
                (source, target)
            )
            result = cursor.fetchone()
            next_key = (result[0] + 1) if result and result[0] is not None else 0
            
            conn.execute(
                "INSERT INTO edges (source, target, key, kind, data) VALUES (?, ?, ?, ?, ?)",
                (source, target, next_key, edge_kind, data_json)
            )
            return next_key

    def has_node(self, node_id: str) -> bool:
        """Check if node exists."""
        conn = self._get_conn()
        cursor = conn.execute("SELECT 1 FROM nodes WHERE id = ?", (node_id,))
        return cursor.fetchone() is not None

    def has_edge(self, source: str, target: str, key: Optional[int] = None) -> bool:
        """Check if edge exists."""
        conn = self._get_conn()
        if key is None:
            cursor = conn.execute(
                "SELECT 1 FROM edges WHERE source = ? AND target = ? LIMIT 1",
                (source, target)
            )
        else:
            cursor = conn.execute(
                "SELECT 1 FROM edges WHERE source = ? AND target = ? AND key = ?",
                (source, target, key)
            )
        return cursor.fetchone() is not None

    def get_node_data(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Get node attributes."""
        conn = self._get_conn()
        cursor = conn.execute("SELECT data FROM nodes WHERE id = ?", (node_id,))
        row = cursor.fetchone()
        if row:
            return json.loads(row[0])
        return None

    def get_edge_data(
        self, source: str, target: str, key: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """Get edge attributes."""
        conn = self._get_conn()
        # If key is None, networkx returns a dict of key->attrs for all edges
        if key is None:
            cursor = conn.execute(
                "SELECT key, data FROM edges WHERE source = ? AND target = ?",
                (source, target)
            )
            result = {}
            for row in cursor:
                result[row[0]] = json.loads(row[1])
            return result if result else None
        else:
            cursor = conn.execute(
                "SELECT data FROM edges WHERE source = ? AND target = ? AND key = ?",
                (source, target, key)
            )
            row = cursor.fetchone()
            return json.loads(row[0]) if row else None

    def nodes(self, data: bool = False) -> Iterable:
        """Iterate over nodes."""
        conn = self._get_conn()
        # Use server-side cursor via iterator
        cursor = conn.execute("SELECT id, data FROM nodes")
        for row in cursor:
            if data:
                yield (row[0], json.loads(row[1]))
            else:
                yield row[0]

    def edges(self, data: bool = False, keys: bool = False) -> Iterable:
        """Iterate over edges."""
        conn = self._get_conn()
        cursor = conn.execute("SELECT source, target, key, data FROM edges")
        for row in cursor:
            if keys:
                if data:
                    yield (row[0], row[1], row[2], json.loads(row[3]))
                else:
                    yield (row[0], row[1], row[2])
            else:
                if data:
                    yield (row[0], row[1], json.loads(row[3]))
                else:
                    yield (row[0], row[1])

    def node_count(self) -> int:
        """Get number of nodes."""
        conn = self._get_conn()
        cursor = conn.execute("SELECT COUNT(*) FROM nodes")
        return cursor.fetchone()[0]

    def edge_count(self) -> int:
        """Get number of edges."""
        conn = self._get_conn()
        cursor = conn.execute("SELECT COUNT(*) FROM edges")
        return cursor.fetchone()[0]

    def successors(self, node_id: str) -> Iterable[str]:
        """Get successor nodes."""
        conn = self._get_conn()
        cursor = conn.execute("SELECT DISTINCT target FROM edges WHERE source = ?", (node_id,))
        for row in cursor:
            yield row[0]

    def predecessors(self, node_id: str) -> Iterable[str]:
        """Get predecessor nodes."""
        conn = self._get_conn()
        cursor = conn.execute("SELECT DISTINCT source FROM edges WHERE target = ?", (node_id,))
        for row in cursor:
            yield row[0]

    def in_degree(self, node_id: str) -> int:
        """Get in-degree of node."""
        conn = self._get_conn()
        cursor = conn.execute("SELECT COUNT(*) FROM edges WHERE target = ?", (node_id,))
        return cursor.fetchone()[0]

    def out_degree(self, node_id: str) -> int:
        """Get out-degree of node."""
        conn = self._get_conn()
        cursor = conn.execute("SELECT COUNT(*) FROM edges WHERE source = ?", (node_id,))
        return cursor.fetchone()[0]

    def clear(self) -> None:
        """Clear all nodes and edges."""
        with self.transaction() as conn:
            conn.execute("DELETE FROM edges")
            conn.execute("DELETE FROM nodes")

    def out_edges(
        self, node_id: str, data: bool = False, keys: bool = False
    ) -> Iterable:
        """Get outgoing edges of a node."""
        conn = self._get_conn()
        cursor = conn.execute("SELECT source, target, key, data FROM edges WHERE source = ?", (node_id,))
        for row in cursor:
            if keys:
                if data:
                    yield (row[0], row[1], row[2], json.loads(row[3]))
                else:
                    yield (row[0], row[1], row[2])
            else:
                if data:
                    yield (row[0], row[1], json.loads(row[3]))
                else:
                    yield (row[0], row[1])

    def in_edges(
        self, node_id: str, data: bool = False, keys: bool = False
    ) -> Iterable:
        """Get incoming edges of a node."""
        conn = self._get_conn()
        cursor = conn.execute("SELECT source, target, key, data FROM edges WHERE target = ?", (node_id,))
        for row in cursor:
            if keys:
                if data:
                    yield (row[0], row[1], row[2], json.loads(row[3]))
                else:
                    yield (row[0], row[1], row[2])
            else:
                if data:
                    yield (row[0], row[1], json.loads(row[3]))
                else:
                    yield (row[0], row[1])

    def remove_edge(self, source: str, target: str, key: Optional[int] = None) -> None:
        """Remove an edge from the graph."""
        with self.transaction() as conn:
            if key is None:
                # Remove all edges between source and target if key not specified?
                # NetworkX behavior: remove_edge(u, v) removes ONE edge. If multiple, ambiguous.
                # But remove_edge(u, v) in MultiGraph usually raises error if key not provided and multiple exist.
                # Here we'll delete all for simplicity or just first. 
                # Let's align with common SQLite usage: delete all matching
                conn.execute(
                    "DELETE FROM edges WHERE source = ? AND target = ?",
                    (source, target)
                )
            else:
                conn.execute(
                    "DELETE FROM edges WHERE source = ? AND target = ? AND key = ?",
                    (source, target, key)
                )

    def remove_node(self, node_id: str) -> None:
        """Remove a node from the graph."""
        with self.transaction() as conn:
            conn.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
            # Cascade delete edges
            conn.execute("DELETE FROM edges WHERE source = ? OR target = ?", (node_id, node_id))

    def update_node(self, node_id: str, **attributes: Any) -> None:
        """Update node attributes."""
        # Need to read-modify-write
        with self.transaction() as conn:
            cursor = conn.execute("SELECT data FROM nodes WHERE id = ?", (node_id,))
            row = cursor.fetchone()
            if row:
                data = json.loads(row[0])
                data.update(attributes)
                conn.execute(
                    "UPDATE nodes SET data = ? WHERE id = ?",
                    (json.dumps(data), node_id)
                )

    def subgraph(self, nodes: Iterable[str]) -> Any:
        """Return a subgraph view containing only the specified nodes.
        
        For SQLite, efficient subgraphing is hard without materialization.
        We return a NetworkX subgraph in memory for now.
        """
        import networkx as nx
        sg = nx.MultiDiGraph()
        nodes_set: Set[str] = set(nodes)
        if not nodes_set:
            return sg
        
        # Load nodes
        conn = self._get_conn()
        for n in nodes_set:
            data = self.get_node_data(n)
            if data:
                sg.add_node(n, **data)
        
        # Load edges where both ends are in nodes_set
        placeholders = ",".join("?" for _ in nodes_set)
        query = (
            f"SELECT source, target, key, data FROM edges "
            f"WHERE source IN ({placeholders}) AND target IN ({placeholders})"
        )
        params = tuple(nodes_set) + tuple(nodes_set)
        for src, tgt, key, data_json in conn.execute(query, params):
            edge_attrs = json.loads(data_json) if data_json else {}
            sg.add_edge(src, tgt, key=key, **edge_attrs)
        return sg
