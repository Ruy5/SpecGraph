from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

BELIEFS = ("held", "suspicious", "invalidated", "unverifiable")
VERDICTS = ("held", "invalidated", "unverifiable")
SPEC_TYPES = (
    "goal-and-requirements",
    "architecture-design",
    "module-design",
    "submodule-design",
    "task-spec",
)
SPEC_STATUSES = ("draft", "active", "stale", "done", "deprecated")
LINK_KINDS = ("parent", "depends-on", "references", "implements")
SINGLE_LINKS = ("parent",)
LIST_LINKS = ("depends-on", "references", "implements")

# Authoring lifecycle stays on ``status``. Governance state is
# ``belief`` so a code event can demand review without pretending the spec
# was edited or retired.
DEFAULT_BELIEF = "held"


@dataclass
class SpecNode:
    id: str
    type: str
    title: str
    path: str
    body: str
    frontmatter: dict
    status: str | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    belief: str = DEFAULT_BELIEF
    belief_reason: str | None = None
    belief_at: datetime | None = None
    perturbed_by: list[str] = field(default_factory=list)
    anchors: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    @property
    def abs_path(self) -> str:
        return self.path


@dataclass
class SpecEdge:
    src: str
    dst: str
    kind: str
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    belief: str | None = None


@dataclass
class SpecGraph:
    root: Path
    nodes: dict[str, SpecNode]
    edges: list[SpecEdge]
    duplicate_ids: dict[str, list[str]]

    def outgoing(self, src: str, kind: str | None = None) -> list[SpecEdge]:
        return [
            edge
            for edge in self.edges
            if edge.src == src and (kind is None or edge.kind == kind)
        ]

    def incoming(self, dst: str, kind: str | None = None) -> list[SpecEdge]:
        return [
            edge
            for edge in self.edges
            if edge.dst == dst and (kind is None or edge.kind == kind)
        ]
