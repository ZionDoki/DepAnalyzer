
import json
import argparse
import sys
import networkx as nx
from rich.console import Console
from rich.tree import Tree
from rich.panel import Panel
from rich.text import Text
from rich.prompt import Prompt
from rich.table import Table
from typing import List, Optional, Dict, Any

console = Console()

class GraphExplorer:
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.graph = nx.DiGraph()
        self.nodes_data: Dict[str, Dict[str, Any]] = {}
        self.load_graph()

    def load_graph(self):
        with console.status(f"[bold green]Loading graph from {self.file_path}..."):
            try:
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                # Handle different JSON structures
                nodes_list = data.get('nodes', [])
                
                # Check for links/edges
                if 'links' in data:
                    edges_list = data['links']
                elif 'edges' in data:
                    edges_list = data['edges']
                elif 'graph' in data and 'edges' in data['graph']:
                     # Sometimes nested in 'graph' key if networkx exported
                     edges_list = data['graph']['edges']
                     nodes_list = data['graph'].get('nodes', nodes_list)
                else:
                    edges_list = []

                # Build Graph
                for node in nodes_list:
                    nid = node['id']
                    self.nodes_data[nid] = node
                    self.graph.add_node(nid, **node)

                for edge in edges_list:
                    src = edge.get('source')
                    dst = edge.get('target')
                    if src and dst:
                        self.graph.add_edge(src, dst)
                
                console.print(f"[bold green]Successfully loaded {self.graph.number_of_nodes()} nodes and {self.graph.number_of_edges()} edges.[/bold green]")

            except Exception as e:
                console.print(f"[bold red]Error loading file:[/bold red] {e}")
                sys.exit(1)

    def search_nodes(self, query: str) -> List[str]:
        """Fuzzy search for nodes."""
        query = query.lower()
        matches = []
        for nid, data in self.nodes_data.items():
            # Search in ID, label, or name
            search_text = f"{nid} {data.get('label', '')} {data.get('name', '')}".lower()
            if query in search_text:
                matches.append(nid)
        return matches

    def find_path(self, start: str, end: str) -> Optional[List[str]]:
        """Find shortest path from start to end."""
        try:
            return nx.shortest_path(self.graph, start, end)
        except nx.NetworkXNoPath:
            return None
        except nx.NodeNotFound:
            return None

    def find_common_ancestor_connection(self, node1: str, node2: str):
        """Finds the lowest common ancestor and paths to both nodes."""
        ancestors1 = nx.ancestors(self.graph, node1)
        ancestors1.add(node1)
        ancestors2 = nx.ancestors(self.graph, node2)
        ancestors2.add(node2)

        common = ancestors1.intersection(ancestors2)
        if not common:
            return None

        # Find the "lowest" common ancestor (closest to the nodes)
        # We define "lowest" as a node in common set that has no children in the common set
        # (i.e. topologically lowest)
        # Actually, a better heuristic for visualization is the node that minimizes 
        # distance(LCA, n1) + distance(LCA, n2)
        
        best_lca = None
        min_dist = float('inf')
        
        for ancestor in common:
            try:
                # calculating shortest path might be expensive for all, but graph likely not huge
                l1 = nx.shortest_path_length(self.graph, ancestor, node1)
                l2 = nx.shortest_path_length(self.graph, ancestor, node2)
                if l1 + l2 < min_dist:
                    min_dist = l1 + l2
                    best_lca = ancestor
            except nx.NetworkXNoPath:
                continue
        
        if best_lca:
            path1 = nx.shortest_path(self.graph, best_lca, node1)
            path2 = nx.shortest_path(self.graph, best_lca, node2)
            return best_lca, path1, path2
        
        return None

    def get_node_label(self, nid: str) -> str:
        data = self.nodes_data.get(nid, {})
        # Use label if available, else ID. 
        # Truncate if too long for display
        lbl = data.get('label') or nid
        if len(lbl) > 60:
            lbl = "..." + lbl[-57:]
        return lbl

def draw_path_tree(explorer: GraphExplorer, root_path: List[str], branch_path: List[str] = None, root_is_common_ancestor=False):
    """
    Draws a tree.
    If root_is_common_ancestor is True:
       Root -> ... -> Node1
            -> ... -> Node2
    """
    root_id = root_path[0]
    root_label = f"[bold cyan]{explorer.get_node_label(root_id)}[/bold cyan]"
    
    tree = Tree(root_label)

    if root_is_common_ancestor and branch_path:
        # Scenario: Common Ancestor splits to two nodes
        # root_path is path to Node 1 (excluding root itself in iteration)
        # branch_path is path to Node 2 (excluding root itself in iteration)
        
        # Branch 1
        current_branch = tree
        for i, node in enumerate(root_path[1:]):
            label = explorer.get_node_label(node)
            if i == len(root_path) - 2: # Leaf of this path
                label = f"[bold yellow]{label} (Target 1)[/bold yellow]"
            current_branch = current_branch.add(label)
            
        # Branch 2
        current_branch = tree
        for i, node in enumerate(branch_path[1:]):
            label = explorer.get_node_label(node)
            if i == len(branch_path) - 2: # Leaf of this path
                label = f"[bold yellow]{label} (Target 2)[/bold yellow]"
            current_branch = current_branch.add(label)
            
    else:
        # Linear path
        current_branch = tree
        for i, node in enumerate(root_path[1:]):
            label = explorer.get_node_label(node)
            if i == len(root_path) - 2:
                label = f"[bold yellow]{label} (End)[/bold yellow]"
            current_branch = current_branch.add(label)

    console.print(tree)

