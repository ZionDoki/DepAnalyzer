"""Maven POM parser building graph nodes and dependency specs."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from depanalyzer.parsers.base import BaseParser, DependencySpec
from depanalyzer.graph import GraphManager, NodeType, EdgeKind
from depanalyzer.graph import NodeSpec, EdgeSpec
from depanalyzer.runtime.eventbus import Event, EventType

logger = logging.getLogger("depanalyzer.parsers.maven.config_parser")


class MavenParser(BaseParser):
    """Parse Maven pom.xml files and emit unified graph facts."""

    NAME = "maven"

    def __init__(
        self,
        workspace_root: Path,
        graph_manager: GraphManager,
        eventbus,
        config: Any | None = None,
    ) -> None:
        super().__init__(workspace_root, graph_manager, eventbus, config=config)
        self._last_source_roots: List[Path] = []
        self._last_module_root: Optional[Path] = None
        self._last_module_id: Optional[str] = None

    def parse(self, target_path: Path) -> None:
        """Parse a pom.xml file."""
        try:
            tree = ET.parse(target_path)
        except (OSError, ET.ParseError) as exc:
            logger.warning("Failed to parse %s: %s", target_path, exc)
            return

        root = tree.getroot()
        ns = _detect_namespace(root)

        gav = _extract_gav(root, ns)
        group_id, artifact_id, version = gav

        packaging = _find_text(root, "packaging", ns) or "jar"
        module_id = f"module:maven:{group_id}:{artifact_id}"
        artifact_suffix = f"{group_id}:{artifact_id}"
        node_label = artifact_suffix
        self._last_module_id = module_id

        module_spec = NodeSpec(
            id=module_id,
            type=NodeType.MODULE,
            label=node_label,
            src_path=str(target_path.parent.resolve()),
            name=artifact_id,
            parser_name=self.NAME,
            confidence=1.0,
            attrs={
                "group_id": group_id,
                "artifact_id": artifact_id,
                "version": version,
                "packaging": packaging,
                "origin": "in_repo",
                "provenance": "maven_pom",
            },
        )
        self.graph_manager.add_node_spec(module_spec)

        try:
            config_id = str(target_path.relative_to(self.workspace_root))
        except ValueError:
            config_id = str(target_path)
        config_spec = NodeSpec(
            id=config_id,
            type=NodeType.CONFIG,
            label=config_id,
            src_path=str(target_path.resolve()),
            name=target_path.name,
            parser_name=self.NAME,
            confidence=1.0,
        )
        self.graph_manager.add_node_spec(config_spec)
        self.graph_manager.add_edge_spec(
            EdgeSpec(
                source=module_id,
                target=config_id,
                kind=EdgeKind.DEFINED_BY,
                parser_name=self.NAME,
            )
        )

        self._last_module_root = target_path.parent.resolve()
        self._last_source_roots = self._collect_source_roots(root, ns, target_path.parent)
        if self._last_source_roots:
            existing = module_spec.attrs.get("source_roots", [])
            module_spec.attrs["source_roots"] = list(
                {str(p) for p in self._last_source_roots}.union(
                    set(existing if isinstance(existing, list) else [])
                )
            )
            self.graph_manager.update_node_attribute(
                module_id, "source_roots", module_spec.attrs["source_roots"]
            )

        artifact_id_str = f"artifact:maven:{artifact_suffix}:{packaging}"
        artifact_path = (
            target_path.parent / "target" / f"{artifact_id}-{version}.{packaging}"
            if version
            else target_path.parent / "target" / f"{artifact_id}.{packaging}"
        )
        artifact_spec = NodeSpec(
            id=artifact_id_str,
            type=NodeType.TARGET,
            label=artifact_id_str,
            src_path=str(artifact_path),
            name=f"{artifact_id}.{packaging}",
            parser_name=self.NAME,
            confidence=0.9,
            attrs={
                "group_id": group_id,
                "artifact_id": artifact_id,
                "version": version,
                "packaging": packaging,
                "target_type": packaging,
                "origin": "in_repo",
                "provenance": "maven_pom",
            },
        )
        self.graph_manager.add_node_spec(artifact_spec)
        self.graph_manager.add_edge_spec(
            EdgeSpec(
                source=module_id,
                target=artifact_id_str,
                kind=EdgeKind.PRODUCES,
                parser_name=self.NAME,
            )
        )

        self._process_modules(root, ns, module_id, target_path)
        self._process_dependencies(root, ns, module_id, target_path, packaging)

        try:
            module_root_rel = str(target_path.parent.relative_to(self.workspace_root))
        except ValueError:
            module_root_rel = str(target_path.parent)

        event = Event(
            event_type=EventType.MODULE_PARSED,
            source=self.NAME,
            data={
                "module_id": module_id,
                "module_name": artifact_id,
                "module_root": module_root_rel,
                "declared_via": "pom.xml",
            },
        )
        self.publish_parse_event(event)

    def discover_code_files(self) -> List[Path]:
        """Discover Java source files for the last parsed module."""
        code_files: List[Path] = []
        visited: Set[Path] = set()

        for root in self._last_source_roots:
            if not root.exists() or not root.is_dir():
                continue
            try:
                for path in root.rglob("*.java"):
                    if path.is_file():
                        resolved = path.resolve()
                        if resolved not in visited:
                            visited.add(resolved)
                            code_files.append(resolved)
            except (OSError, ValueError) as exc:
                logger.debug("Failed scanning %s: %s", root, exc)

        return code_files

    def _process_modules(
        self, xml_root: ET.Element, ns: str, parent_module_id: str, target_path: Path
    ) -> None:
        modules = []
        for mod in xml_root.findall(_ns_tag("modules/module", ns)):
            if mod.text:
                modules.append(mod.text.strip())

        for module_rel in modules:
            child_pom = (target_path.parent / module_rel / "pom.xml").resolve()
            child_group = None
            child_artifact = module_rel
            if child_pom.exists():
                try:
                    child_tree = ET.parse(child_pom)
                    child_root = child_tree.getroot()
                    child_ns = _detect_namespace(child_root)
                    child_group, child_artifact, _child_version = _extract_gav(
                        child_root, child_ns
                    )
                except (OSError, ET.ParseError):
                    pass

            module_suffix = (
                f"{child_group}:{child_artifact}"
                if child_group
                else child_artifact
            )
            child_id = f"module:maven:{module_suffix}"

            if not self.graph_manager.has_node(child_id):
                child_spec = NodeSpec(
                    id=child_id,
                    type=NodeType.MODULE,
                    label=module_suffix,
                    src_path=str(child_pom.parent),
                    name=child_artifact,
                    parser_name=self.NAME,
                    confidence=0.7,
                    attrs={
                        "origin": "in_repo",
                        "provenance": "maven_modules",
                        "parent_module": parent_module_id,
                    },
                )
                self.graph_manager.add_node_spec(child_spec)

            self.graph_manager.add_edge_spec(
                EdgeSpec(
                    source=parent_module_id,
                    target=child_id,
                    kind=EdgeKind.CONTAINS,
                    parser_name=self.NAME,
                )
            )

    def _process_dependencies(
        self,
        xml_root: ET.Element,
        ns: str,
        module_id: str,
        pom_path: Path,
        packaging: str,
    ) -> None:
        deps = xml_root.findall(_ns_tag("dependencies/dependency", ns))
        include_test = True if getattr(self.config, "include_test_scope", False) else False

        for dep in deps:
            group_id = _find_text(dep, "groupId", ns) or ""
            art_id = _find_text(dep, "artifactId", ns) or ""
            version = _find_text(dep, "version", ns) or ""
            scope = (_find_text(dep, "scope", ns) or "compile").lower()
            system_path = _find_text(dep, "systemPath", ns)

            if not art_id:
                continue

            if scope == "test" and not include_test:
                continue

            dep_name = f"{group_id}:{art_id}" if group_id else art_id
            module_target_id = f"module:maven:{group_id}:{art_id}"
            module_exists = self.graph_manager.has_node(module_target_id)

            if module_exists:
                # Treat as internal module dependency; avoid creating external node/spec.
                self.graph_manager.add_edge_spec(
                    EdgeSpec(
                        source=module_id,
                        target=module_target_id,
                        kind=EdgeKind.DEPENDS_ON,
                        parser_name=self.NAME,
                        attrs={"scope": scope},
                    )
                )
                continue

            node_id = f"ext_lib:maven:{dep_name}"
            if version:
                node_id = f"{node_id}@{version}"

            dep_spec = NodeSpec(
                id=node_id,
                type=NodeType.EXTERNAL_LIBRARY,
                label=dep_name,
                name=dep_name,
                parser_name=self.NAME,
                confidence=0.9,
                attrs={
                    "ecosystem": "maven",
                    "version": version or None,
                    "scope": scope,
                    "packaging": _find_text(dep, "type", ns),
                    "provenance": "maven_dependency",
                    "system_path": system_path if getattr(self.config, "record_system_path", True) else None,
                },
            )
            self.graph_manager.add_node_spec(dep_spec)
            self.graph_manager.add_edge_spec(
                EdgeSpec(
                    source=module_id,
                    target=node_id,
                    kind=EdgeKind.DEPENDS_ON,
                    parser_name=self.NAME,
                    attrs={"scope": scope},
                )
            )

            spec = DependencySpec(
                name=dep_name,
                version=version or "",
                ecosystem="maven",
                metadata={
                    "group_id": group_id,
                    "artifact_id": art_id,
                    "scope": scope,
                    "packaging": _find_text(dep, "type", ns) or None,
                    "system_path": system_path,
                },
            )
            event = Event(
                event_type=EventType.DEPENDENCY_DISCOVERED,
                source=self.NAME,
                data={
                    "spec": spec,
                    "source_file": str(pom_path),
                },
            )
            self.publish_parse_event(event)

    def _collect_source_roots(
        self, xml_root: ET.Element, ns: str, module_dir: Path
    ) -> List[Path]:
        roots: List[Path] = []
        candidates = [
            _find_text(xml_root, "build/sourceDirectory", ns),
            _find_text(xml_root, "build/testSourceDirectory", ns),
        ]
        default_roots = [
            module_dir / "src" / "main" / "java",
            module_dir / "src" / "test" / "java",
        ]

        for candidate in candidates:
            if candidate:
                candidate_path = Path(candidate)
                if not candidate_path.is_absolute():
                    roots.append((module_dir / candidate_path).resolve())
                else:
                    roots.append(candidate_path.resolve())

        roots.extend(default_roots)

        unique: List[Path] = []
        seen: Set[Path] = set()
        for root in roots:
            try:
                resolved = root.resolve()
            except OSError:
                continue
            if resolved not in seen:
                seen.add(resolved)
                unique.append(resolved)
        return unique


def _ns_tag(tag: str, ns: str) -> str:
    if not ns:
        return f".//{tag}"
    if "/" in tag:
        parts = tag.split("/")
        return ".//" + "/".join(f"{{{ns}}}{p}" for p in parts)
    return f".//{{{ns}}}{tag}"


def _find_text(elem: ET.Element, tag: str, ns: str) -> Optional[str]:
    target = elem.find(_ns_tag(tag, ns))
    if target is not None and target.text:
        return target.text.strip()
    return None


def _detect_namespace(root: ET.Element) -> str:
    if root.tag.startswith("{"):
        return root.tag.split("}")[0][1:]
    return ""


def _extract_gav(root: ET.Element, ns: str) -> Tuple[str, str, str]:
    group_id = _find_text(root, "groupId", ns)
    artifact_id = _find_text(root, "artifactId", ns) or "unknown"
    version = _find_text(root, "version", ns)

    parent = root.find(_ns_tag("parent", ns))
    if parent is not None:
        group_id = group_id or _find_text(parent, "groupId", ns)
        version = version or _find_text(parent, "version", ns)

    return (group_id or "unknown", artifact_id, version or "")


__all__ = ["MavenParser"]
