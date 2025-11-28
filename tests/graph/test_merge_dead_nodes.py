import networkx as nx

from depanalyzer.graph.ops.merge import _filter_dead_nodes_to_main_graph


def test_filter_dead_nodes_keeps_main_graph_only():
    merged = nx.MultiDiGraph()
    main_graph_id = "main"

    main_root = "hap:app"
    main_unused = "//unused"
    dep_unused = "//dep/ns/path"

    merged.add_node(main_root, graph_id=main_graph_id)
    merged.add_node(main_unused, graph_id=main_graph_id)
    merged.add_node(dep_unused, graph_id="dep_graph")

    dead = [main_unused, dep_unused]

    filtered = _filter_dead_nodes_to_main_graph(dead, merged, main_graph_id)

    assert filtered == [main_unused]
