from __future__ import annotations

import fnmatch
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from specgraph.database import create_spec as create_spec_node
from specgraph.database import update_belief
from specgraph.errors import SpecGraphError
from specgraph.fingerprint import Fingerprint, SymbolChange, diff_fingerprints, language_of
from specgraph.model import SPEC_TYPES, VERDICTS, SpecGraph, SpecNode
from specgraph.query import recall, temporally_current
from specgraph.store import (
    append_event,
    code_fingerprints,
    load_graph,
    load_snapshot,
    save_snapshot,
)
from specgraph.timeutil import dump_time, utcnow


@dataclass
class FileDelta:
    path: str
    changes: list[SymbolChange] = field(default_factory=list)
    nonstructural: bool = False


@dataclass
class ScanReport:
    baseline: bool
    marked: list[dict]
    nonstructural: list[str]
    unparsed: list[str]


@dataclass
class Consumption:
    status: str
    spec_id: str
    message: str
    node: object = None


def scan(root, since: str | None = None, now: datetime | None = None) -> ScanReport:
    root = Path(root).resolve()
    moment = now or utcnow()
    current = code_fingerprints(root)
    if since:
        deltas = _git_deltas(root, since)
    else:
        previous = load_snapshot(root)
        if previous is None:
            save_snapshot(root, current)
            append_event(root, _event("baseline", moment, detail="structure snapshot created"))
            return ScanReport(True, [], [], [])
        deltas = _snapshot_deltas(previous, current)

    graph = load_graph(root)
    direct: dict[str, list[str]] = {}
    nonstructural: list[str] = []
    unparsed: list[str] = []
    for delta in deltas:
        if delta.nonstructural:
            nonstructural.append(delta.path)
            continue
        if any(change.symbol == "*" for change in delta.changes):
            unparsed.append(delta.path)
        for node in graph.nodes.values():
            hits = [
                f"{change.symbol} {change.action}"
                for change in delta.changes
                if _anchor_hit(node.anchors, delta.path, change.symbol)
            ]
            if hits:
                direct.setdefault(node.id, []).extend(f"{delta.path} {hit}" for hit in hits)

    marked = []
    for spec_id in sorted(_propagate(graph, set(direct))):
        node = graph.nodes[spec_id]
        if node.belief == "invalidated":
            continue
        role = _role(graph, direct, spec_id)
        evidence = direct.get(spec_id) or _evidence_from_direct(direct)
        reason = f"{role}: " + "; ".join(evidence)
        event_id = _apply_belief(
            root,
            node,
            belief="suspicious",
            reason=reason,
            moment=moment,
            close_interval=False,
        )
        append_event(
            root,
            _event(
                "perturb",
                moment,
                event_id=event_id,
                spec_id=spec_id,
                detail=reason,
                extra={"role": role, "evidence": evidence},
            ),
        )
        marked.append({"id": spec_id, "role": role, "reason": reason, "event": event_id})

    save_snapshot(root, current)
    return ScanReport(False, marked, nonstructural, unparsed)


def verify(root, spec_id: str, verdict: str, reason: str, now: datetime | None = None) -> dict:
    if verdict not in VERDICTS:
        raise SpecGraphError(f"verdict must be one of {', '.join(VERDICTS)}")
    if not reason or not reason.strip():
        raise SpecGraphError("a verification reason is required")
    root = Path(root).resolve()
    graph = load_graph(root)
    node = graph.nodes.get(spec_id)
    if node is None:
        raise SpecGraphError(f"unknown spec {spec_id}")
    moment = now or utcnow()
    event_id = _apply_belief(
        root,
        node,
        belief=verdict,
        reason=reason.strip(),
        moment=moment,
        close_interval=verdict == "invalidated",
    )
    event = _event(
        "verify",
        moment,
        event_id=event_id,
        spec_id=spec_id,
        detail=reason.strip(),
        extra={"verdict": verdict},
    )
    append_event(root, event)
    return event


