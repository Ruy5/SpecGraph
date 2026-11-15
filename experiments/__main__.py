"""Entry point: ``python -m experiments <command> --scenario <path>``.

Nothing here touches Neo4j or an LLM. The scenario supplies code structure,
the harness runs the real governance code in memory, and the report is written
to disk. A run is deterministic for a given ``--seed``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from experiments.report import report, write_governance_csv, write_utility_csv
from experiments.runner import run_all
from experiments.scenario import describe, load_scenario
from experiments.utility import POLICIES, run_tasks
from experiments.variants import ALL_VARIANTS, by_name


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="experiments",
        description="Offline evaluation of SpecGraph's belief governance and task utility.",
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
    return parser


def _select(value: str):
    if value.strip().lower() == "all":
        return list(ALL_VARIANTS)
    names = [name.strip() for name in value.split(",") if name.strip()]
    if not names:
        raise SystemExit("--variants needs at least one name or 'all'")
    return [by_name(name) for name in names]


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        variants = _select(args.variants)
    except KeyError as exc:
        print("experiments: %s" % exc, file=sys.stderr)
        return 2

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
        records = run_all(scenario, variants)
        write_governance_csv(records, out / (scenario.name + "-steps.csv"))

        outcomes = []
        if args.command in ("utility", "all"):
            for variant in variants:
                for policy in POLICIES:
                    outcomes.extend(run_tasks(scenario, variant, policy, records=records))
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


if __name__ == "__main__":
    raise SystemExit(main())
