"""Code dependency mapper for Maven Java sources."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from depanalyzer.graph import LinkClass
from depanalyzer.graph import EdgeKind, GraphManager, NodeType
from depanalyzer.graph import EdgeSpec, NodeSpec
from depanalyzer.parsers.base import BaseCodeDependencyMapper
from depanalyzer.runtime.context import TransactionContext


class MavenCodeDependencyMapper(BaseCodeDependencyMapper):
    """Map Java imports and JNI hints to graph edges."""

    NAME: str = "maven_code_mapper"
    ECOSYSTEM: str = "maven"

    def _map_for_file(
        self,
        transaction_ctx: TransactionContext,
        graph: GraphManager,
        file_path: Path,
        parse_result: Dict[str, Any],
    ) -> None:
        file_node_id = _normalize_code_node(graph, file_path, parse_result)
        imports = parse_result.get("imports") or parse_result.get("includes") or []
        imports = [str(val) for val in imports]
        source_roots = _collect_source_roots(graph)

        for imp in imports:
            target_id, target_src = _resolve_import(graph, imp, source_roots)
            if not graph.has_node(target_id):
                graph.add_node_spec(
                    NodeSpec(
                        id=target_id,
                        type=NodeType.CODE,
                        src_path=str(target_src) if target_src else None,
                        name=Path(imp.replace(".", "/")).name,
                        parser_name=parse_result.get("parser_name") or "maven_code",
                        confidence=0.7 if target_src else 0.5,
                    )
                )

            graph.add_edge_spec(
                EdgeSpec(
                    source=file_node_id,
                    target=target_id,
                    kind=EdgeKind.IMPORT,
                    parser_name=parse_result.get("parser_name") or "maven_code",
                    attrs={"link_class": LinkClass.SEMANTIC.value},
                )
            )

        # JNI bridging: register consumer artifacts when System.loadLibrary is present.
        for lib_name in parse_result.get("native_libs", []):
            _register_native_contract(graph, lib_name)


def _normalize_code_node(
    graph: GraphManager, file_path: Path, parse_result: Dict[str, Any]
) -> str:
    try:
        if graph.root_path:
            node_id = graph.normalize_path(file_path)
        else:
            node_id = str(file_path)
    except Exception:
        node_id = str(file_path)

    if not graph.has_node(node_id):
        graph.add_node_spec(
            NodeSpec(
                id=node_id,
                type=NodeType.CODE,
                src_path=str(file_path.resolve()),
                path=str(file_path),
                name=file_path.name,
                parser_name=parse_result.get("parser_name") or "maven_code",
            )
        )
    return node_id


def _collect_source_roots(graph: GraphManager) -> List[Path]:
    roots: List[Path] = []
    for _, attrs in graph.nodes():
        if attrs.get("type") != NodeType.MODULE.value:
            continue
        if attrs.get("parser_name") != "maven":
            continue

        srcs = attrs.get("source_roots") or []
        if not isinstance(srcs, list):
            continue
        for src in srcs:
            try:
                p = Path(src).resolve()
            except OSError:
                continue
            if p not in roots:
                roots.append(p)
    return roots


def _resolve_import(
    graph: GraphManager, import_name: str, source_roots: List[Path]
) -> tuple[str, Optional[Path]]:
    rel_path = Path(import_name.replace(".", "/") + ".java")
    for root in source_roots:
        candidate = (root / rel_path).resolve()
        if candidate.is_file():
            try:
                return graph.normalize_path(candidate), candidate
            except Exception:
                return str(candidate), candidate

    return f"java_import:{import_name}", None


def _register_native_contract(graph: GraphManager, lib_name: str) -> None:
    artifact_id = f"artifact:maven:lib{lib_name}.so"
    if not graph.has_node(artifact_id):
        graph.add_node_spec(
            NodeSpec(
                id=artifact_id,
                type=NodeType.SHARED_LIBRARY,
                label=artifact_id,
                name=f"lib{lib_name}.so",
                parser_name="maven_code",
                confidence=0.6,
                attrs={
                    "origin": "in_repo",
                    "provenance": "jni_load",
                    "linkage_kind": "shared",
                },
            )
        )

    # No provider available yet; GlobalContractLinker will match by artifact_name.
    try:
        from depanalyzer.graph.contract import BuildInterfaceContract, ContractType
        from depanalyzer.graph.contract_registry import ContractRegistry
    except ImportError:
        return

    contract = BuildInterfaceContract(
        artifact_name=f"lib{lib_name}.so",
        provider_artifact="",
        consumer_artifact=artifact_id,
        contract_type=ContractType.ARTIFACT_NAME,
        confidence=0.0,
        evidence=[f"jni_load:{lib_name}"],
        metadata={"lib_name": lib_name},
    )
    ContractRegistry().register(contract)


__all__ = ["MavenCodeDependencyMapper"]
