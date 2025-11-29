import json
import sys
import argparse
import networkx as nx
import pprint
from typing import List, Optional, Dict, Any, Tuple

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Header, Footer, Input, Button, Label, OptionList, RichLog, LoadingIndicator, Static, Tree, TextArea
from textual.screen import ModalScreen
from textual.worker import Worker, get_current_worker
from textual import on, events

# --- Core Graph Logic ---

class GraphLogic:
    def __init__(self):
        self.graph = nx.DiGraph()
        self.nodes_data: Dict[str, Dict[str, Any]] = {}
        self.loaded = False

    def load_file(self, file_path: str):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            nodes_list = data.get('nodes', [])
            if 'links' in data:
                edges_list = data['links']
            elif 'edges' in data:
                edges_list = data['edges']
            elif 'graph' in data and 'edges' in data['graph']:
                 edges_list = data['graph']['edges']
                 nodes_list = data['graph'].get('nodes', nodes_list)
            else:
                edges_list = []

            for node in nodes_list:
                nid = node['id']
                self.nodes_data[nid] = node
                self.graph.add_node(nid, **node)

            for edge in edges_list:
                src = edge.get('source')
                dst = edge.get('target')
                if src and dst:
                    self.graph.add_edge(src, dst)
            
            self.loaded = True
            return True, f"Loaded {self.graph.number_of_nodes()} nodes, {self.graph.number_of_edges()} edges."
        except Exception as e:
            return False, str(e)

    def search_nodes(self, query: str, limit: int = 50) -> List[Tuple[str, str]]:
        """Returns list of (id, label)"""
        query = query.lower()
        matches = []
        for nid, data in self.nodes_data.items():
            label = data.get('label', nid)
            # Search in ID, label, or name
            search_text = f"{nid} {label} {data.get('name', '')}".lower()
            if query in search_text:
                matches.append((nid, label))
                if len(matches) >= limit:
                    break
        return matches

    def get_node_display(self, nid: str) -> str:
        data = self.nodes_data.get(nid, {})
        label = data.get('label') or nid
        # Add type if available
        ntype = data.get('type', '')
        if ntype:
            return f"[{ntype}] {label}"
        return label

    def find_path(self, start: str, end: str):
        try:
            return nx.shortest_path(self.graph, start, end)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

    def find_common_ancestor_connection(self, node1: str, node2: str):
        try:
            ancestors1 = nx.ancestors(self.graph, node1)
            ancestors1.add(node1)
            ancestors2 = nx.ancestors(self.graph, node2)
            ancestors2.add(node2)
        except nx.NetworkXError:
            return None

        common = ancestors1.intersection(ancestors2)
        if not common:
            return None

        best_lca = None
        min_dist = float('inf')
        
        for ancestor in common:
            try:
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

# --- TUI Screens ---

