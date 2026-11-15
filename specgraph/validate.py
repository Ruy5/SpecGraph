from __future__ import annotations

from dataclasses import dataclass, field

from specgraph.model import BELIEFS, SpecGraph


@dataclass
class ValidationReport:
    dangling: list[str] = field(default_factory=list)
    duplicate_ids: list[str] = field(default_factory=list)
    parent_cycles: list[str] = field(default_factory=list)
    intervals: list[str] = field(default_factory=list)
    beliefs: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.dangling or self.duplicate_ids or self.parent_cycles or self.intervals or self.beliefs)


def validate_graph(graph: SpecGraph) -> ValidationReport:
    report = ValidationReport()
    for edge in graph.edges:
        if edge.dst not in graph.nodes:
            report.dangling.append(f"{edge.src} -{edge.kind}-> {edge.dst} ({graph.nodes[edge.src].path})")
        if edge.belief is not None and edge.belief not in BELIEFS:
            report.beliefs.append(f"{edge.src} {edge.kind} belief {edge.belief}")
    for spec_id, paths in sorted(graph.duplicate_ids.items()):
        report.duplicate_ids.append(f"{spec_id}: {', '.join(paths)}")
    for ring in _parent_cycles(graph):
        report.parent_cycles.append(" -> ".join(ring))
    for node in graph.nodes.values():
        if node.belief not in BELIEFS:
            report.beliefs.append(f"{node.id} belief {node.belief}")
        if node.valid_from and node.valid_to and node.valid_from >= node.valid_to:
            report.intervals.append(f"{node.id} valid-from is not before valid-to")
        for edge in graph.outgoing(node.id):
            start = edge.valid_from or node.valid_from
            end = edge.valid_to or node.valid_to
            if start and end and start >= end:
                report.intervals.append(f"{node.id} -{edge.kind}-> {edge.dst} has an empty interval")
        if node.type in {"module-design", "submodule-design"} and not node.anchors:
            report.warnings.append(f"{node.id} has no anchors, so code changes cannot mark it")
    return report


def _parent_cycles(graph: SpecGraph) -> list[list[str]]:
    cycles: list[list[str]] = []
    seen: set[str] = set()
    keys: set[tuple[str, ...]] = set()
    for start in graph.nodes:
        if start in seen:
            continue
        path: list[str] = []
        index: dict[str, int] = {}
        current: str | None = start
        while current is not None and current in graph.nodes:
            if current in index:
                ring = path[index[current] :]
                key = tuple(sorted(ring))
                if key not in keys:
                    keys.add(key)
                    cycles.append(ring)
                break
            if current in seen:
                break
            index[current] = len(path)
            path.append(current)
            parents = [edge.dst for edge in graph.outgoing(current, "parent")]
            current = parents[0] if parents else None
        seen.update(path)
    return cycles
