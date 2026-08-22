"""
Platrixa
Sprint 12A - Deterministic Maths & Financial Reasoning Engine
backend/maths/accounting_graph.py

Directed Accounting Graph.

* Nodes  = financial facts (known) and derived quantities (computed).
* Edges  = mathematical / accounting relationships from the Formula
           Registry (target -> dependencies).
* Multi-step dependency chains are supported: the graph is resolved
  bottom-up along a deterministic topological order.
* Cycles are detected BEFORE execution (DFS with color marking) and
  reported as a structured CycleDetectedError / BLOCKED reason - the
  engine never evaluates a circular chain.
* Traversal paths are recorded deterministically (dependency order, then
  registration order) and memoized so identical sub-problems are
  evaluated exactly once.

Pure module: no Streamlit, no AI, no network. Deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from backend.maths.exceptions import CycleDetectedError, RegistrationError
from backend.maths.formula_registry import FormulaRegistry
from backend.maths.status import BLOCKED, DERIVED, VERIFIED


@dataclass
class GraphNode:
    """One node in the accounting graph.

    FACT    -> directly known fact (carries a FactNode)
    DERIVED -> quantity produced by a registered formula application
    """

    node_id: str
    kind: str = "FACT"                    # "FACT" | "DERIVED"
    formula_id: Optional[str] = None
    dependencies: List[str] = field(default_factory=list)
    value: object = None                  # Decimal after resolution
    status: str = VERIFIED
    status_reason: Optional[str] = None
    resolved: bool = False


class AccountingGraph:
    """Deterministic directed accounting graph built from a registry."""

    def __init__(self, registry: FormulaRegistry) -> None:
        self.registry = registry
        self._nodes: Dict[str, GraphNode] = {}
        # adjacency: node_id -> set of node_ids it directly depends on
        self._edges: Dict[str, Set[str]] = {}

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def add_fact(self, node_id: str, value=None, status: str = VERIFIED,
                 status_reason: Optional[str] = None) -> None:
        self._nodes[node_id] = GraphNode(
            node_id=node_id, kind="FACT", value=value,
            status=status, status_reason=status_reason, resolved=True,
        )

    def add_formula_application(
        self, target: str, formula_id: str, dependencies: List[str],
    ) -> None:
        """Register a derived node: target produced by a registered
        formula from the given dependencies."""
        self._nodes[target] = GraphNode(
            node_id=target, kind="DERIVED", formula_id=formula_id,
            dependencies=list(dependencies), resolved=False,
        )
        self._edges[target] = set(dependencies)

    def has_node(self, node_id: str) -> bool:
        return node_id in self._nodes

    def get_node(self, node_id: str) -> Optional[GraphNode]:
        return self._nodes.get(node_id)

    def node_ids(self) -> List[str]:
        return sorted(self._nodes.keys())

    # ------------------------------------------------------------------
    # Cycle detection (DFS with colors; deterministic order)
    # ------------------------------------------------------------------

    def detect_cycles(self) -> List[List[str]]:
        """Return every cycle in the graph as an ordered path, or [].
        Deterministic: nodes are visited in sorted order."""
        WHITE, GRAY, BLACK = 0, 1, 2
        color: Dict[str, int] = {k: WHITE for k in self._nodes}
        cycles: List[List[str]] = []

        def dfs(node: str, path: List[str]) -> None:
            color[node] = GRAY
            path.append(node)
            for dep in sorted(self._edges.get(node, ())):
                if dep not in self._nodes:
                    continue
                if color[dep] == GRAY:
                    # cycle found: dep ... -> dep
                    start = path.index(dep) if dep in path else 0
                    cycles.append(path[start:] + [dep])
                elif color[dep] == WHITE:
                    dfs(dep, path)
            path.pop()
            color[node] = BLACK

        for n in sorted(self._nodes):
            if color[n] == WHITE:
                dfs(n, [])
        return cycles

    def assert_acyclic(self) -> None:
        cycles = self.detect_cycles()
        if cycles:
            raise CycleDetectedError(
                "Dependency cycle detected: "
                + " -> ".join(cycles[0])
            )

    # ------------------------------------------------------------------
    # Topological order (deterministic: sorted tie-breaking)
    # ------------------------------------------------------------------

    def topological_order(self, target: Optional[str] = None) -> List[str]:
        """Deterministic topological order of the (sub)graph.

        If `target` is given, only nodes required to compute it are
        returned, in execution order (dependencies before dependents).
        Raises CycleDetectedError when a cycle exists.
        """
        self.assert_acyclic()
        # restrict to the closure of target when requested
        nodes = self._closure(target) if target else set(self._nodes)
        visited: Set[str] = set()
        order: List[str] = []

        def visit(node: str) -> None:
            if node in visited:
                return
            visited.add(node)
            for dep in sorted(self._edges.get(node, ())):
                if dep in nodes and dep not in visited:
                    visit(dep)
            order.append(node)

        for n in sorted(nodes):
            visit(n)
        return order

    def _closure(self, target: str) -> Set[str]:
        """All node ids transitively required to compute `target`."""
        if target not in self._nodes:
            return set()
        seen: Set[str] = set()
        stack = [target]
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            for dep in self._edges.get(n, ()):
                if dep not in seen:
                    stack.append(dep)
        return seen

    # ------------------------------------------------------------------
    # Deterministic traversal path
    # ------------------------------------------------------------------

    def traversal_path(self, target: str) -> List[str]:
        """Deterministic traversal path for one target: the ordered list
        of node ids visited, dependencies first (memoized: each node
        appears once)."""
        order = self.topological_order(target)
        return order

    def to_dict(self) -> Dict[str, Dict]:
        return {
            nid: {
                "node_id": n.node_id,
                "kind": n.kind,
                "formula_id": n.formula_id,
                "dependencies": list(n.dependencies),
                "status": n.status,
                "resolved": n.resolved,
            }
            for nid, n in sorted(self._nodes.items())
        }
