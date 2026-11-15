"""Stores the evaluation harness can run its governance loop against.

``MemoryGraph`` in :mod:`experiments.memory` is the offline stand-in. This
module adds ``Neo4jStore``, which forwards the graph calls to the shipped
persistence layer so an evaluation can exercise the real database instead.

Only the graph is persisted. The code-structure snapshot stays in process
because the scenario synthesises it from JSON and must never overwrite the
``.specgraph/structure.json`` of the checkout.
"""

from __future__ import annotations

import copy
import os
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from specgraph import database


class Neo4jStore:
    """The MemoryGraph interface, backed by Neo4j instead of dictionaries."""

    def __init__(self, project: str) -> None:
        self.project = project
        self.snapshot: dict[str, dict] | None = None
        self.fingerprints: dict[str, dict] = {}
        self.snapshot_writes = 0

    def load_graph(self, root=Path(".")):
        return database.load_graph(Path(root))

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
        database.create_spec(
            Path(root),
            spec_id,
            spec_type,
            title,
            body,
            moment,
            parent=parent,
            anchors=anchors,
            depends_on=depends_on,
            implements=implements,
            references=references,
            tags=tags,
            status=status,
        )

    def update_belief(self, root, spec_id, belief, reason, moment, perturbed_by, valid_to):
        database.update_belief(
            Path(root), spec_id, belief, reason, moment, perturbed_by, valid_to
        )

    def append_event(self, root, event):
        database.append_event(Path(root), event)

    def link_specs(self, root, src, dst, kind):
        database.link_specs(Path(root), src, dst, kind)

    def set_status(self, root, spec_id, status, moment):
        database.set_status(Path(root), spec_id, status, moment)

    def load_snapshot(self, root):
        return copy.deepcopy(self.snapshot)

    def save_snapshot(self, root, files):
        self.snapshot = copy.deepcopy(files)
        self.snapshot_writes += 1

    def code_fingerprints(self, root):
        return copy.deepcopy(self.fingerprints)

    def clear(self, root=Path(".")) -> None:
        database.delete_project(Path(root))


@contextmanager
def neo4j_store(scenario_name: str, variant_name: str) -> Iterator[Neo4jStore]:
    """Run one scenario/variant pair under a fresh, self-cleaning project key.

    Neo4j has no transaction that spans the whole run, so isolation comes from
    the project key: every node and event the run writes carries it, and both
    ends of the block delete exactly that key. A failed connection raises
    ``SpecGraphError`` from the first cleanup, before any spec is written.
    """
    project = "specgraph-eval:%s:%s:%s" % (
        scenario_name,
        variant_name,
        uuid.uuid4().hex[:8],
    )
    previous = os.environ.get("SPECGRAPH_PROJECT")
    os.environ["SPECGRAPH_PROJECT"] = project
    store = Neo4jStore(project)
    connected = False
    try:
        store.clear()
        connected = True
        yield store
    finally:
        try:
            if connected:
                store.clear()
        finally:
            if previous is None:
                os.environ.pop("SPECGRAPH_PROJECT", None)
            else:
                os.environ["SPECGRAPH_PROJECT"] = previous
