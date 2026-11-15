"""Scenario data model for the offline evaluation harness.

A scenario is a fixed project: a set of specs plus an ordered sequence of
code-structure changes. Every step carries ground truth about which specs
actually stopped being true after that change. The metrics compare the
system's beliefs against that truth, so the truth is authored by hand rather
than derived from the system being measured.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from specgraph.model import SPEC_TYPES


@dataclass(frozen=True)
class FileChange:
    """One file's symbol table before and after a change."""

    path: str
    before: dict[str, str]
    after: dict[str, str]
    body_changed: bool = False
    parser: str = "python"

    @property
    def structural(self) -> bool:
        return self.before != self.after

    def symbols_at(self, phase: str) -> dict[str, str]:
        if phase == "before":
            return dict(self.before)
        if phase == "after":
            return dict(self.after)
        raise ValueError("phase must be 'before' or 'after', got %r" % (phase,))


@dataclass(frozen=True)
class Task:
    """A question whose answer depends on exactly one spec staying true."""

    id: str
    question: str
    target: str
    answer_valid: str
    answer_stale: str
    files_to_read: int = 1


@dataclass(frozen=True)
class Step:
    """One commit-sized code change and the truth about what it broke."""

    label: str
    changes: tuple[FileChange, ...]
    newly_stale: tuple[str, ...]
    tasks: tuple[Task, ...] = ()
    note: str = ""

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(change.path for change in self.changes)


@dataclass(frozen=True)
class SpecDecl:
    """A spec written into the graph before the first change."""

    id: str
    type: str
    title: str
    statement: str = ""
    boundary: str = ""
    anchors: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    implements: tuple[str, ...] = ()
    references: tuple[str, ...] = ()
    parent: str | None = None
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    specs: tuple[SpecDecl, ...]
    steps: tuple[Step, ...]
    ttl_steps: int = 4

    @property
    def spec_ids(self) -> tuple[str, ...]:
        return tuple(spec.id for spec in self.specs)

    @property
    def total_stale(self) -> tuple[str, ...]:
        seen: list[str] = []
        for step in self.steps:
            for spec_id in step.newly_stale:
                if spec_id not in seen:
                    seen.append(spec_id)
        return tuple(seen)


def _dict_of_str(payload: Any, where: str) -> dict[str, str]:
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ValueError("%s must be an object of symbol -> signature" % where)
    return {str(key): str(value) for key, value in payload.items()}


def _tuple_of_str(payload: Any, where: str) -> tuple[str, ...]:
    if payload is None:
        return ()
    if isinstance(payload, str):
        return (payload,)
    if not isinstance(payload, Iterable):
        raise ValueError("%s must be a list of strings" % where)
    return tuple(str(item) for item in payload)


def _file_change(payload: dict, where: str) -> FileChange:
    path = str(payload.get("path") or "").strip()
    if not path:
        raise ValueError("%s is missing a path" % where)
    return FileChange(
        path=path,
        before=_dict_of_str(payload.get("before"), where + ".before"),
        after=_dict_of_str(payload.get("after"), where + ".after"),
        body_changed=bool(payload.get("body_changed", False)),
        parser=str(payload.get("parser") or "python"),
    )


def _task(payload: dict, where: str) -> Task:
    for key in ("id", "question", "target", "answer_valid", "answer_stale"):
        if not payload.get(key):
            raise ValueError("%s is missing %r" % (where, key))
    return Task(
        id=str(payload["id"]),
        question=str(payload["question"]),
        target=str(payload["target"]),
        answer_valid=str(payload["answer_valid"]),
        answer_stale=str(payload["answer_stale"]),
        files_to_read=int(payload.get("files_to_read", 1)),
    )


def _step(payload: dict, where: str) -> Step:
    changes = tuple(
        _file_change(entry, "%s.changes[%d]" % (where, index))
        for index, entry in enumerate(payload.get("changes") or [])
    )
    if not changes:
        raise ValueError("%s has no changes" % where)
    return Step(
        label=str(payload.get("label") or where),
        changes=changes,
        newly_stale=_tuple_of_str(payload.get("newly_stale"), where + ".newly_stale"),
        tasks=tuple(
            _task(entry, "%s.tasks[%d]" % (where, index))
            for index, entry in enumerate(payload.get("tasks") or [])
        ),
        note=str(payload.get("note") or ""),
    )


