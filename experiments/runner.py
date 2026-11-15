"""Drives one scenario under one variant and returns per-step observations.

The scenario supplies the code structure; the harness only translates it into
fingerprints and hands them to the real ``scan``. Every number the metrics read
comes back out of the real ``recall``, so nothing is computed twice, once by
the system and once by the experiment.

The graph lives in Neo4j by default, under a per-run project key that is
dropped afterwards. ``store='memory'`` swaps in the offline stand-in. Verdicts
come from the DeepSeek reviewer by default; ``reviewer='oracle'`` reads the
scenario's ground truth instead.
"""

from __future__ import annotations

import copy
import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Sequence

from specgraph import govern
from specgraph.query import recall

from experiments.graph_store import neo4j_store
from experiments.memory import MemoryGraph, installed
from experiments.metrics import StepRecord
from experiments.reviewer import DeepSeekReviewer, OracleReviewer
from experiments.scenario import Scenario
from experiments.variants import FULL, Variant, activated

ROOT = Path(".")
CREATED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc) - timedelta(days=1)
FIRST_STEP_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
STEP_INTERVAL = timedelta(hours=1)


def step_time(index: int) -> datetime:
    """Deterministic clock: one hour per step, so a rerun is bit-identical."""
    return FIRST_STEP_AT + STEP_INTERVAL * (index + 1)


def _payload(symbols: dict, parser: str, body_version: int) -> dict:
    text = "%s|%d" % (json.dumps(symbols, sort_keys=True), body_version)
    return {
        "text": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "parser": parser,
        "symbols": dict(symbols),
    }


def initial_state(scenario: Scenario) -> dict:
    """Whole-project fingerprint before the first change, not just the touched files."""
    files: dict = {}
    for step in scenario.steps:
        for change in step.changes:
            if change.path not in files:
                files[change.path] = _payload(change.before, change.parser, 0)
    return files


def _resolve_reviewer(reviewer):
    if reviewer is None:
        return DeepSeekReviewer.from_env()
    if isinstance(reviewer, str):
        if reviewer == "oracle":
            return OracleReviewer()
        if reviewer == "deepseek":
            return DeepSeekReviewer.from_env()
        raise ValueError("unknown reviewer %r; use 'deepseek' or 'oracle'" % (reviewer,))
    return reviewer


@contextmanager
def _store(kind: str, scenario: Scenario, variant: Variant):
    if kind == "memory":
        memory = MemoryGraph()
        with installed(memory):
            yield memory
        return
    if kind == "neo4j":
        with neo4j_store(scenario.name, variant.name) as store:
            with installed(store):
                yield store
        return
    raise ValueError("unknown store %r; use 'neo4j' or 'memory'" % (kind,))


def run_scenario(
    scenario: Scenario,
    variant: Variant,
    reviewer=None,
    store: str = "neo4j",
):
    """Return one StepRecord per step.

    ``reviewer='oracle'`` stands in for a correct human or agent reading the
    pending set: it is the mechanism's ceiling, not a claim about real
    reviewers. The default reviewer is the model served over
    ``SPECGRAPH_REVIEWER_BASE_URL``.
    """
    judge = _resolve_reviewer(reviewer)
    records: list = []
    stale: set = set()
    known = set(scenario.spec_ids)
    body_versions: dict = {}

    with _store(store, scenario, variant) as memory, activated(variant):
        for decl in scenario.specs:
            govern.create_spec(
                ROOT,
                decl.id,
                decl.type,
                decl.title,
                statement=decl.statement,
                boundary=decl.boundary,
                parent=decl.parent,
                anchors=list(decl.anchors),
                depends_on=list(decl.depends_on),
                implements=list(decl.implements),
                references=list(decl.references),
                tags=list(decl.tags),
                now=CREATED_AT,
            )

        files = initial_state(scenario)
        memory.snapshot = None
        memory.fingerprints = copy.deepcopy(files)
        if variant.scans:
            govern.scan(ROOT, now=CREATED_AT)

        for index, step in enumerate(scenario.steps):
            moment = step_time(index)
            for change in step.changes:
                if change.body_changed:
                    body_versions[change.path] = body_versions.get(change.path, 0) + 1
                files[change.path] = _payload(
                    change.after, change.parser, body_versions.get(change.path, 0)
                )
            memory.fingerprints = copy.deepcopy(files)
            stale.update(step.newly_stale)

            before_belief = {
                spec_id: node.belief for spec_id, node in memory.load_graph().nodes.items()
            }
            marked: set = set()
            if variant.scans:
                report = govern.scan(ROOT, now=moment)
                marked = {item["id"] for item in report.marked}
            newly_marked = {
                spec_id
                for spec_id in marked
                if before_belief.get(spec_id) != "suspicious"
            }

            if variant.ttl_steps is not None:
                _expire_by_age(memory, scenario, variant.ttl_steps, index, moment)

            if variant.verify:
                pending_now = recall(memory.load_graph(), moment).pending
                for node in pending_now:
                    verdict, reason = judge.decide(node, step, stale)
                    govern.verify(ROOT, node.id, verdict, reason, now=moment)

            found = recall(memory.load_graph(), moment)
            trusted = frozenset(node.id for node in found.trusted)
            pending = frozenset(node.id for node in found.pending)
            records.append(
                StepRecord(
                    scenario=scenario.name,
                    variant=variant.name,
                    family=variant.family,
                    step=index + 1,
                    label=step.label,
                    marked=frozenset(marked),
                    newly_marked=frozenset(newly_marked),
                    newly_stale=frozenset(step.newly_stale),
                    stale=frozenset(stale),
                    trusted=trusted,
                    pending=pending,
                    known=frozenset(known),
                )
            )
    return records


def _expire_by_age(store, scenario: Scenario, ttl_steps: int, index: int, moment) -> None:
    """Fixed lifetime, staggered per spec so the baseline is not a strawman.

    Spec at position ``p`` expires after ``ttl_steps + p`` changes. Nothing in
    this path looks at the code, which is exactly the point of the comparison.
    """
    nodes = store.load_graph(ROOT).nodes
    for position, decl in enumerate(scenario.specs):
        if index + 1 < ttl_steps + position:
            continue
        node = nodes.get(decl.id)
        if node is None or node.belief == "invalidated":
            continue
        store.update_belief(ROOT, decl.id, "invalidated", "ttl expired", moment, [], moment)


def run_all(
    scenario: Scenario,
    variants: Sequence | None = None,
    reviewer=None,
    store: str = "neo4j",
) -> list:
    chosen: Iterable = variants if variants is not None else (FULL,)
    records: list = []
    for variant in chosen:
        records.extend(run_scenario(scenario, variant, reviewer=reviewer, store=store))
    return records