def consume(root, spec_id: str, as_of: datetime | None = None) -> Consumption:
    graph = load_graph(root)
    node = graph.nodes.get(spec_id)
    moment = as_of or utcnow()
    if node is None:
        return Consumption("absent", spec_id, "no spec with this id")
    if node.type == "task-spec" and node.status == "done":
        return Consumption("absent", spec_id, "finished task-spec is not active knowledge", node)
    if node.belief == "invalidated" or not temporally_current(node, moment):
        return Consumption("absent", spec_id, "outside its validity interval or invalidated", node)
    if node.belief == "held":
        found = recall(graph, moment)
        if any(item.id == spec_id for item in found.trusted):
            return Consumption("trusted", spec_id, "held and inside its validity interval", node)
    if node.belief in {"suspicious", "unverifiable"}:
        return Consumption(
            "pending",
            spec_id,
            "do not use until verify records held, invalidated, or unverifiable",
            node,
        )
    return Consumption("absent", spec_id, f"belief {node.belief} is not consumable", node)


def create_spec(
    root,
    spec_id: str,
    spec_type: str,
    title: str,
    statement: str = "",
    boundary: str = "",
    parent: str | None = None,
    anchors: list[str] | None = None,
    depends_on: list[str] | None = None,
    implements: list[str] | None = None,
    now: datetime | None = None,
) -> str:
    if spec_type not in SPEC_TYPES:
        raise SpecGraphError(f"type must be one of {', '.join(SPEC_TYPES)}")
    root = Path(root).resolve()
    moment = now or utcnow()
    body = "\n\n".join(part.strip() for part in (statement, boundary) if part and part.strip())
    create_spec_node(
        root,
        spec_id,
        spec_type,
        title,
        body,
        moment,
        parent=parent,
        anchors=anchors,
        depends_on=depends_on,
        implements=implements,
    )
    return spec_id


def _apply_belief(root, node: SpecNode, belief: str, reason: str, moment: datetime, close_interval: bool) -> str:
    identifier = uuid.uuid4().hex[:12]
    if belief == "held":
        perturbed: list[str] = []
    else:
        perturbed = [*node.perturbed_by, identifier][-20:]
    valid_to = node.valid_to
    if close_interval and (valid_to is None or valid_to > moment):
        valid_to = moment
    update_belief(root, node.id, belief, reason, moment, perturbed, valid_to)
    return identifier


def _propagate(graph: SpecGraph, direct: set[str]) -> set[str]:
    hit = set(direct)
    for spec_id in direct:
        for edge in graph.outgoing(spec_id, "implements"):
            if edge.dst in graph.nodes:
                hit.add(edge.dst)
        for edge in graph.incoming(spec_id, "implements"):
            hit.add(edge.src)
        for edge in graph.incoming(spec_id, "depends-on"):
            hit.add(edge.src)
    return hit


def _role(graph: SpecGraph, direct: dict[str, list[str]], spec_id: str) -> str:
    if spec_id in direct:
        return "anchor"
    roles = []
    if any(edge.kind == "implements" and edge.dst == spec_id and edge.src in direct for edge in graph.edges):
        roles.append("implements-target")
    if any(edge.kind == "implements" and edge.src == spec_id and edge.dst in direct for edge in graph.edges):
        roles.append("implementer")
    if any(edge.kind == "depends-on" and edge.src == spec_id and edge.dst in direct for edge in graph.edges):
        roles.append("depends-on")
    return "+".join(roles) or "traced"


def _evidence_from_direct(direct: dict[str, list[str]]) -> list[str]:
    evidence = []
    for spec_id in sorted(direct):
        evidence.extend(f"via {spec_id}: {item}" for item in direct[spec_id])
    return evidence


def _anchor_hit(anchors: list[str], rel_path: str, symbol: str) -> bool:
    for anchor in anchors:
        path_part, sep, wanted = anchor.partition("#")
        if not _path_matches(path_part, rel_path):
            continue
        if not sep:
            return True
        if symbol == wanted:
            return True
    return False


def _path_matches(pattern: str, rel_path: str) -> bool:
    pattern = pattern.replace("\\", "/").strip().rstrip("/")
    rel_path = rel_path.replace("\\", "/")
    if pattern == "":
        return False
    if any(char in pattern for char in "*?["):
        return _glob_match(pattern.split("/"), rel_path.split("/"))
    leaf = pattern.split("/")[-1]
    if "." not in leaf:
        return rel_path == pattern or rel_path.startswith(pattern + "/")
    return rel_path == pattern


def _glob_match(pattern: list[str], path: list[str]) -> bool:
    def walk(i: int, j: int) -> bool:
        if i == len(pattern):
            return j == len(path)
        if pattern[i] == "**":
            return walk(i + 1, j) or (j < len(path) and walk(i, j + 1))
        if j == len(path):
            return False
        if fnmatch.fnmatch(path[j], pattern[i]):
            return walk(i + 1, j + 1)
        return False

    return walk(0, 0)


