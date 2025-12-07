"""Uncertainty analysis for nodes and edges.

This module classifies nodes and edges into high-level uncertainty
categories (definite / probable / conditional) based on confidence
scores and structural patterns. The resulting annotations are stored
on graph attributes and are intended to be consumed by downstream
tools (license scanners, security analyzers, etc.).

The analysis is controlled by UncertaintyPolicyConfig and is entirely
orthogonal to any business logic: it only labels uncertainty, it does
not interpret legal or security implications.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from depanalyzer.graph import GraphManager
from depanalyzer.runtime.graph_config import UncertaintyPolicyConfig

logger = logging.getLogger("depanalyzer.analysis.uncertainty")


class UncertaintyAnalyzer:
    """Analyzer for determining uncertainty categories on the graph.

    The analyzer uses two layers of rules:
    1) Confidence-based defaults (definite / probable / conditional).
    2) Structural overrides for well-known patterns such as proxy nodes,
       unresolved external dependencies, and projection-derived edges.
    """

    def __init__(
        self,
        graph_manager: GraphManager,
        config: UncertaintyPolicyConfig,
    ) -> None:
        """Initialize uncertainty analyzer.

        Args:
            graph_manager: Transaction graph manager.
            config: Uncertainty policy configuration.
        """
        self._gm = graph_manager
        self._config = config
        logger.info("UncertaintyAnalyzer initialized (enabled=%s)", config.enabled)

    def analyze(self) -> None:
        """Run uncertainty analysis on the current graph."""
        if not self._config.enabled:
            logger.info("Uncertainty analysis disabled by config, skipping")
            return

        logger.info(
            "Starting uncertainty analysis on graph (%d nodes, %d edges)",
            self._gm.node_count(),
            self._gm.edge_count(),
        )

        # First derive default categories from confidence scores.
        self._apply_confidence_defaults()

        # Then apply structural rules that override defaults for known patterns.
        self._apply_structural_rules()

        logger.info("Uncertainty analysis completed")

    # ------------------------------------------------------------------
    # Confidence-based defaults
    # ------------------------------------------------------------------

    def _apply_confidence_defaults(self) -> None:
        """Assign default uncertainty categories based on confidence."""
        cfg = self._config

        for _, attrs in self._gm.nodes():
            self._set_default_category(attrs, cfg)

        for _, _, _, attrs in self._gm.edges():
            self._set_default_category(attrs, cfg)

    def _set_default_category(
        self,
        attrs: Dict[str, Any],
        cfg: UncertaintyPolicyConfig,
    ) -> None:
        """Set default uncertainty_category based on confidence if unset.

        This method intentionally does not override non-default categories so
        that future callers can pin categories explicitly if needed.
        """
        current = attrs.get("uncertainty_category")
        if current not in (None, "", "definite"):
            # Category already set to a non-default value; respect it.
            return

        try:
            confidence = float(attrs.get("confidence", 1.0))
        except (TypeError, ValueError):
            confidence = 1.0

        if confidence >= cfg.definite_min_confidence:
            category = "definite"
        elif confidence >= cfg.probable_min_confidence:
            category = "probable"
        else:
            category = "conditional"

        attrs["uncertainty_category"] = category
        attrs["over_approx"] = category != "definite"

    # ------------------------------------------------------------------
    # Structural rules for well-known over-approximation patterns
    # ------------------------------------------------------------------

    def _apply_structural_rules(self) -> None:
        """Apply structural rules for obviously uncertain patterns."""
        cfg = self._config

        # Proxy nodes represent references into external graphs and are
        # over-approximations by design.
        if cfg.mark_proxy_nodes_as_conditional:
            for _, attrs in self._gm.nodes():
                if attrs.get("type") == "proxy" or attrs.get("child_graph_id"):
                    self._mark_conditional(attrs, reason="proxy_reference")

        # Unresolved external_dep nodes (no resolved_path) are conditional.
        if cfg.mark_unresolved_external_as_conditional:
            for _, attrs in self._gm.nodes():
                if attrs.get("type") == "external_dep" and not attrs.get(
                    "resolved_path"
                ):
                    self._mark_conditional(attrs, reason="unresolved_external")

        # Edges produced by the projection phase are typically derived from
        # heuristics (e.g. include+part_of) and should be treated as probable.
        if cfg.mark_projection_edges_as_probable:
            for _, _, _, attrs in self._gm.edges():
                if attrs.get("parser_name") == "projection":
                    self._mark_probable(attrs, reason="projection_fallback")

        # Contract-derived edges (build interface contracts) are based on
        # configuration-level matching and should generally be treated as
        # probable rather than definite, since they rely on naming and
        # path conventions across ecosystems.
        if cfg.mark_contract_edges_as_probable:
            for _, _, _, attrs in self._gm.edges():
                derived_from = attrs.get("derived_from") or ""
                parser_name = attrs.get("parser_name") or ""

                if derived_from in {"build_contract", "contract_binding", "hvigor_shared_library"}:
                    self._mark_probable(attrs, reason="build_contract")
                elif parser_name in {"global_contract_linker", "hvigor_linker"}:
                    # Fall back to parser_name when derived_from is not present
                    self._mark_probable(attrs, reason="build_contract")

        # Dynamic patterns (e.g. dlopen, reflection) and wildcard patterns
        # will be wired in during Phase 2 when parsers start emitting
        # standardized evidence tags. We keep the toggle for future use.
        if cfg.mark_dynamic_patterns_as_conditional:
            # No-op until parsers add dynamic/wildcard evidence tags.
            return

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _mark_conditional(self, attrs: Dict[str, Any], reason: str) -> None:
        """Mark an attribute mapping as conditional with a specific reason."""
        attrs["uncertainty_category"] = "conditional"
        attrs["over_approx"] = True

        reasons = set(attrs.get("uncertainty_reasons") or [])
        reasons.add(reason)
        attrs["uncertainty_reasons"] = sorted(reasons)

    def _mark_probable(self, attrs: Dict[str, Any], reason: str) -> None:
        """Mark an attribute mapping as probable with a specific reason."""
        # Do not downgrade already-conditional entries.
        if attrs.get("uncertainty_category") == "conditional":
            return

        attrs["uncertainty_category"] = "probable"
        attrs["over_approx"] = True

        reasons = set(attrs.get("uncertainty_reasons") or [])
        reasons.add(reason)
        attrs["uncertainty_reasons"] = sorted(reasons)


__all__ = ["UncertaintyAnalyzer"]
