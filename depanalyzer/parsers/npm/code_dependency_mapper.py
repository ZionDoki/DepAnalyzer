"""NPM code dependency mapper.

Maps parsed JavaScript/TypeScript import/require statements to graph edges,
connecting code nodes to their dependencies.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from depanalyzer.graph import EdgeKind, GraphManager, NodeType
from depanalyzer.graph.models.identifiers import normalize_node_id
from depanalyzer.graph.models.linking import LinkClass
from depanalyzer.parsers.base import BaseCodeDependencyMapper
from depanalyzer.runtime.context import TransactionContext

logger = logging.getLogger("depanalyzer.parsers.npm.code_dependency_mapper")


class NpmCodeDependencyMapper(BaseCodeDependencyMapper):
    """Maps npm code dependencies to graph edges.

    Handles:
    - Relative imports (./foo, ../bar)
    - Package imports (lodash, @scope/package)
    - Node.js built-in modules (fs, path, etc.)
    """

    NAME = "npm_code_mapper"
    ECOSYSTEM = "npm"

    # Node.js built-in modules
    BUILTIN_MODULES = {
        "assert",
        "async_hooks",
        "buffer",
        "child_process",
        "cluster",
        "console",
        "constants",
        "crypto",
        "dgram",
        "dns",
        "domain",
        "events",
        "fs",
        "http",
        "http2",
        "https",
        "inspector",
        "module",
        "net",
        "os",
        "path",
        "perf_hooks",
        "process",
        "punycode",
        "querystring",
        "readline",
        "repl",
        "stream",
        "string_decoder",
        "sys",
        "timers",
        "tls",
        "trace_events",
        "tty",
        "url",
        "util",
        "v8",
        "vm",
        "wasi",
        "worker_threads",
        "zlib",
    }

    def _map_for_file(
        self,
        transaction_ctx: TransactionContext,
        graph: GraphManager,
        file_path: Path,
        parse_result: Dict[str, Any],
    ) -> None:
        """Map imports from a parsed JavaScript/TypeScript file.

        Args:
            transaction_ctx: Transaction context.
            graph: Graph manager instance.
            file_path: Path to the parsed source file.
            parse_result: Parse result from NpmCodeParser.
        """
        imports = parse_result.get("imports", [])
        exports = parse_result.get("exports", [])

        if not imports and not exports:
            return

        # Get or create source code node
        workspace_root = transaction_ctx.workspace_root
        source_id = normalize_node_id(file_path, workspace_root)

        if not graph.has_node(source_id):
            graph.add_node(
                source_id,
                NodeType.CODE.value,
                parser_name="npm_code_mapper",
                src_path=str(file_path),
                ecosystem=self.ECOSYSTEM,
                origin="in_repo",
            )

        # Process imports
        for import_spec in imports:
            self._map_import(
                graph,
                source_id,
                import_spec,
                file_path,
                workspace_root,
            )

        # Process re-exports (export * from 'module')
        for export_spec in exports:
            self._map_import(
                graph,
                source_id,
                export_spec,
                file_path,
                workspace_root,
                is_reexport=True,
            )

        logger.debug(
            "NpmCodeDependencyMapper: mapped %d imports and %d exports for %s",
            len(imports),
            len(exports),
            file_path,
        )

    def _map_import(
        self,
        graph: GraphManager,
        source_id: str,
        import_spec: str,
        file_path: Path,
        workspace_root: Path,
        is_reexport: bool = False,
    ) -> None:
        """Map a single import to a graph edge.

        Args:
            graph: Graph manager instance.
            source_id: Source code node ID.
            import_spec: Import specifier string.
            file_path: Path to the importing file.
            workspace_root: Workspace root path.
            is_reexport: Whether this is a re-export.
        """
        # Skip empty imports
        if not import_spec:
            return

        # Determine import type
        if import_spec.startswith("."):
            # Relative import
            target_id = self._resolve_relative_import(
                import_spec, file_path, workspace_root, graph
            )
        elif import_spec.startswith("node:") or import_spec in self.BUILTIN_MODULES:
            # Node.js built-in module
            module_name = import_spec.replace("node:", "")
            target_id = f"//system:node/{module_name}"
            if not graph.has_node(target_id):
                graph.add_node(
                    target_id,
                    NodeType.EXTERNAL_LIBRARY.value,
                    parser_name="npm_code_mapper",
                    name=module_name,
                    ecosystem="node",
                    origin="system",
                    is_builtin=True,
                )
        else:
            # Package import
            target_id = self._resolve_package_import(import_spec, graph)

        if not target_id:
            return

        # Create edge
        edge_kind = EdgeKind.DEPENDS_ON.value
        graph.add_edge(
            source_id,
            target_id,
            edge_kind=edge_kind,
            parser_name="npm_code_mapper",
            link_class=LinkClass.SEMANTIC.value,
            import_spec=import_spec,
            is_reexport=is_reexport,
        )

    def _resolve_relative_import(
        self,
        import_spec: str,
        file_path: Path,
        workspace_root: Path,
        graph: GraphManager,
    ) -> Optional[str]:
        """Resolve a relative import to a node ID.

        Args:
            import_spec: Relative import specifier (./foo, ../bar).
            file_path: Path to the importing file.
            workspace_root: Workspace root path.
            graph: Graph manager instance.

        Returns:
            Optional[str]: Target node ID or None.
        """
        # Resolve relative path
        base_dir = file_path.parent
        target_path = (base_dir / import_spec).resolve()

        # Try common extensions
        extensions = [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ""]
        resolved_path = None

        for ext in extensions:
            candidate = target_path.with_suffix(ext) if ext else target_path
            if candidate.exists() and candidate.is_file():
                resolved_path = candidate
                break

            # Try index file
            index_candidate = target_path / f"index{ext}" if ext else target_path / "index"
            if index_candidate.exists() and index_candidate.is_file():
                resolved_path = index_candidate
                break

        if resolved_path:
            target_id = normalize_node_id(resolved_path, workspace_root)

            # Ensure target node exists
            if not graph.has_node(target_id):
                graph.add_node(
                    target_id,
                    NodeType.CODE.value,
                    parser_name="npm_code_mapper",
                    src_path=str(resolved_path),
                    ecosystem=self.ECOSYSTEM,
                    origin="in_repo",
                )

            return target_id

        # Create placeholder for unresolved import
        placeholder_id = f"//include:{import_spec}"
        if not graph.has_node(placeholder_id):
            graph.add_node(
                placeholder_id,
                NodeType.CODE.value,
                parser_name="npm_code_mapper",
                name=import_spec,
                ecosystem=self.ECOSYSTEM,
                origin="unresolved",
                is_placeholder=True,
            )

        return placeholder_id

    def _resolve_package_import(
        self,
        import_spec: str,
        graph: GraphManager,
    ) -> str:
        """Resolve a package import to a node ID.

        Args:
            import_spec: Package import specifier (lodash, @scope/pkg).
            graph: Graph manager instance.

        Returns:
            str: Target node ID.
        """
        # Extract package name (handle subpath imports like lodash/get)
        parts = import_spec.split("/")

        if import_spec.startswith("@"):
            # Scoped package: @scope/package or @scope/package/subpath
            if len(parts) >= 2:
                package_name = f"{parts[0]}/{parts[1]}"
            else:
                package_name = import_spec
        else:
            # Regular package: package or package/subpath
            package_name = parts[0]

        # Create external dependency node
        target_id = f"//external:npm/{package_name}"

        if not graph.has_node(target_id):
            graph.add_node(
                target_id,
                NodeType.EXTERNAL_DEP.value,
                parser_name="npm_code_mapper",
                name=package_name,
                ecosystem=self.ECOSYSTEM,
                origin="external",
            )

        return target_id