def _spec(payload: dict, where: str) -> SpecDecl:
    for key in ("id", "type", "title"):
        if not payload.get(key):
            raise ValueError("%s is missing %r" % (where, key))
    return SpecDecl(
        id=str(payload["id"]),
        type=str(payload["type"]),
        title=str(payload["title"]),
        statement=str(payload.get("statement") or ""),
        boundary=str(payload.get("boundary") or ""),
        anchors=_tuple_of_str(payload.get("anchors"), where + ".anchors"),
        depends_on=_tuple_of_str(payload.get("depends_on"), where + ".depends_on"),
        implements=_tuple_of_str(payload.get("implements"), where + ".implements"),
        references=_tuple_of_str(payload.get("references"), where + ".references"),
        parent=payload.get("parent") or None,
        tags=_tuple_of_str(payload.get("tags"), where + ".tags"),
    )


def load_scenario(path: str | Path) -> Scenario:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("scenario file must contain a JSON object")
    scenario = Scenario(
        name=str(payload.get("name") or Path(path).stem),
        description=str(payload.get("description") or ""),
        specs=tuple(
            _spec(entry, "specs[%d]" % index)
            for index, entry in enumerate(payload.get("specs") or [])
        ),
        steps=tuple(
            _step(entry, "steps[%d]" % index)
            for index, entry in enumerate(payload.get("steps") or [])
        ),
        ttl_steps=int(payload.get("ttl_steps", 4)),
    )
    problems = validate_scenario(scenario)
    if problems:
        raise ValueError("invalid scenario %r:\n  %s" % (scenario.name, "\n  ".join(problems)))
    return scenario


def validate_scenario(scenario: Scenario) -> list[str]:
    """Return human-readable problems; an empty list means the scenario is usable."""
    problems: list[str] = []
    if not scenario.specs:
        problems.append("scenario has no specs")
    if not scenario.steps:
        problems.append("scenario has no steps")

    known: set[str] = set()
    for spec in scenario.specs:
        if spec.id in known:
            problems.append("duplicate spec id %r" % spec.id)
        known.add(spec.id)

    for spec in scenario.specs:
        if spec.type not in SPEC_TYPES:
            problems.append("spec %r has unknown type %r" % (spec.id, spec.type))
        for label, targets in (
            ("depends_on", spec.depends_on),
            ("implements", spec.implements),
            ("references", spec.references),
        ):
            for target in targets:
                if target not in known:
                    problems.append("spec %r %s unknown spec %r" % (spec.id, label, target))
                if target == spec.id:
                    problems.append("spec %r links to itself via %s" % (spec.id, label))
        if spec.parent is not None and spec.parent not in known:
            problems.append("spec %r has unknown parent %r" % (spec.id, spec.parent))
        for anchor in spec.anchors:
            if anchor.startswith("#") or anchor.endswith("#"):
                problems.append("spec %r has a malformed anchor %r" % (spec.id, anchor))

    broken: list[str] = []
    seen_change: dict[str, dict[str, str]] = {}
    for index, step in enumerate(scenario.steps):
        where = "step %d (%s)" % (index, step.label)
        for spec_id in step.newly_stale:
            if spec_id not in known:
                problems.append("%s marks unknown spec %r stale" % (where, spec_id))
            if spec_id in broken:
                problems.append("%s marks %r stale again; this harness assumes monotone staleness"
                                % (where, spec_id))
            broken.append(spec_id)
        for task in step.tasks:
            if task.target not in known:
                problems.append("%s task %r targets unknown spec %r"
                                % (where, task.id, task.target))
            if not any(task.target == spec_id for spec_id in step.newly_stale):
                if task.target in broken:
                    problems.append("%s task %r targets %r which is already stale"
                                    % (where, task.id, task.target))
        for change in step.changes:
            previous = seen_change.get(change.path)
            if previous is not None and previous != change.before:
                problems.append(
                    "%s file %r starts from a different symbol table than the previous step ended with"
                    % (where, change.path)
                )
            seen_change[change.path] = dict(change.after)
    return problems


def describe(scenario: Scenario) -> str:
    lines = [
        "scenario: %s" % scenario.name,
        "  specs: %d, steps: %d, specs that go stale: %d"
        % (len(scenario.specs), len(scenario.steps), len(scenario.total_stale)),
    ]
    for index, step in enumerate(scenario.steps):
        stale = ", ".join(step.newly_stale) or "-"
        lines.append("  [%d] %s: %d file(s), stale: %s"
                     % (index + 1, step.label, len(step.changes), stale))
    return "\n".join(lines)
