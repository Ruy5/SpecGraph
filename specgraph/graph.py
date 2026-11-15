from __future__ import annotations

from specgraph.model import (
    DEFAULT_BELIEF,
    LINK_KINDS,
    SpecEdge,
    SpecGraph,
    SpecNode,
)
from specgraph.parse import link_entries, scalar, string_list
from specgraph.timeutil import parse_time


def build_graph(root, files: list[tuple[str, dict, str]]) -> SpecGraph:
    grouped: dict[str, list[tuple[str, dict, str]]] = {}
    for path, frontmatter, body in files:
        spec_id = scalar(frontmatter, "id")
        spec_type = scalar(frontmatter, "type")
        if spec_id is None or spec_type is None:
            continue
        grouped.setdefault(spec_id, []).append((path, frontmatter, body))

    nodes: dict[str, SpecNode] = {}
    duplicates: dict[str, list[str]] = {}
    for spec_id in sorted(grouped):
        entries = sorted(grouped[spec_id], key=lambda item: item[0])
        if len(entries) > 1:
            duplicates[spec_id] = [path for path, _fm, _body in entries]
        path, frontmatter, body = entries[0]
        nodes[spec_id] = _node(path, frontmatter, body)

    edges: list[SpecEdge] = []
    for node in nodes.values():
        for kind in LINK_KINDS:
            for entry in link_entries(node.frontmatter, kind):
                edges.append(
                    SpecEdge(
                        src=node.id,
                        dst=str(entry["id"]),
                        kind=kind,
                        valid_from=parse_time(_as_text(entry.get("valid-from"))),
                        valid_to=parse_time(_as_text(entry.get("valid-to"))),
                        belief=_as_text(entry.get("belief")),
                    )
                )
    return SpecGraph(root=root, nodes=nodes, edges=edges, duplicate_ids=duplicates)


def _node(path: str, frontmatter: dict, body: str) -> SpecNode:
    belief = scalar(frontmatter, "belief") or DEFAULT_BELIEF
    return SpecNode(
        id=scalar(frontmatter, "id") or "",
        type=scalar(frontmatter, "type") or "",
        title=scalar(frontmatter, "title") or "",
        path=path,
        body=body,
        frontmatter=frontmatter,
        status=scalar(frontmatter, "status"),
        valid_from=parse_time(scalar(frontmatter, "valid-from")),
        valid_to=parse_time(scalar(frontmatter, "valid-to")),
        belief=belief,
        belief_reason=scalar(frontmatter, "belief-reason"),
        belief_at=parse_time(scalar(frontmatter, "belief-at")),
        perturbed_by=string_list(frontmatter, "perturbed-by"),
        anchors=string_list(frontmatter, "anchors"),
        tags=string_list(frontmatter, "tags"),
    )


def _as_text(value) -> str | None:
    if isinstance(value, str) and value != "":
        return value
    return None
