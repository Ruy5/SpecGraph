"""Command-line entry point for the SpecGraph governance loop.

Local commands (``fingerprint``, ``--help``) work without the Neo4j driver.
Graph commands load the driver lazily and report connection problems as
``SpecGraphError`` instead of a traceback.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime
from pathlib import Path

from specgraph.database import load_graph, project_key
from specgraph.errors import SpecGraphError
from specgraph.govern import (
    consume,
    create_spec,
    events,
    link,
    scan,
    set_status,
    verify,
)
from specgraph.model import LINK_KINDS, SPEC_STATUSES, SPEC_TYPES, VERDICTS
from specgraph.query import recall, slice_graph
from specgraph.store import code_fingerprints
from specgraph.timeutil import dump_time, parse_time
from specgraph.validate import validate_graph


@dataclass
class Result:
    payload: object
    lines: list[str] = field(default_factory=list)
    exit_code: int = 0


def _common() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--root",
        default=argparse.SUPPRESS,
        help="project root (default: the current directory)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="emit machine-readable JSON",
    )
    return parser


def build_parser() -> argparse.ArgumentParser:
    common = _common()
    parser = argparse.ArgumentParser(
        prog="specgraph",
        description="Project-level temporal spec graph with belief governance.",
    )
    parser.add_argument("--root", default=".", help="project root (default: the current directory)")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    p = sub.add_parser("init", parents=[common], help="check Neo4j and create schema constraints")
    p.set_defaults(handler=_cmd_init)

    p = sub.add_parser("create", parents=[common], help="write a spec node")
    p.add_argument("spec_id")
    p.add_argument("--type", required=True, choices=SPEC_TYPES)
    p.add_argument("--title", required=True)
    p.add_argument("--statement", default="")
    p.add_argument("--boundary", default="")
    p.add_argument("--parent")
    p.add_argument("--anchor", action="append", default=[], dest="anchors", metavar="PATH#SYMBOL")
    p.add_argument("--depends-on", action="append", default=[], dest="depends_on")
    p.add_argument("--implements", action="append", default=[], dest="implements")
    p.add_argument("--references", action="append", default=[], dest="references")
    p.add_argument("--tag", action="append", default=[], dest="tags")
    p.add_argument("--status", default="active", choices=SPEC_STATUSES)
    p.set_defaults(handler=_cmd_create)

    p = sub.add_parser("link", parents=[common], help="relate two existing specs")
    p.add_argument("src")
    p.add_argument("dst")
    p.add_argument("--kind", default="depends-on", choices=LINK_KINDS)
    p.set_defaults(handler=_cmd_link)

    p = sub.add_parser("status", parents=[common], help="change a spec's authoring status")
    p.add_argument("spec_id")
    p.add_argument("--set", required=True, dest="status", choices=SPEC_STATUSES)
    p.set_defaults(handler=_cmd_status)

    p = sub.add_parser("scan", parents=[common], help="diff code structure and mark specs suspicious")
    p.add_argument("--since", metavar="GIT_REF", help="compare against a git revision instead of the snapshot")
    p.set_defaults(handler=_cmd_scan)

    p = sub.add_parser("recall", parents=[common], help="list trusted and pending specs at a point in time")
    p.add_argument("--text", help="case-insensitive filter over id, title, tags and body")
    p.add_argument("--as-of", dest="as_of", metavar="TIMESTAMP")
    p.set_defaults(handler=_cmd_recall)

    p = sub.add_parser("consume", parents=[common], help="decide whether one spec may be used")
    p.add_argument("spec_id")
    p.add_argument("--as-of", dest="as_of", metavar="TIMESTAMP")
    p.set_defaults(handler=_cmd_consume)

    p = sub.add_parser("verify", parents=[common], help="record a three-state verdict for a spec")
    p.add_argument("spec_id")
    p.add_argument("--verdict", required=True, choices=VERDICTS)
    p.add_argument("--reason", required=True, help="why the verdict was reached")
    p.set_defaults(handler=_cmd_verify)

    p = sub.add_parser("slice", parents=[common], help="walk the graph around one spec")
    p.add_argument("spec_id")
    p.add_argument("--direction", default="neighbors", choices=("ancestors", "subtree", "neighbors"))
    p.add_argument("--depth", type=int, default=1)
    p.add_argument("--as-of", dest="as_of", metavar="TIMESTAMP")
    p.set_defaults(handler=_cmd_slice)

    p = sub.add_parser("events", parents=[common], help="show the governance trail")
    p.add_argument("--spec", dest="spec_id", help="only events attached to this spec")
    p.set_defaults(handler=_cmd_events)

    p = sub.add_parser("validate", parents=[common], help="check the loaded graph for structural problems")
    p.set_defaults(handler=_cmd_validate)

    p = sub.add_parser("fingerprint", parents=[common], help="show code symbols without touching Neo4j")
    p.add_argument("--symbols", action="store_true", help="include every symbol and its signature")
    p.set_defaults(handler=_cmd_fingerprint)

    return parser


def _moment(args) -> datetime | None:
    return parse_time(getattr(args, "as_of", None))


def _cmd_init(args) -> Result:
    graph = load_graph(args.root)
    payload = {
        "project": project_key(Path(args.root)),
        "specs": len(graph.nodes),
        "edges": len(graph.edges),
    }
    return Result(payload, [f"Neo4j ready: {payload['specs']} specs, {payload['edges']} edges "
                            f"(project {payload['project']})"])


def _cmd_create(args) -> Result:
    create_spec(
        args.root,
        args.spec_id,
        args.type,
        args.title,
        statement=args.statement,
        boundary=args.boundary,
        parent=args.parent,
        anchors=args.anchors,
        depends_on=args.depends_on,
        implements=args.implements,
        references=args.references,
        tags=args.tags,
        status=args.status,
    )
    return Result({"id": args.spec_id, "type": args.type, "status": args.status},
                  [f"created {args.spec_id} ({args.type}, {args.status})"])


def _cmd_link(args) -> Result:
    link(args.root, args.src, args.dst, args.kind)
    return Result({"src": args.src, "dst": args.dst, "kind": args.kind},
                  [f"linked {args.src} -{args.kind}-> {args.dst}"])


def _cmd_status(args) -> Result:
    set_status(args.root, args.spec_id, args.status)
    return Result({"id": args.spec_id, "status": args.status},
                  [f"{args.spec_id}: status -> {args.status}"])


def _cmd_scan(args) -> Result:
    report = scan(args.root, since=getattr(args, "since", None))
    payload = asdict(report)
    lines = []
    if report.baseline:
        lines.append("baseline structure snapshot created; no spec was marked")
    else:
        lines.append(f"marked {len(report.marked)} spec(s) suspicious")
        for item in report.marked:
            lines.append(f"  {item['id']} [{item['role']}] {item['reason']}")
        if report.nonstructural:
            lines.append(f"non-structural changes: {', '.join(report.nonstructural)}")
        if report.unparsed:
            lines.append(f"unparsed files: {', '.join(report.unparsed)}")
    return Result(payload, lines)


def _format_node(node) -> str:
    extra = f" - {node.belief_reason}" if node.belief_reason else ""
    return f"  {node.id} [{node.type}] {node.title}{extra}"


def _cmd_recall(args) -> Result:
    found = recall(load_graph(args.root), as_of=_moment(args), text=getattr(args, "text", None))
    payload = asdict(found)
    lines = [f"recall as of {dump_time(found.as_of)}",
             f"trusted ({len(found.trusted)}):"]
    lines.extend(_format_node(node) for node in found.trusted)
    lines.append(f"pending ({len(found.pending)}):")
    lines.extend(_format_node(node) for node in found.pending)
    return Result(payload, lines)


def _cmd_consume(args) -> Result:
    decision = consume(args.root, args.spec_id, as_of=_moment(args))
    payload = asdict(decision)
    lines = [f"{decision.spec_id}: {decision.status} - {decision.message}"]
    return Result(payload, lines)


def _cmd_verify(args) -> Result:
    event = verify(args.root, args.spec_id, args.verdict, args.reason)
    return Result(event, [f"verified {args.spec_id} -> {args.verdict} (event {event['id']})"])


def _cmd_slice(args) -> Result:
    rows = slice_graph(load_graph(args.root), args.spec_id, args.direction,
                       args.depth, as_of=_moment(args))
    lines = [f"slice {args.spec_id} / {args.direction} / depth {args.depth}"]
    lines.extend(f"  {'  ' * row.depth}{row.id} via {row.via or '-'} [{row.belief}]"
                 for row in rows)
    return Result([asdict(row) for row in rows], lines)


def _cmd_events(args) -> Result:
    trail = events(args.root)
    wanted = getattr(args, "spec_id", None)
    if wanted:
        trail = [item for item in trail if item.get("spec_id") == wanted]
    lines = [f"events ({len(trail)}):"]
    lines.extend(f"  {item.get('at', '')} {item.get('op', '')} "
                 f"{item.get('spec_id', '-')} {item.get('detail', '')}" for item in trail)
    return Result(trail, lines)


def _cmd_validate(args) -> Result:
    report = validate_graph(load_graph(args.root))
    payload = asdict(report)
    payload["ok"] = report.ok
    lines = ["graph is valid" if report.ok else "graph has problems"]
    for label in ("dangling", "duplicate_ids", "parent_cycles", "intervals", "beliefs", "warnings"):
        items = getattr(report, label)
        if items:
            lines.append(f"{label} ({len(items)}):")
            lines.extend(f"  {item}" for item in items)
    return Result(payload, lines, 0 if report.ok else 1)


def _cmd_fingerprint(args) -> Result:
    root = Path(args.root).resolve()
    files = code_fingerprints(root)
    payload = files
    lines = [f"fingerprint: {len(files)} code file(s) under {root}"]
    for path in sorted(files):
        symbols = files[path]["symbols"]
        lines.append(f"  {path} [{files[path]['parser']}] {len(symbols)} symbol(s)")
        if getattr(args, "symbols", False):
            lines.extend(f"    {name}: {signature}" for name, signature in sorted(symbols.items()))
    return Result(payload, lines)


def _json_default(value):
    if isinstance(value, datetime):
        return dump_time(value)
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    return str(value)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    raw_as_of = getattr(args, "as_of", None)
    if raw_as_of:
        try:
            parse_time(raw_as_of)
        except ValueError as exc:
            print(f"specgraph: invalid --as-of timestamp: {exc}", file=sys.stderr)
            return 2
    try:
        result = args.handler(args)
    except (SpecGraphError, KeyError, ValueError, OSError) as exc:
        print(f"specgraph: {exc}", file=sys.stderr)
        return 2
    if getattr(args, "json", False):
        print(json.dumps(result.payload, ensure_ascii=False, indent=2, default=_json_default))
    else:
        for line in result.lines:
            print(line)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
