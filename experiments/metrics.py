"""Metrics that keep the two failure directions apart.

A memory system can fail in two opposite ways:

* it lets a stale spec be trusted, which is the silent corruption this project
  is about;
* it refuses a spec that is still true, which is the price of being cautious.

Reporting only one of them lets a cautious system look good for the wrong
reason, and lets a permissive system look good on availability. Both are
reported, with the review cost that produced them.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Iterable, Sequence


def ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


@dataclass(frozen=True)
class StepRecord:
    """What one variant believed after one code change."""

    scenario: str
    variant: str
    family: str
    step: int
    label: str
    marked: frozenset
    newly_marked: frozenset
    newly_stale: frozenset
    stale: frozenset
    trusted: frozenset
    pending: frozenset
    known: frozenset

    @property
    def valid(self) -> frozenset:
        return frozenset(self.known - self.stale)

    @property
    def absent(self) -> frozenset:
        return frozenset(self.known - self.trusted - self.pending)

    @property
    def mark_tp(self) -> int:
        return len(self.newly_marked & self.newly_stale)

    @property
    def mark_fp(self) -> int:
        return len(self.newly_marked - self.newly_stale)

    @property
    def mark_fn(self) -> int:
        return len(self.newly_stale - self.newly_marked)

    @property
    def false_trust(self) -> int:
        return len(self.trusted & self.stale)

    @property
    def available(self) -> int:
        return len(self.trusted & self.valid)

    @property
    def mark_precision(self) -> float | None:
        return ratio(self.mark_tp, len(self.newly_marked))

    @property
    def mark_recall(self) -> float | None:
        return ratio(self.mark_tp, len(self.newly_stale))

    @property
    def mark_f1(self) -> float | None:
        precision = self.mark_precision
        recall = self.mark_recall
        if precision is None or recall is None or precision + recall == 0:
            return None
        return 2 * precision * recall / (precision + recall)

    @property
    def reviews_per_hit(self) -> float | None:
        return ratio(len(self.newly_marked), self.mark_tp)

    @property
    def false_trust_rate(self) -> float | None:
        return ratio(self.false_trust, len(self.stale))

    @property
    def staleness_at_use(self) -> float | None:
        return ratio(self.false_trust, len(self.trusted))

    @property
    def availability(self) -> float | None:
        return ratio(self.available, len(self.valid))

    @property
    def review_load(self) -> int:
        return len(self.pending)

    def as_row(self) -> dict:
        return {
            "scenario": self.scenario,
            "variant": self.variant,
            "family": self.family,
            "step": self.step,
            "label": self.label,
            "marked": "|".join(sorted(self.marked)),
            "newly_marked": "|".join(sorted(self.newly_marked)),
            "newly_stale": "|".join(sorted(self.newly_stale)),
            "stale": "|".join(sorted(self.stale)),
            "trusted": "|".join(sorted(self.trusted)),
            "pending": "|".join(sorted(self.pending)),
            "mark_tp": self.mark_tp,
            "mark_fp": self.mark_fp,
            "mark_fn": self.mark_fn,
            "false_trust": self.false_trust,
            "available": self.available,
            "review_load": self.review_load,
            "mark_precision": self.mark_precision,
            "mark_recall": self.mark_recall,
            "mark_f1": self.mark_f1,
            "false_trust_rate": self.false_trust_rate,
            "staleness_at_use": self.staleness_at_use,
            "availability": self.availability,
            "reviews_per_hit": self.reviews_per_hit,
        }


@dataclass
class Accumulator:
    """Micro counts for the headline numbers, per-step ratios for intervals."""

    scenario: str = ""
    variant: str = ""
    family: str = ""
    records: list = field(default_factory=list)
    mark_tp: int = 0
    mark_fp: int = 0
    mark_fn: int = 0
    false_trust: int = 0
    available: int = 0
    stale_total: int = 0
    valid_total: int = 0
    review_total: int = 0
    steps: int = 0
    _precision: list = field(default_factory=list)
    _recall: list = field(default_factory=list)
    _f1: list = field(default_factory=list)
    _false_trust_rate: list = field(default_factory=list)
    _availability: list = field(default_factory=list)
    _reviews_per_hit: list = field(default_factory=list)

    def add(self, record: StepRecord) -> None:
        self.records.append(record)
        self.scenario = record.scenario
        self.variant = record.variant
        self.family = record.family
        self.mark_tp += record.mark_tp
        self.mark_fp += record.mark_fp
        self.mark_fn += record.mark_fn
        self.false_trust += record.false_trust
        self.available += record.available
        self.stale_total += len(record.stale)
        self.valid_total += len(record.valid)
        self.review_total += record.review_load
        self.steps += 1
        if record.mark_precision is not None:
            self._precision.append(record.mark_precision)
        if record.mark_recall is not None:
            self._recall.append(record.mark_recall)
        if record.mark_f1 is not None:
            self._f1.append(record.mark_f1)
        if record.false_trust_rate is not None:
            self._false_trust_rate.append(record.false_trust_rate)
        if record.availability is not None:
            self._availability.append(record.availability)
        if record.reviews_per_hit is not None:
            self._reviews_per_hit.append(record.reviews_per_hit)

    def micro(self, name: str) -> float | None:
        if name == "mark_precision":
            return ratio(self.mark_tp, self.mark_tp + self.mark_fp)
        if name == "mark_recall":
            return ratio(self.mark_tp, self.mark_tp + self.mark_fn)
        if name == "mark_f1":
            precision = self.micro("mark_precision")
            recall = self.micro("mark_recall")
            if precision is None or recall is None or precision + recall == 0:
                return None
            return 2 * precision * recall / (precision + recall)
        if name == "false_trust_rate":
            return ratio(self.false_trust, self.stale_total)
        if name == "availability":
            return ratio(self.available, self.valid_total)
        if name == "reviews_per_hit":
            marked = self.mark_tp + self.mark_fp
            return ratio(marked, self.mark_tp)
        raise KeyError(name)

    def samples(self, name: str) -> list:
        return {
            "mark_precision": self._precision,
            "mark_recall": self._recall,
            "mark_f1": self._f1,
            "false_trust_rate": self._false_trust_rate,
            "availability": self._availability,
            "reviews_per_hit": self._reviews_per_hit,
        }[name]

    def mean(self, name: str) -> float | None:
        values = self.samples(name)
        if not values:
            return None
        return sum(values) / len(values)

    def review_per_step(self) -> float:
        return self.review_total / self.steps if self.steps else 0.0

    def summary(self) -> dict:
        return {
            "variant": self.variant,
            "family": self.family,
            "steps": self.steps,
            "mark_precision": self.micro("mark_precision"),
            "mark_recall": self.micro("mark_recall"),
            "mark_f1": self.micro("mark_f1"),
            "reviews_per_hit": self.micro("reviews_per_hit"),
            "false_trust": self.false_trust,
            "false_trust_rate": self.micro("false_trust_rate"),
            "availability": self.micro("availability"),
            "review_per_step": self.review_per_step(),
        }


def aggregate(records: Iterable[StepRecord]) -> list[Accumulator]:
    """One accumulator per variant, in first-seen order."""
    order: list[str] = []
    buckets: dict[str, Accumulator] = {}
    for record in records:
        if record.variant not in buckets:
            buckets[record.variant] = Accumulator()
            order.append(record.variant)
        buckets[record.variant].add(record)
    return [buckets[name] for name in order]


def bootstrap_ci(
    values: Sequence,
    samples: int = 2000,
    seed: int = 0,
    alpha: float = 0.05,
) -> tuple:
    """Percentile bootstrap over per-step ratios; deterministic for a given seed."""
    clean = [value for value in values if value is not None]
    if not clean:
        return (None, None)
    if len(clean) == 1:
        return (clean[0], clean[0])
    rng = random.Random(seed)
    count = len(clean)
    means = []
    for _ in range(samples):
        total = 0.0
        for _ in range(count):
            total += clean[rng.randrange(count)]
        means.append(total / count)
    means.sort()
    low_index = int(alpha / 2 * samples)
    high_index = int((1 - alpha / 2) * samples) - 1
    return (means[low_index], means[max(low_index, high_index)])
