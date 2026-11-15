"""Entry point: ``python -m experiments <command> --scenario <path>``.

By default a run writes its spec graph to Neo4j under a temporary project key
and asks a DeepSeek-R1-Distill-Qwen-1.5B endpoint for verdicts. Pass
``--store memory --reviewer oracle`` to use the offline stand-ins instead; that
path needs neither a database nor a model server.

The scenario supplies code structure, the harness runs the real governance
code, and the report is written to disk.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from specgraph.errors import SpecGraphError

from experiments.report import report, write_governance_csv, write_utility_csv
from experiments.reviewer import DeepSeekReviewer, OracleReviewer
from experiments.runner import run_all
from experiments.scenario import describe, load_scenario
from experiments.utility import POLICIES, run_tasks
from experiments.variants import ALL_VARIANTS, by_name


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="experiments",
        description="Evaluation of SpecGraph's belief governance and task utility.",
        epilog=(
            "Neo4j reads SPECGRAPH_NEO4J_URI, SPECGRAPH_NEO4J_USER, "
            "SPECGRAPH_NEO4J_PASSWORD and SPECGRAPH_NEO4J_DATABASE. "
            "The reviewer reads SPECGRAPH_REVIEWER_BASE_URL, "
            "SPECGRAPH_REVIEWER_MODEL and SPECGRAPH_REVIEWER_API_KEY. "
            "For a run with no external services, pass "
            "--store memory --reviewer oracle."
        ),
    )
    parser.add_argument("command", choices=("governance", "utility", "all"),
                        help="which experiment to run")
    parser.add_argument("--scenario", action="append", required=True, metavar="PATH",
                        help="scenario JSON; repeat for several scenarios")
    parser.add_argument("--out", default="results", metavar="DIR",
                        help="directory for report.md and the CSV files")
    parser.add_argument("--variants", default="all",
                        help="comma-separated condition names, or 'all'")
    parser.add_argument("--seed", type=int, default=0,
                        help="bootstrap seed; the simulation itself is deterministic")
    parser.add_argument("--bootstrap", type=int, default=2000, metavar="N",
                        help="bootstrap resamples for confidence intervals")
    parser.add_argument("--ci", action="store_true",
                        help="append 95%% bootstrap intervals to the main table")
    parser.add_argument("--store", choices=("neo4j", "memory"), default="neo4j",
                        help="graph store: Neo4j (default) or the offline memory stand-in")
    parser.add_argument("--reviewer", choices=("deepseek", "oracle"), default="deepseek",
                        help="verdict source: the served model (default) or scenario ground truth")
    parser.add_argument("--reviewer-model", default=None, metavar="MODEL",
                        help="model id; defaults to SPECGRAPH_REVIEWER_MODEL")
    parser.add_argument("--reviewer-base-url", default=None, metavar="URL",
                        help="OpenAI-compatible base URL; defaults to SPECGRAPH_REVIEWER_BASE_URL")
    return parser


def _select(value: str):
    if value.strip().lower() == "all":
        return list(ALL_VARIANTS)
    names = [name.strip() for name in value.split(",") if name.strip()]
    if not names:
        raise SystemExit("--variants needs at least one name or 'all'")
    return [by_name(name) for name in names]


def _reviewer(args):
    if args.reviewer == "oracle":
        return OracleReviewer()
    reviewer = DeepSeekReviewer.from_env()
    if args.reviewer_model:
        reviewer = replace(reviewer, model=args.reviewer_model)
    if args.reviewer_base_url:
        reviewer = replace(reviewer, base_url=args.reviewer_base_url.rstrip("/"))
    return reviewer


def _run(args, variants, reviewer) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    blocks = []

    for raw_path in args.scenario:
        try:
            scenario = load_scenario(raw_path)
        except (OSError, ValueError) as exc:
            print("experiments: %s" % exc, file=sys.stderr)
            return 2

        print(describe(scenario))
        records = run_all(scenario, variants, reviewer=reviewer, store=args.store)
        write_governance_csv(records, out / (scenario.name + "-steps.csv"))

        outcomes = []
        if args.command in ("utility", "all"):
            for variant in variants:
                for policy in POLICIES:
                    outcomes.extend(
                        run_tasks(
                            scenario,
                            variant,
                            policy,
                            records=records,
                            reviewer=reviewer,
                            store=args.store,
                        )
                    )
            write_utility_csv(outcomes, out / (scenario.name + "-tasks.csv"))

        blocks.append(
            report(
                scenario.name,
                records,
                outcomes,
                seed=args.seed,
                samples=args.bootstrap,
                show_ci=args.ci,
            )
        )

    text = "\n\n---\n\n".join(blocks)
    target = out / "report.md"
    target.write_text(text + "\n", encoding="utf-8")
    print(text)
    print("\nwrote %s" % target)
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        variants = _select(args.variants)
    except KeyError as exc:
        print("experiments: %s" % exc, file=sys.stderr)
        return 2

    try:
        return _run(args, variants, _reviewer(args))
    except SpecGraphError as exc:
        print("experiments: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