def select_node_interactive(explorer: GraphExplorer, query: str) -> Optional[str]:
    matches = explorer.search_nodes(query)
    if not matches:
        console.print(f"[red]No nodes found matching '{query}'[/red]")
        return None
    
    if len(matches) == 1:
        return matches[0]
    
    # Limit display
    limit = 10
    table = Table(title=f"Found {len(matches)} matches (showing top {limit})")
    table.add_column("Index", justify="right", style="cyan", no_wrap=True)
    table.add_column("ID", style="magenta")
    table.add_column("Label", style="green")

    for i, nid in enumerate(matches[:limit]):
        data = explorer.nodes_data.get(nid, {})
        label = data.get('label', '')
        table.add_row(str(i), nid, label)
    
    console.print(table)
    if len(matches) > limit:
        console.print(f"... and {len(matches) - limit} more.")

    while True:
        selection = Prompt.ask("Select a node by Index (or 'c' to cancel)")
        if selection.lower() == 'c':
            return None
        if selection.isdigit() and 0 <= int(selection) < len(matches):
            return matches[int(selection)]
        console.print("[red]Invalid selection.[/red]")

def main():
    parser = argparse.ArgumentParser(description="Interactive Dependency Graph Explorer")
    parser.add_argument("result_path", help="Path to the results directory or specific JSON file")
    args = parser.parse_args()

    file_path = args.result_path
    # If directory, try default name
    if not file_path.endswith('.json'):
        import os
        possible = os.path.join(file_path, 'compatibility_graph.json')
        if os.path.exists(possible):
            file_path = possible
        else:
            console.print(f"[red]Could not find JSON file in {file_path}. Please specify full path.[/red]")
            return

    explorer = GraphExplorer(file_path)

    console.print(Panel.fit(
        "[bold yellow]Graph Explorer CLI[/bold yellow]\n\n"
        "Commands:\n"
        "  [cyan]path <query1> <query2>[/cyan] : Show connection between two nodes\n"
        "  [cyan]info <query>[/cyan]           : Show details of a node\n"
        "  [cyan]help[/cyan]                   : Show help\n"
        "  [cyan]quit[/cyan] or [cyan]exit[/cyan]       : Exit",
        title="Welcome"
    ))

    while True:
        try:
            user_input = Prompt.ask("\n[bold blue]> [/bold blue]")
            parts = user_input.strip().split()
            if not parts:
                continue
            
            cmd = parts[0].lower()

            if cmd in ['quit', 'exit']:
                break
            
            elif cmd == 'help':
                 console.print("Commands: path, info, quit")

            elif cmd == 'info':
                if len(parts) < 2:
                    console.print("[red]Usage: info <search_term>[/red]")
                    continue
                query = " ".join(parts[1:])
                nid = select_node_interactive(explorer, query)
                if nid:
                    console.print(Panel(json.dumps(explorer.nodes_data[nid], indent=2), title=nid))

            elif cmd == 'path':
                # This one is tricky because of two arguments which might contain spaces
                # Let's assume arguments are separated by a literal pipe '|' or we ask interactively if not distinct
                if '|' in user_input:
                    # syntax: path query1 | query2
                    _, queries = user_input.split(maxsplit=1)
                    q1, q2 = queries.split('|')
                else:
                    # Interactive mode
                    q1 = Prompt.ask("Enter search term for [bold green]Node A[/bold green]")
                    q2 = Prompt.ask("Enter search term for [bold green]Node B[/bold green]")

                node_a = select_node_interactive(explorer, q1.strip())
                if not node_a: continue
                
                node_b = select_node_interactive(explorer, q2.strip())
                if not node_b: continue

                console.print(f"\nAnalyzing connection between:\n A: [green]{node_a}[/green]\n B: [green]{node_b}[/green]\n")

                # 1. Check direct path A -> B
                path_ab = explorer.find_path(node_a, node_b)
                if path_ab:
                    console.print("[bold green]Found path from A to B:[/bold green]")
                    draw_path_tree(explorer, path_ab)
                    continue

                # 2. Check direct path B -> A
                path_ba = explorer.find_path(node_b, node_a)
                if path_ba:
                    console.print("[bold green]Found path from B to A:[/bold green]")
                    draw_path_tree(explorer, path_ba)
                    continue

                # 3. Check Common Ancestor
                result = explorer.find_common_ancestor_connection(node_a, node_b)
                if result:
                    lca, p1, p2 = result
                    console.print(f"[bold yellow]Nodes share a common ancestor: {explorer.get_node_label(lca)}[/bold yellow]")
                    draw_path_tree(explorer, p1, p2, root_is_common_ancestor=True)
                else:
                    console.print("[red]No connection found (neither direct path nor common ancestor).[/red]")

        except KeyboardInterrupt:
            break
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

if __name__ == "__main__":
    main()
