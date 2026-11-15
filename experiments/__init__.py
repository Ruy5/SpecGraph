"""Offline evaluation harness for SpecGraph.

Not imported by the library: ``specgraph`` stays free of experiment-only code,
so the shipped wheel is unchanged. Run it with ``python -m experiments``.
"""

from experiments.metrics import StepRecord, aggregate, bootstrap_ci, ratio
from experiments.runner import run_all, run_scenario, step_time
from experiments.scenario import (
    FileChange,
    Scenario,
    SpecDecl,
    Step,
    Task,
    describe,
    load_scenario,
    validate_scenario,
)
from experiments.utility import (
    POLICIES,
    BlindPolicy,
    MemoryView,
    NoMemoryPolicy,
    TrustedOnlyPolicy,
    run_tasks,
    summarise,
)
from experiments.variants import ALL_VARIANTS, Variant, by_name, family

__all__ = [
    "ALL_VARIANTS",
    "BlindPolicy",
    "FileChange",
    "MemoryView",
    "NoMemoryPolicy",
    "POLICIES",
    "Scenario",
    "SpecDecl",
    "Step",
    "StepRecord",
    "Task",
    "TrustedOnlyPolicy",
    "Variant",
    "aggregate",
    "bootstrap_ci",
    "by_name",
    "describe",
    "family",
    "load_scenario",
    "ratio",
    "run_all",
    "run_scenario",
    "run_tasks",
    "step_time",
    "summarise",
    "validate_scenario",
]
