"""Neo4j persistence for the spec graph and its governance events."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from specgraph.errors import SpecGraphError
from specgraph.model import (
    DEFAULT_BELIEF,
    LINK_KINDS,
    SPEC_STATUSES,
    SpecEdge,
    SpecGraph,
    SpecNode,
)
from specgraph.timeutil import parse_time


def _neo4j_exceptions():
    """Load the driver lazily so the local, driver-free modules stay importable."""
    try:
        from neo4j.exceptions import AuthError, Neo4jError, ServiceUnavailable
    except ImportError as exc:
        raise SpecGraphError(
            "the neo4j driver is required for graph commands: pip install 'neo4j>=5.14,<6'"
        ) from exc
    return AuthError, Neo4jError, ServiceUnavailable


_REL = {
    "parent": "PARENT",
    "depends-on": "DEPENDS_ON",
    "references": "REFERENCES",
    "implements": "IMPLEMENTS",
}
_KIND = {rel: kind for kind, rel in _REL.items()}

_driver = None
_schema_ready = False


def project_key(root: Path) -> str:
    override = os.environ.get("SPECGRAPH_PROJECT", "").strip()
    if override:
        return override
    return str(Path(root).resolve())


def load_graph(root: Path) -> SpecGraph:
    root = Path(root)
    project = project_key(root)
    props, links = _run(False, _read_graph, project)
    nodes = {row["id"]: _node(row) for row in props}
    edges = [_edge(row) for row in links]
    return SpecGraph(root=root, nodes=nodes, edges=edges, duplicate_ids={})


def create_spec(
    root: Path,
    spec_id: str,
    spec_type: str,
    title: str,
    body: str,
    moment: datetime,
    parent: str | None = None,
    anchors: list[str] | None = None,
    depends_on: list[str] | None = None,
    implements: list[str] | None = None,
    references: list[str] | None = None,
    tags: list[str] | None = None,
    status: str = "active",
) -> None:
    if status not in SPEC_STATUSES:
        raise SpecGraphError(f"status must be one of {', '.join(SPEC_STATUSES)}")
    links: list[tuple[str, str]] = []
    if parent:
        links.append(("parent", parent))
    for kind, values in (
        ("depends-on", depends_on),
        ("implements", implements),
        ("references", references),
    ):
        for value in values or []:
            links.append((kind, value))
    _run(
        True,
        _create_spec,
        project_key(root),
        {
            "id": spec_id,
            "type": spec_type,
            "title": title,
            "body": body,
            "status": status,
            "belief": DEFAULT_BELIEF,
            "valid_from": moment,
            "valid_to": None,
            "belief_reason": None,
            "belief_at": None,
            "perturbed_by": [],
            "anchors": list(anchors or []),
            "tags": list(tags or []),
        },
        links,
    )


def link_specs(root: Path, src: str, dst: str, kind: str) -> None:
    if kind not in LINK_KINDS:
        raise SpecGraphError(f"link kind must be one of {', '.join(LINK_KINDS)}")
    _run(True, _link_specs, project_key(root), src, dst, kind)


def set_status(root: Path, spec_id: str, status: str, moment: datetime) -> None:
    if status not in SPEC_STATUSES:
        raise SpecGraphError(f"status must be one of {', '.join(SPEC_STATUSES)}")
    _run(True, _set_status, project_key(root), spec_id, status, moment)


def update_belief(
    root: Path,
    spec_id: str,
    belief: str,
    reason: str,
    moment: datetime,
    perturbed_by: list[str],
    valid_to: datetime | None,
) -> None:
    _run(
        True,
        _update_belief,
        project_key(root),
        spec_id,
        belief,
        reason,
        moment,
        perturbed_by,
        valid_to,
    )


def append_event(root: Path, event: dict) -> None:
    _run(True, _append_event, project_key(root), event)


def read_events(root: Path) -> list[dict]:
    return _run(False, _read_events, project_key(root))


def delete_project(root: Path) -> None:
    """Remove every spec and event stored for this project.

    The evaluation harness gives each run its own project key so repeated runs
    cannot inherit each other's beliefs, then drops it when the run ends.
    """
    _run(True, _delete_project, project_key(root))


def _read_graph(tx, project: str):
    props = [
        record["props"]
        for record in tx.run(
            "MATCH (s:Spec {project: $project}) RETURN properties(s) AS props ORDER BY s.id",
            project=project,
        )
    ]
    links = tx.run(
        """
        MATCH (a:Spec {project: $project})-[r]->(b:Spec {project: $project})
        WHERE type(r) IN $types
        RETURN a.id AS src, b.id AS dst, type(r) AS rel,
               r.valid_from AS valid_from, r.valid_to AS valid_to, r.belief AS belief
        ORDER BY src, rel, dst
        """,
        project=project,
        types=list(_REL.values()),
    ).data()
    return props, links


def _create_spec(tx, project: str, props: dict, links: list[tuple[str, str]]) -> None:
    existing = tx.run(
        "MATCH (s:Spec {project: $project, id: $id}) RETURN s.id AS id",
        project=project,
        id=props["id"],
    ).single()
    if existing is not None:
        raise SpecGraphError(f"spec {props['id']} already exists")
    tx.run(
        """
        CREATE (s:Spec {
            project: $project,
            id: $id,
            type: $type,
            title: $title,
            body: $body,
            status: $status,
            belief: $belief,
            valid_from: $valid_from,
            valid_to: $valid_to,
            belief_reason: $belief_reason,
            belief_at: $belief_at,
            perturbed_by: $perturbed_by,
            anchors: $anchors,
            tags: $tags
        })
        """,
        project=project,
        **props,
    )
    for kind, dst in links:
        _create_link(tx, project, props["id"], dst, kind)


def _create_link(tx, project: str, src: str, dst: str, kind: str) -> None:
    if kind not in LINK_KINDS:
        raise SpecGraphError(f"unknown link {kind}")
    found = tx.run(
        "MATCH (s:Spec {project: $project, id: $id}) RETURN s.id AS id",
        project=project,
        id=dst,
    ).single()
    if found is None:
        raise SpecGraphError(f"unknown spec {dst}")
    rel = _REL[kind]
    tx.run(
        f"""
        MATCH (a:Spec {{project: $project, id: $src}})
        MATCH (b:Spec {{project: $project, id: $dst}})
        MERGE (a)-[r:{rel}]->(b)
        """,
        project=project,
        src=src,
        dst=dst,
    )


def _link_specs(tx, project: str, src: str, dst: str, kind: str) -> None:
    for spec_id in (src, dst):
        found = tx.run(
            "MATCH (s:Spec {project: $project, id: $id}) RETURN s.id AS id",
            project=project,
            id=spec_id,
        ).single()
        if found is None:
            raise SpecGraphError(f"unknown spec {spec_id}")
    _create_link(tx, project, src, dst, kind)


def _set_status(tx, project: str, spec_id: str, status: str, moment: datetime) -> None:
    record = tx.run(
        """
        MATCH (s:Spec {project: $project, id: $id})
        SET s.status = $status, s.status_at = $moment
        RETURN s.id AS id
        """,
        project=project,
        id=spec_id,
        status=status,
        moment=moment,
    ).single()
    if record is None:
        raise SpecGraphError(f"unknown spec {spec_id}")


def _update_belief(tx, project, spec_id, belief, reason, moment, perturbed_by, valid_to) -> None:
    record = tx.run(
        """
        MATCH (s:Spec {project: $project, id: $id})
        SET s.belief = $belief,
            s.belief_reason = $reason,
            s.belief_at = $moment,
            s.perturbed_by = $perturbed_by,
            s.valid_to = $valid_to
        RETURN s.id AS id
        """,
        project=project,
        id=spec_id,
        belief=belief,
        reason=reason,
        moment=moment,
        perturbed_by=perturbed_by,
        valid_to=valid_to,
    ).single()
    if record is None:
        raise SpecGraphError(f"unknown spec {spec_id}")


def _append_event(tx, project: str, event: dict) -> None:
    tx.run(
        """
        CREATE (e:Event {
            project: $project,
            id: $id,
            at: $at,
            op: $op,
            detail: $detail,
            spec_id: $spec_id,
            payload: $payload
        })
        """,
        project=project,
        id=event["id"],
        at=event.get("at") or "",
        op=event.get("op") or "",
        detail=event.get("detail") or "",
        spec_id=event.get("spec_id") or "",
        payload=json.dumps(event, ensure_ascii=False),
    )
    spec_id = event.get("spec_id")
    if spec_id:
        tx.run(
            """
            MATCH (s:Spec {project: $project, id: $spec_id})
            MATCH (e:Event {project: $project, id: $id})
            MERGE (s)-[:HAS_EVENT]->(e)
            """,
            project=project,
            spec_id=spec_id,
            id=event["id"],
        )


def _read_events(tx, project: str) -> list[dict]:
    events = []
    for record in tx.run(
        """
        MATCH (e:Event {project: $project})
        RETURN e.payload AS payload
        ORDER BY e.at, e.id
        """,
        project=project,
    ):
        events.append(json.loads(record["payload"]))
    return events


def _delete_project(tx, project: str) -> None:
    tx.run("MATCH (s:Spec {project: $project}) DETACH DELETE s", project=project)
    tx.run("MATCH (e:Event {project: $project}) DETACH DELETE e", project=project)


def _node(props: dict) -> SpecNode:
    return SpecNode(
        id=props.get("id") or "",
        type=props.get("type") or "",
        title=props.get("title") or "",
        body=props.get("body") or "",
        status=props.get("status"),
        valid_from=_as_datetime(props.get("valid_from")),
        valid_to=_as_datetime(props.get("valid_to")),
        belief=props.get("belief") or DEFAULT_BELIEF,
        belief_reason=props.get("belief_reason"),
        belief_at=_as_datetime(props.get("belief_at")),
        perturbed_by=[str(item) for item in props.get("perturbed_by") or []],
        anchors=[str(item) for item in props.get("anchors") or []],
        tags=[str(item) for item in props.get("tags") or []],
    )


def _edge(row: dict) -> SpecEdge:
    return SpecEdge(
        src=row["src"],
        dst=row["dst"],
        kind=_KIND[row["rel"]],
        valid_from=_as_datetime(row.get("valid_from")),
        valid_to=_as_datetime(row.get("valid_to")),
        belief=row.get("belief"),
    )


def _as_datetime(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    native = getattr(value, "to_native", None)
    if native is not None:
        return _as_datetime(native())
    return parse_time(str(value).replace(" ", "T"))


def _run(write: bool, work, *args):
    auth_error, neo4j_error, unavailable = _neo4j_exceptions()
    database = os.environ.get("SPECGRAPH_NEO4J_DATABASE", "neo4j")
    try:
        _prepare(database)
        with _driver.session(database=database) as session:
            if write:
                return session.execute_write(work, *args)
            return session.execute_read(work, *args)
    except SpecGraphError:
        raise
    except (auth_error, unavailable, OSError) as exc:
        raise SpecGraphError(f"Neo4j is not available: {exc}") from exc
    except neo4j_error as exc:
        raise SpecGraphError(str(exc)) from exc


def _prepare(database: str) -> None:
    global _schema_ready
    _connect()
    if _schema_ready:
        return
    statements = (
        """
        CREATE CONSTRAINT spec_project_id IF NOT EXISTS
        FOR (s:Spec) REQUIRE (s.project, s.id) IS UNIQUE
        """,
        """
        CREATE CONSTRAINT event_project_id IF NOT EXISTS
        FOR (e:Event) REQUIRE (e.project, e.id) IS UNIQUE
        """,
    )
    for statement in statements:
        with _driver.session(database=database) as session:
            session.execute_write(lambda tx, query=statement: tx.run(query))
    _schema_ready = True


def _connect() -> None:
    global _driver
    if _driver is not None:
        return
    if "SPECGRAPH_NEO4J_PASSWORD" not in os.environ:
        raise SpecGraphError("SPECGRAPH_NEO4J_PASSWORD is required")
    uri = os.environ.get("SPECGRAPH_NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("SPECGRAPH_NEO4J_USER", "neo4j")
    from neo4j import GraphDatabase

    _driver = GraphDatabase.driver(uri, auth=(user, os.environ["SPECGRAPH_NEO4J_PASSWORD"]))
