import networkx as nx

from depanalyzer.analysis.deadcode import DeadcodeAnalyzer
from depanalyzer.graph import GraphManager
from depanalyzer.graph.models.schema import EdgeKind, NodeType
from depanalyzer.graph.ops.merge import _compute_dead_nodes_for_graph


def _build_reachability_fixture():
    root = "hap:app"
    cluster = "scc:cluster"
    proxy = "//ext_lib/ohos_smart_dialog@^1.3.6"
    dep = "//dep/ohpm/ohos.smart.dialog@1.8.8/har/smart_dialog"
    unused = "//unused/file"

    return root, cluster, proxy, dep, unused


def test_deadcode_analyzer_traverses_contains_and_dependencies():
    root, cluster, proxy, dep, unused = _build_reachability_fixture()

    gm = GraphManager()
    gm.add_node(root, NodeType.HAP.value, parser_name="test")
    gm.add_node(cluster, NodeType.CODE_SCC_CLUSTER.value, parser_name="test")
    gm.add_node(proxy, NodeType.PROXY.value, parser_name="test")
    gm.add_node(dep, NodeType.EXTERNAL_LIBRARY.value, parser_name="test")
    gm.add_node(unused, NodeType.CODE.value, parser_name="test")

    gm.add_edge(cluster, root, EdgeKind.CONTAINS.value, parser_name="test", implicit=True)
    gm.add_edge(cluster, proxy, EdgeKind.CONTAINS.value, parser_name="test", implicit=True)
    gm.add_edge(proxy, dep, EdgeKind.DEPENDS_ON.value, parser_name="test")

    dead_nodes = DeadcodeAnalyzer(gm).analyze()

    assert proxy not in dead_nodes
    assert dep not in dead_nodes
    assert cluster not in dead_nodes
    assert root not in dead_nodes
    assert unused in dead_nodes


def test_compute_dead_nodes_for_merged_graph_matches_analyzer_logic():
    root, cluster, proxy, dep, unused = _build_reachability_fixture()

    graph = nx.MultiDiGraph()
    graph.add_node(root, type=NodeType.HAP.value)
    graph.add_node(cluster, type=NodeType.CODE_SCC_CLUSTER.value)
    graph.add_node(proxy, type=NodeType.PROXY.value)
    graph.add_node(dep, type=NodeType.EXTERNAL_LIBRARY.value)
    graph.add_node(unused, type=NodeType.CODE.value)

    graph.add_edge(cluster, root, kind=EdgeKind.CONTAINS.value, implicit=True)
    graph.add_edge(cluster, proxy, kind=EdgeKind.CONTAINS.value, implicit=True)
    graph.add_edge(proxy, dep, kind=EdgeKind.DEPENDS_ON.value)

    dead_nodes = _compute_dead_nodes_for_graph(graph)

    assert proxy not in dead_nodes
    assert dep not in dead_nodes
    assert cluster not in dead_nodes
    assert root not in dead_nodes
    assert unused in dead_nodes
