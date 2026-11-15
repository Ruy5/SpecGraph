"""Task-level utility: what the memory is worth when an agent answers questions.

The governance experiment measures belief bookkeeping. This one measures what a
reader cares about: under a given memory discipline, does an agent answer
architecture questions correctly, and at what reading cost?

A policy may only read the memory it is handed. Ground truth is applied by the
runner, so no policy can score by peeking at the answer. ``no_memory`` is the
exception by construction: re-deriving from source is exactly what it does, and
its file count is the cost that memorising is supposed to remove.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from experiments.runner import run_scenario
from experiments.scenario import Scenario, Task
from experiments.variants import Variant


@dataclass(frozen=True)
class MemoryView:
    trusted: frozenset
    pending: frozenset
    known: frozenset


@dataclass(frozen=True)
class Truth:
    text: str
    files: int


@dataclass(frozen=True)
class Answer:
    text: str | None
    files_read: int = 0

    @property
    def abstained(self) -> bool:
        return self.text is None


class Policy(Protocol):
    name: str

    def answer(self, task: Task, view: MemoryView, truth: Truth) -> Answer:
        ...


class NoMemoryPolicy:
    """Re-derive from the source every time; correct but pays the reading cost."""

    name = "no_memory"

    def answer(self, task: Task, view: MemoryView, truth: Truth) -> Answer:
        return Answer(truth.text, truth.files)


class TrustedOnlyPolicy:
    """Answer from the spec only while the system calls it trusted."""

    name = "trusted_only"

    def answer(self, task: Task, view: MemoryView, truth: Truth) -> Answer:
        if task.target in view.trusted:
            return Answer(task.answer_valid, 0)
        return Answer(None, 0)


class BlindPolicy:
    """Answer from the spec whenever one exists, ignoring belief entirely."""

    name = "blind"

    def answer(self, task: Task, view: MemoryView, truth: Truth) -> Answer:
        if task.target in view.known:
            return Answer(task.answer_valid, 0)
        return Answer(None, 0)


POLICIES = (NoMemoryPolicy(), TrustedOnlyPolicy(), BlindPolicy())
POLICY_NAMES = tuple(policy.name for policy in POLICIES)


@dataclass(frozen=True)
class TaskOutcome:
    scenario: str
    variant: str
    policy: str
    step: int
    task_id: str
    answered: bool
    correct: bool
    files_read: int

    @property
    def harmful(self) -> bool:
        return self.answered and not self.correct

    def as_row(self) -> dict:
        return {
            "scenario": self.scenario,
            "variant": self.variant,
            "policy": self.policy,
            "step": self.step,
            "task": self.task_id,
            "answered": self.answered,
            "correct": self.correct,
            "harmful": self.harmful,
            "files_read": self.files_read,
        }


@dataclass
class UtilitySummary:
    variant: str
    policy: str
    tasks: int = 0
    answered: int = 0
    correct: int = 0
    failed: int = 0
    files_read: int = 0

    @property
    def accuracy(self) -> float | None:
        if self.answered == 0:
            return None
        return self.correct / self.answered

    @property
    def abstain_rate(self) -> float | None:
        if self.tasks == 0:
            return None
        return (self.tasks - self.answered) / self.tasks

    @property
    def harmful_rate(self) -> float | None:
        if self.tasks == 0:
            return None
        return self.failed / self.tasks

    @property
    def files_per_task(self) -> float | None:
        if self.tasks == 0:
            return None
        return self.files_read / self.tasks


def _truth_for(task: Task, record) -> Truth:
    if task.target in record.stale:
        return Truth(task.answer_stale, task.files_to_read)
    return Truth(task.answer_valid, task.files_to_read)


def run_tasks(scenario: Scenario, variant: Variant, policy: Policy, records=None, reviewer=None, store: str = "neo4j") -> list:
    """One outcome per (step, task) pair the scenario declares.

    ``records`` may be reused from the governance pass so the scenario is not
    replayed once per policy.
    """
    if records is None:
        records = run_scenario(scenario, variant, reviewer=reviewer, store=store)
    relevant = [record for record in records if record.variant == variant.name]
    by_step = {record.step: record for record in relevant}
    outcomes: list = []
    for index, step in enumerate(scenario.steps):
        if not step.tasks:
            continue
        record = by_step[index + 1]
        view = MemoryView(record.trusted, record.pending, record.known)
        for task in step.tasks:
            truth = _truth_for(task, record)
            answer = policy.answer(task, view, truth)
            correct = answer.text is not None and answer.text == truth.text
            outcomes.append(
                TaskOutcome(
                    scenario=scenario.name,
                    variant=variant.name,
                    policy=policy.name,
                    step=index + 1,
                    task_id=task.id,
                    answered=not answer.abstained,
                    correct=correct,
                    files_read=answer.files_read,
                )
            )
    return outcomes


def summarise(outcomes) -> list:
    order: list = []
    buckets: dict = {}
    for outcome in outcomes:
        key = (outcome.variant, outcome.policy)
        if key not in buckets:
            buckets[key] = UtilitySummary(variant=outcome.variant, policy=outcome.policy)
            order.append(key)
        summary = buckets[key]
        summary.tasks += 1
        summary.files_read += outcome.files_read
        if outcome.answered:
            summary.answered += 1
        if outcome.correct:
            summary.correct += 1
        if outcome.harmful:
            summary.failed += 1
    return [buckets[key] for key in order]