class NodeSearchScreen(ModalScreen[str]):
    """A modal screen to search and select a node."""
    
    CSS = """
    NodeSearchScreen {
        align: center middle;
    }

    #dialog {
        grid-size: 1 2;
        grid-rows: auto 1fr;
        padding: 0 1;
        width: 80%;
        height: 80%;
        border: thick $background 80%;
        background: $surface;
    }

    #search_input {
        margin: 1;
    }

    OptionList {
        border: solid $secondary;
        margin: 1;
    }
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, logic: GraphLogic, title: str):
        super().__init__()
        self.logic = logic
        self.dialog_title = title
        self.candidates = []

    def compose(self) -> ComposeResult:
        yield Container(
            Label(self.dialog_title, classes="title"),
            Input(placeholder="Type to search (ID, Label, Name)...", id="search_input"),
            OptionList(id="results_list"),
            id="dialog"
        )

    def on_mount(self):
        self.query_one("#search_input").focus()
        self.update_list("")

    def action_cancel(self):
        self.dismiss(None)

    @on(Input.Changed, "#search_input")
    def on_input_changed(self, event: Input.Changed):
        self.update_list(event.value)

    def update_list(self, query: str):
        option_list = self.query_one(OptionList)
        option_list.clear_options()
        
        self.candidates = self.logic.search_nodes(query, limit=100)
        
        if not self.candidates:
            option_list.add_option("No matches found")
            option_list.disabled = True
        else:
            option_list.disabled = False
            options = []
            for nid, label in self.candidates:
                display_str = f"{label} ({nid})"
                options.append(display_str)
            option_list.add_options(options)

    @on(OptionList.OptionSelected, "#results_list")
    def on_option_selected(self, event: OptionList.OptionSelected):
        if self.candidates and event.option_index < len(self.candidates):
            selected_id = self.candidates[event.option_index][0]
            self.dismiss(selected_id)

# --- Main App ---

class GraphExplorerApp(App):
    CSS = """
    Screen {
        align: center middle;
    }

    #control_panel {
        dock: top;
        height: auto;
        background: $panel;
        padding: 1;
        border-bottom: solid $primary;
    }

    .node_selector {
        margin: 1;
        height: auto;
    }
    
    .node_selector Label {
        width: 10;
        content-align: right middle;
    }
    
    .node_selector Button {
        width: 1fr;
    }

    #trace_btn {
        width: 100%;
        margin: 1 2;
    }

    #main_content {
        width: 100%;
        height: 1fr;
        layout: horizontal;
    }

    #path_tree {
        width: 50%;
        height: 100%;
        border: solid $secondary;
    }

    #node_details {
        width: 50%;
        height: 100%;
        border: solid $secondary;
    }

    #status_bar {
        dock: bottom;
        height: 1;
        background: $primary;
        color: $text;
    }
    """

    def __init__(self, file_path: str):
        super().__init__()
        self.file_path = file_path
        self.logic = GraphLogic()
        self.node_a: Optional[str] = None
        self.node_b: Optional[str] = None

    def compose(self) -> ComposeResult:
        yield Header()
        
        with Container(id="control_panel"):
            with Horizontal(classes="node_selector"):
                yield Label("Start Node:")
                yield Button("Click to select Node A", id="btn_node_a")
            
            with Horizontal(classes="node_selector"):
                yield Label("End Node:")
                yield Button("Click to select Node B", id="btn_node_b")
            
            yield Button("Trace Path", id="trace_btn", disabled=True, variant="primary")

        with Container(id="main_content"):
            yield Tree("Path Results", id="path_tree")
            yield TextArea("", id="node_details", read_only=True)

        yield Static("Ready", id="status_bar")

    def on_mount(self):
        self.run_worker(self.load_data, thread=True)

    def load_data(self):
        self.query_one("#status_bar").update("Loading graph data...")
        success, msg = self.logic.load_file(self.file_path)
        if success:
            self.call_from_thread(self.query_one("#status_bar").update, f"Ready. {msg}")
        else:
            self.call_from_thread(self.query_one("#status_bar").update, f"Error: {msg}")

    @on(Button.Pressed, "#btn_node_a")
    def select_node_a(self):
        if not self.logic.loaded: return
        def set_node(result: Optional[str]):
            if result:
                self.node_a = result
                display = self.logic.get_node_display(result)
                self.query_one("#btn_node_a").label = display
                self.check_can_trace()
        self.push_screen(NodeSearchScreen(self.logic, "Select Start Node"), set_node)

    @on(Button.Pressed, "#btn_node_b")
    def select_node_b(self):
        if not self.logic.loaded: return
        def set_node(result: Optional[str]):
            if result:
                self.node_b = result
                display = self.logic.get_node_display(result)
                self.query_one("#btn_node_b").label = display
                self.check_can_trace()
        self.push_screen(NodeSearchScreen(self.logic, "Select End Node"), set_node)

    def check_can_trace(self):
        if self.node_a and self.node_b:
            self.query_one("#trace_btn").disabled = False

    @on(Button.Pressed, "#trace_btn")
    def trace_path(self):
        if not self.node_a or not self.node_b:
            return
        
        self.query_one("#status_bar").update(f"Tracing connection between A and B...")
        
        # Check Direct Paths
        path_ab = self.logic.find_path(self.node_a, self.node_b)
        if path_ab:
            self.build_textual_tree("Direct Path (A -> B)", path_ab)
            return

        path_ba = self.logic.find_path(self.node_b, self.node_a)
        if path_ba:
            self.build_textual_tree("Direct Path (B -> A)", path_ba)
            return

        # Check Common Ancestor
        result = self.logic.find_common_ancestor_connection(self.node_a, self.node_b)
        if result:
            lca, path1, path2 = result
            self.build_split_textual_tree(lca, path1, path2)
        else:
             tree = self.query_one("#path_tree", Tree)
             tree.clear()
             tree.root.label = "[bold red]No connection found[/bold red]"

    def build_textual_tree(self, title: str, path: List[str]):
        tree = self.query_one("#path_tree", Tree)
        tree.clear()
        tree.root.expand()
        tree.root.label = title
        
        current_node = tree.root
        for i, node_id in enumerate(path):
            label = self.logic.get_node_display(node_id)
            # If last node
            if i == len(path) - 1:
                label = f"[bold yellow]{label} (End)[/bold yellow]"
            
            # Add child to current node
            new_node = current_node.add(label, data=node_id)
            new_node.expand()
            current_node = new_node

    def build_split_textual_tree(self, lca: str, path_to_a: List[str], path_to_b: List[str]):
        tree = self.query_one("#path_tree", Tree)
        tree.clear()
        tree.root.expand()
        
        lca_label = self.logic.get_node_display(lca)
        tree.root.label = f"[bold magenta]Common Ancestor: {lca_label}[/bold magenta]"
        tree.root.data = lca

        # Branch A
        branch_a = tree.root.add("[bold]Path to A[/bold]")
        branch_a.expand()
        curr = branch_a
        # path_to_a includes LCA at [0], skip it
        for i, node_id in enumerate(path_to_a[1:]):
            label = self.logic.get_node_display(node_id)
            if i == len(path_to_a) - 2:
                label = f"[bold yellow]{label} (Target A)[/bold yellow]"
            child = curr.add(label, data=node_id)
            child.expand()
            curr = child

        # Branch B
        branch_b = tree.root.add("[bold]Path to B[/bold]")
        branch_b.expand()
        curr = branch_b
        for i, node_id in enumerate(path_to_b[1:]):
            label = self.logic.get_node_display(node_id)
            if i == len(path_to_b) - 2:
                label = f"[bold yellow]{label} (Target B)[/bold yellow]"
            child = curr.add(label, data=node_id)
            child.expand()
            curr = child

    @on(Tree.NodeSelected, "#path_tree")
    def on_tree_node_selected(self, event: Tree.NodeSelected):
        node_id = event.node.data
        if node_id:
            data = self.logic.nodes_data.get(node_id, {})
            text_widget = self.query_one("#node_details", TextArea)
            # Format data nicely
            formatted_text = pprint.pformat(data, indent=2, width=60)
            text_widget.text = formatted_text

def main():
    parser = argparse.ArgumentParser(description="Textual Graph Explorer")
    parser.add_argument("result_path", help="Path to JSON file")
    args = parser.parse_args()
    
    app = GraphExplorerApp(args.result_path)
    app.run()

if __name__ == "__main__":
    main()