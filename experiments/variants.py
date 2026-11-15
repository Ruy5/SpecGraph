"""The experimental conditions: two baselines, four ablations, one system.

Every condition runs the real governance code. A condition differs only by a
named switch, so a reviewer can see that exactly one mechanism was turned off.
"""

from __future__ import annotations

import uuid
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import Iterator

from specgraph import govern, query


@dataclass(frozen=True)
class Variant:
    name: str
    family: str
    scans: bool = True
    propagate: bool = True
    symbol_anchors: bool = True
    three_state: bool = True
    temporal: bool = True
    verify: bool = True
    ttl_steps: int | None = None
    note: str = ""


FULL = Variant(
    name="specgraph",
    family="system",
    note="scan marks, verify decides, elapsed intervals filter recall",
)
STATIC = Variant(
    name="static_memory",
    family="baseline",
    scans=False,
    verify=False,
    note="specs written once and never revised; the usual Markdown file",
)
TTL = Variant(
    name="ttl_memory",
    family="baseline",
    scans=False,
    verify=False,
    ttl_steps=4,
    note="fixed lifetime per spec, blind to what the code does",
)
STRUCT_ONLY = Variant(
    name="struct_only",
    family="baseline",
    three_state=False,
    note="a structural change invalidates a spec outright, no reviewer",
)
NO_PROPAGATION = Variant(
    name="no_propagation",
    family="ablation",
    propagate=False,
    note="only specs whose own anchors changed are marked",
)
NO_TEMPORAL = Variant(
    name="no_temporal",
    family="ablation",
    temporal=False,
    note="validity intervals ignored at recall time",
)
NO_VERIFY = Variant(
    name="no_verify",
    family="ablation",
    verify=False,
    note="suspicions accumulate; nobody records a verdict",
)
FILE_ANCHORS = Variant(
    name="file_anchors",
    family="ablation",
    symbol_anchors=False,
    note="anchors match whole files instead of symbols",
)

ALL_VARIANTS = (FULL, STATIC, TTL, STRUCT_ONLY, NO_PROPAGATION, NO_TEMPORAL, NO_VERIFY, FILE_ANCHORS)

_BY_NAME = {variant.name: variant for variant in ALL_VARIANTS}


def by_name(name: str) -> Variant:
    try:
        return _BY_NAME[name]
    except KeyError:
        raise KeyError("unknown variant %r; known: %s"
                       % (name, ", ".join(sorted(_BY_NAME)))) from None


def family(family_name: str) -> tuple[Variant, ...]:
    return tuple(variant for variant in ALL_VARIANTS if variant.family == family_name)


@contextmanager
def _replace(module, attribute: str, value) -> Iterator[None]:
    saved = getattr(module, attribute)
    setattr(module, attribute, value)
    try:
        yield
    finally:
        setattr(module, attribute, saved)


def _no_propagation(graph, direct):
    return set(direct)


def _file_level_anchor_hit(anchors, rel_path, symbol):
    for anchor in anchors:
        path_part = anchor.partition("#")[0]
        if govern._path_matches(path_part, rel_path):
            return True
    return False


def _invalidating_apply_belief(root, node, belief, reason, moment, close_interval):
    """Turn 'suspicious' into 'invalidated' without asking anyone."""
    identifier = uuid.uuid4().hex[:12]
    final = "invalidated" if belief == "suspicious" else belief
    perturbed = [] if final == "held" else [*node.perturbed_by, identifier][-20:]
    valid_to = moment if final == "invalidated" else node.valid_to
    govern.update_belief(root, node.id, final, reason, moment, perturbed, valid_to)
    return identifier


def _always_current(node, moment):
    return True


@contextmanager
def activated(variant: Variant) -> Iterator[Variant]:
    """Apply the switches that turn the full system into ``variant``."""
    with ExitStack() as stack:
        if not variant.propagate:
            stack.enter_context(_replace(govern, "_propagate", _no_propagation))
        if not variant.symbol_anchors:
            stack.enter_context(_replace(govern, "_anchor_hit", _file_level_anchor_hit))
        if not variant.three_state:
            stack.enter_context(_replace(govern, "_apply_belief", _invalidating_apply_belief))
        if not variant.temporal:
            stack.enter_context(_replace(govern, "temporally_current", _always_current))
            stack.enter_context(_replace(query, "temporally_current", _always_current))
        yield variant


def describe(variant: Variant) -> str:
    return "%-16s %-9s %s" % (variant.name, variant.family, variant.note)