def _snapshot_deltas(previous: dict[str, dict], current: dict[str, dict]) -> list[FileDelta]:
    deltas = []
    for path in sorted(set(previous) | set(current)):
        old_payload = previous.get(path)
        new_payload = current.get(path)
        old = Fingerprint.from_json(old_payload) if old_payload else None
        if new_payload is None:
            assert old is not None
            deltas.append(FileDelta(path, diff_fingerprints(old, Fingerprint("unsupported"))))
            continue
        new = Fingerprint.from_json(new_payload)
        changes = diff_fingerprints(old, new)
        text_changed = old_payload is not None and old_payload.get("text") != new_payload.get("text")
        if changes:
            deltas.append(FileDelta(path, changes))
        elif text_changed:
            deltas.append(FileDelta(path, nonstructural=True))
    return deltas


def _git_deltas(root: Path, ref: str) -> list[FileDelta]:
    try:
        diff = subprocess.run(
            ["git", "diff", "--name-status", "--find-renames", ref, "--"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise SpecGraphError("git is not available") from exc
    if diff.returncode != 0:
        raise SpecGraphError(diff.stderr.strip() or f"git diff {ref} failed")
    deltas: list[FileDelta] = []
    seen: set[str] = set()
    for line in diff.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        if status.startswith(("R", "C")) and len(parts) >= 3:
            deltas.extend(_pair_delta(root, ref, parts[1], parts[2]))
            seen.add(parts[2])
        elif status.startswith("D") and len(parts) >= 2:
            deltas.extend(_pair_delta(root, ref, parts[1], None))
        elif status.startswith(("A", "M")) and len(parts) >= 2:
            deltas.extend(_pair_delta(root, ref, None if status.startswith("A") else parts[1], parts[1]))
            seen.add(parts[1])
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if untracked.returncode == 0:
        for rel in untracked.stdout.splitlines():
            if rel not in seen and language_of(rel):
                deltas.extend(_pair_delta(root, ref, None, rel))
    return [delta for delta in deltas if delta.changes or delta.nonstructural]


def _pair_delta(root: Path, ref: str, old_path: str | None, new_path: str | None) -> list[FileDelta]:
    path = new_path or old_path
    if path is None or language_of(path) is None and (old_path is None or language_of(old_path) is None):
        return []
    old_source = _git_show(root, ref, old_path) if old_path else None
    new_source = _read_worktree(root, new_path) if new_path else None
    compare_path = new_path or old_path or path
    old_fp = fingerprint_of(old_path or compare_path, old_source)
    new_fp = fingerprint_of(new_path or compare_path, new_source)
    if old_source is not None and new_source is not None and old_fp.symbols == new_fp.symbols and old_fp.parser == new_fp.parser:
        if old_source != new_source:
            return [FileDelta(compare_path, nonstructural=True)]
        return []
    changes = diff_fingerprints(old_fp if old_source is not None else None, new_fp if new_source is not None else Fingerprint("unsupported"))
    if new_source is None and old_fp.symbols:
        changes = [SymbolChange(key, "removed") for key in old_fp.symbols]
    return [FileDelta(compare_path, changes)] if changes else []


def fingerprint_of(path: str, source: str | None) -> Fingerprint:
    from specgraph.fingerprint import fingerprint_source

    if source is None:
        return Fingerprint("unsupported")
    return fingerprint_source(path, source)


def _git_show(root: Path, ref: str, path: str) -> str | None:
    proc = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0 or b"\0" in proc.stdout:
        return None
    return proc.stdout.decode("utf-8", errors="replace")


def _read_worktree(root: Path, path: str) -> str | None:
    file_path = root / path
    if not file_path.is_file():
        return None
    data = file_path.read_bytes()
    if b"\0" in data:
        return None
    return data.decode("utf-8", errors="replace")


def _event(kind: str, moment: datetime, event_id: str | None = None, spec_id: str | None = None, detail: str = "", extra: dict | None = None) -> dict:
    event = {
        "id": event_id or uuid.uuid4().hex[:12],
        "at": dump_time(moment),
        "op": kind,
        "detail": detail,
    }
    if spec_id:
        event["spec_id"] = spec_id
    if extra:
        event.update(extra)
    return event
