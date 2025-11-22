"""Operations and algorithms built on top of the graph core."""

from .condensation import CondensationResult, GraphLike, build_condensation_dag
from .global_dag import GlobalDAG
from .merge import merge_graph_with_dependencies
from .projection import derive_asset_artifact_projection, fuse_projection_evidence

__all__ = [
    "CondensationResult",
    "GlobalDAG",
    "GraphLike",
    "build_condensation_dag",
    "derive_asset_artifact_projection",
    "fuse_projection_evidence",
    "merge_graph_with_dependencies",
]
