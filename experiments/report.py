"""Renders both experiments as CSV plus Markdown tables ready for a paper."""

from __future__ import annotations

import csv
from pathlib import Path

from experiments.metrics import bootstrap_ci, aggregate
from experiments.utility import summarise

GOVERNANCE_HEADER = (
    "condition",
    "family",
    "mark P",
    "mark R",
    "mark F1",
    "reviews/hit",
    "false trust",
    "availability",
    "pending/step",
)

UTILITY_HEADER = (
    "condition",
    "policy",
    "answer acc",
    "abstain",
    "harmful",
    "files/task",
)


def _num(value, digits: int = 3) -> str:
    if value is None:
        return "-"
    return ("%." + str(digits) + "f") % value


def _markdown(header, rows) -> str:
    widths = [len(str(cell)) for cell in header]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(str(cell)))

    def render(cells) -> str:
        padded = []
        for index, cell in enumerate(cells):
            text = str(cell)
            padded.append(text.ljust(widths[index]))
        return "| " + " | ".join(padded) + " |"

    lines = [render(header)]
    lines.append("| " + " | ".join("-" * width for width in widths) + " |")
    lines.extend(render(row) for row in rows)
    return "\n".join(lines)


def _interval(values, seed: int, samples: int) -> str:
    low, high = bootstrap_ci(values, samples=samples, seed=seed)
    if low is None:
        return "-"
    return "[%s, %s]" % (_num(low), _num(high))


def governance_table(records, seed: int = 0, samples: int = 2000, show_ci: bool = False) -> str:
    accumulators = aggregate(records)
    rows = []
    for acc in accumulators:
        false_trust = _num(acc.micro("false_trust_rate"))
        availability = _num(acc.micro("availability"))
        if show_ci:
            false_trust += " " + _interval(acc.samples("false_trust_rate"), seed, samples)
            availability += " " + _interval(acc.samples("availability"), seed, samples)
        rows.append(
            (
                acc.variant,
                acc.family,
                _num(acc.micro("mark_precision")),
                _num(acc.micro("mark_recall")),
                _num(acc.micro("mark_f1")),
                _num(acc.micro("reviews_per_hit"), 2),
                false_trust,
                availability,
                _num(acc.review_per_step(), 2),
            )
        )
    return _markdown(GOVERNANCE_HEADER, rows)


def utility_table(outcomes) -> str:
    rows = []
    for summary in summarise(outcomes):
        rows.append(
            (
                summary.variant,
                summary.policy,
                _num(summary.accuracy),
                _num(summary.abstain_rate),
                _num(summary.harmful_rate),
                _num(summary.files_per_task, 2),
            )
        )
    return _markdown(UTILITY_HEADER, rows)


def write_governance_csv(records, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = [record.as_row() for record in records]
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else ["scenario"])
        writer.writeheader()
        writer.writerows(rows)
    return target


def write_utility_csv(outcomes, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = [outcome.as_row() for outcome in outcomes]
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else ["scenario"])
        writer.writeheader()
        writer.writerows(rows)
    return target


def report(
    scenario_name: str,
    records,
    outcomes,
    seed: int = 0,
    samples: int = 2000,
    show_ci: bool = False,
) -> str:
    parts = ["# Evaluation report", "", "Scenario: `%s`" % scenario_name, ""]
    parts.append("## Belief governance")
    parts.append("")
    parts.append(
        "false trust is the share of already-stale specs still returned as trusted. "
        "availability is the share of still-true specs the system is willing to hand out. "
        "A cautious system can drive the first to zero by refusing everything, so both are reported."
    )
    parts.append("")
    parts.append(governance_table(records, seed=seed, samples=samples, show_ci=show_ci))
    if outcomes:
        parts.append("")
        parts.append("## Task utility")
        parts.append("")
        parts.append(
            "harmful counts answers that were given and wrong, which is the failure mode a "
            "permissive memory produces. abstain is the share of questions the policy refused, "
            "which is what a governed memory trades harmful answers for."
        )
        parts.append("")
        parts.append(utility_table(outcomes))
    parts.append("")
    return "\n".join(parts)
