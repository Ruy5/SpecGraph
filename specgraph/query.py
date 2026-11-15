from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from specgraph.model import SpecEdge, SpecGraph, SpecNode
from specgraph.timeutil import in_window, utcnow


@dataclass
class Recall:
    as_of: datetime
    trusted: list[SpecNode]
    pending: list[SpecNode]


@dataclass
class SliceRow:
    id: str
    depth: int
    via: str | None
    belief: str


def temporally_current(node: SpecNode, as_of: datetime) -> bool:
    return in_window(as_of, node.valid_from, node.valid_to)


def suppressed(node: SpecNode) -> bool:
    return node.type == "task-spec" and node.status == "done"


def recall(graph: SpecGraph, as_of: datetime | None = None, text: str | None = None) -> Recall:
    moment = as_of or utcnow()
    trusted: list[SpecNode] = []
    pending: list[SpecNode] = []
    needle = text.casefold() if text else None
    for node in graph.nodes.values():
        if suppressed(node) or node.belief == "invalidated" or not temporally_current(node, moment):
            continue
        if needle is not None and needle not in _haystack(node):
            continue
        if node.belief == "held":
            trusted.append(node)
        elif node.belief in {"suspicious", "unverifiable"}:
            pending.append(node)
    trusted.sort(key=lambda node: node.id)
    pending.sort(key=lambda node: node.id)
    return Recall(moment, trusted, pending)


def slice_graph(
    graph: SpecGraph,
    root_id: str,
    direction: str,
    depth: int,
    as_of: datetime | None = None,
) -> list[SliceRow]:
    if root_id not in graph.nodes:
        raise KeyError(root_id)
    if direction not in {"ancestors", "subtree", "neighbors"}:
        raise ValueError(direction)
    moment = as_of or utcnow()
    root = graph.nodes[root_id]
    rows = [SliceRow(root.id, 0, None, root.belief)]
    if root.belief == "invalidated" or not temporally_current(root, moment):
        return rows
    seen = {root_id}
    queue = [(root_id, 0)]
    while queue:
        current, dist = queue.pop(0)
        if dist >= depth:
            continue
        for via, other in _steps(graph, current, direction, moment):
            if other in seen or other not in graph.nodes:
                continue
            seen.add(other)
            node = graph.nodes[other]
            rows.append(SliceRow(other, dist + 1, via, node.belief))
            queue.append((other, dist + 1))
    return rows


def edge_current(graph: SpecGraph, edge: SpecEdge, as_of: datetime) -> bool:
    src = graph.nodes.get(edge.src)
    dst = graph.nodes.get(edge.dst)
    if src is None or dst is None:
        return False
    if src.belief == "invalidated" or dst.belief == "invalidated":
        return False
    if not temporally_current(src, as_of) or not temporally_current(dst, as_of):
        return False
    start = edge.valid_from if edge.valid_from is not None else src.valid_from
    end = edge.valid_to if edge.valid_to is not None else src.valid_to
    if not in_window(as_of, start, end):
        return False
    if (edge.belief or src.belief) == "invalidated":
        return False
    return True


def _steps(graph: SpecGraph, current: str, direction: str, as_of: datetime):
    if direction == "ancestors":
        edges = graph.outgoing(current, "parent")
        pairs = [(edge, edge.dst) for edge in edges]
    elif direction == "subtree":
        edges = graph.incoming(current, "parent")
        pairs = [(edge, edge.src) for edge in edges]
    else:
        pairs = []
        for edge in graph.outgoing(current):
            pairs.append((edge, edge.dst))
        for edge in graph.incoming(current):
            pairs.append((edge, edge.src))
    steps = []
    for edge, other in pairs:
        if edge_current(graph, edge, as_of):
            steps.append((edge.kind, other))
    steps.sort()
    return steps


def _haystack(node: SpecNode) -> str:
    return "\n".join([node.id, node.title, node.type, " ".join(node.tags), node.body]).casefold()
