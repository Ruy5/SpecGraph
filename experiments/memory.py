"""An in-process stand-in for the Neo4j store.

The harness swaps these methods into ``specgraph.govern`` so the real
scan / consume / verify code path runs unchanged, only against dictionaries
instead of a database. Nothing here is imported by the shipped library.
"""

from __future__ import annotations

import copy
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from specgraph import govern
from specgraph.errors import SpecGraphError
from specgraph.model import DEFAULT_BELIEF, SpecEdge, SpecGraph, SpecNode

PATCHED_NAMES = (
    "load_graph",
    "create_spec_node",
    "update_belief",
    "append_event",
    "load_snapshot",
    "save_snapshot",
    "code_fingerprints",
    "link_specs_node",
    "set_status_node",
)


class MemoryGraph:
    """The smallest store that satisfies the governance code's expectations."""

    def __init__(self) -> None:
        self.nodes: dict[str, SpecNode] = {}
        self.edges: list[SpecEdge] = []
        self.events: list[dict] = []
        self.snapshot: dict[str, dict] | None = None
        self.fingerprints: dict[str, dict] = {}
        self.snapshot_writes = 0

    def clone(self) -> SpecGraph:
        return SpecGraph(
            root=Path("."),
            nodes={key: copy.deepcopy(value) for key, value in self.nodes.items()},
            edges=[copy.deepcopy(edge) for edge in self.edges],
            duplicate_ids={},
        )

    def load_graph(self, root=Path(".")) -> SpecGraph:
        graph = self.clone()
        graph.root = Path(root)
        return graph

    def create_spec(
        self,
        root,
        spec_id,
        spec_type,
        title,
        body,
        moment,
        parent=None,
        anchors=None,
        depends_on=None,
        implements=None,
        references=None,
        tags=None,
        status="active",
    ):
        if spec_id in self.nodes:
            raise SpecGraphError("spec %s already exists" % spec_id)
        self.nodes[spec_id] = SpecNode(
            id=spec_id,
            type=spec_type,
            title=title,
            body=body,
            status=status,
            valid_from=moment,
            valid_to=None,
            belief=DEFAULT_BELIEF,
            belief_reason=None,
            belief_at=None,
            perturbed_by=[],
            anchors=list(anchors or []),
            tags=list(tags or []),
        )
        pairs: list[tuple[str, str]] = []
        if parent:
            pairs.append(("parent", parent))
        for kind, values in (
            ("depends-on", depends_on),
            ("implements", implements),
            ("references", references),
        ):
            for value in values or []:
                pairs.append((kind, value))
        for kind, target in pairs:
            if target not in self.nodes:
                raise SpecGraphError("unknown spec %s" % target)
            self.edges.append(SpecEdge(src=spec_id, dst=target, kind=kind))

    def update_belief(self, root, spec_id, belief, reason, moment, perturbed_by, valid_to):
        node = self.nodes.get(spec_id)
        if node is None:
            raise SpecGraphError("unknown spec %s" % spec_id)
        node.belief = belief
        node.belief_reason = reason
        node.belief_at = moment
        node.perturbed_by = list(perturbed_by)
        node.valid_to = valid_to

    def append_event(self, root, event):
        self.events.append(copy.deepcopy(event))

    def load_snapshot(self, root):
        return copy.deepcopy(self.snapshot)

    def save_snapshot(self, root, files):
        self.snapshot = copy.deepcopy(files)
        self.snapshot_writes += 1

    def code_fingerprints(self, root):
        return copy.deepcopy(self.fingerprints)

    def link_specs(self, root, src, dst, kind):
        for spec_id in (src, dst):
            if spec_id not in self.nodes:
                raise SpecGraphError("unknown spec %s" % spec_id)
        self.edges.append(SpecEdge(src=src, dst=dst, kind=kind))

    def set_status(self, root, spec_id, status, moment):
        node = self.nodes.get(spec_id)
        if node is None:
            raise SpecGraphError("unknown spec %s" % spec_id)
        node.status = status

    def events_of(self, spec_id: str) -> list[dict]:
        return [event for event in self.events if event.get("spec_id") == spec_id]

    def ops(self) -> list[str]:
        return [event.get("op", "") for event in self.events]


@contextmanager
def installed(memory: MemoryGraph) -> Iterator[MemoryGraph]:
    """Route the governance module's storage calls at ``memory`` for the block."""
    saved = {name: getattr(govern, name) for name in PATCHED_NAMES}
    replacements = {
        "load_graph": memory.load_graph,
        "create_spec_node": memory.create_spec,
        "update_belief": memory.update_belief,
        "append_event": memory.append_event,
        "load_snapshot": memory.load_snapshot,
        "save_snapshot": memory.save_snapshot,
        "code_fingerprints": memory.code_fingerprints,
        "link_specs_node": memory.link_specs,
        "set_status_node": memory.set_status,
    }
    try:
        for name, replacement in replacements.items():
            setattr(govern, name, replacement)
        yield memory
    finally:
        for name, value in saved.items():
            setattr(govern, name, value)
