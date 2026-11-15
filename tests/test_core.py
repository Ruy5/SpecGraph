"""Driver-free tests for the temporal, validation, fingerprint and governance logic."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from specgraph import database, govern, store
from specgraph.errors import SpecGraphError
from specgraph.fingerprint import (
    Fingerprint,
    diff_fingerprints,
    fingerprint_source,
    language_of,
)
from specgraph.model import SpecEdge, SpecGraph, SpecNode
from specgraph.query import edge_current, recall, slice_graph
from specgraph.timeutil import dump_time, in_window, parse_time
from specgraph.validate import validate_graph

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 6, 1, tzinfo=timezone.utc)


def make_node(spec_id: str, **overrides) -> SpecNode:
    values = dict(
        id=spec_id,
        type="module-design",
        title=spec_id,
        body="body of " + spec_id,
        status="active",
        valid_from=T0,
        valid_to=None,
        belief="held",
        anchors=["pkg/mod.py#Thing"],
        tags=["alpha"],
    )
    values.update(overrides)
    return SpecNode(**values)


def make_graph(nodes, edges=()) -> SpecGraph:
    return SpecGraph(
        root=Path("."),
        nodes={node.id: node for node in nodes},
        edges=list(edges),
        duplicate_ids={},
    )


class TimeWindowTests(unittest.TestCase):
    def test_parse_and_dump(self):
        self.assertEqual(parse_time("2026-01-01T00:00:00Z"), T0)
        self.assertEqual(parse_time("2026-01-01T00:00:00"), T0)
        self.assertIsNone(parse_time("null"))
        self.assertIsNone(parse_time("  "))
        self.assertEqual(dump_time(T0), "2026-01-01T00:00:00Z")

    def test_half_open_interval(self):
        self.assertTrue(in_window(T0, T0, T1))
        self.assertFalse(in_window(T1, T0, T1), "valid_to is exclusive")
        self.assertFalse(in_window(T0 - timedelta(seconds=1), T0, T1))
        self.assertTrue(in_window(T0, None, None), "missing bounds are open")


class FingerprintTests(unittest.TestCase):
    def test_python_signature_change_is_structural(self):
        old = fingerprint_source("pkg/mod.py", "def thing(a, b=1):\n    return a\n")
        new = fingerprint_source("pkg/mod.py", "def thing(a, b=2):\n    return a\n")
        self.assertEqual(
            [(c.symbol, c.action) for c in diff_fingerprints(old, new)],
            [("thing", "changed")],
        )

    def test_body_edit_is_not_structural(self):
        old = fingerprint_source("pkg/mod.py", "def thing(a):\n    return a\n")
        new = fingerprint_source("pkg/mod.py", "def thing(a):\n    return a + 1\n")
        self.assertEqual(diff_fingerprints(old, new), [])

    def test_added_removed_and_imports(self):
        old = fingerprint_source("pkg/mod.py", "import os\n\ndef thing(a):\n    pass\n")
        new = fingerprint_source("pkg/mod.py", "import sys\n\ndef other(a):\n    pass\n")
        actions = {(c.symbol, c.action) for c in diff_fingerprints(old, new)}
        self.assertIn(("thing", "removed"), actions)
        self.assertIn(("other", "added"), actions)
        self.assertIn(("import:os", "removed"), actions)
        self.assertIn(("import:sys", "added"), actions)

    def test_syntax_error_does_not_crash(self):
        result = fingerprint_source("pkg/mod.py", "def broken(:\n")
        self.assertEqual(result.parser, "error")

    def test_script_declarations(self):
        source = """
        // export interface ignored in comments
        export interface Repo { load(id: string): Spec }
        export class Store { save(x) { return x } }
        type Alias = { id: string }
        function helper(a, b) { return a }
        const arrow = (x) => x
        """
        symbols = fingerprint_source("pkg/mod.ts", source).symbols
        self.assertIn("interface:Repo", symbols)
        self.assertIn("class:Store", symbols)
        self.assertIn("class:Store.save", symbols)
        self.assertIn("type:Alias", symbols)
        self.assertIn("function:helper", symbols)
        self.assertIn("function:arrow", symbols)

    def test_language_detection(self):
        self.assertEqual(language_of("a.py"), "python")
        self.assertEqual(language_of("a.TSX"), "script")
        self.assertIsNone(language_of("a.md"))

    def test_baseline_diff_lists_all_symbols(self):
        changes = diff_fingerprints(None, Fingerprint("python", {"a": "s", "b": "s"}))
        self.assertEqual({c.symbol for c in changes}, {"a", "b"})


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.graph = make_graph(
            [
                make_node("a-held"),
                make_node("b-suspicious", belief="suspicious"),
                make_node("c-unverifiable", belief="unverifiable"),
                make_node("d-invalidated", belief="invalidated"),
                make_node("e-expired", valid_to=T1),
                make_node("f-done", type="task-spec", status="done"),
                make_node("g-future", valid_from=datetime(2027, 1, 1, tzinfo=timezone.utc)),
            ]
        )

    def test_recall_partitions_beliefs(self):
        found = recall(self.graph, as_of=datetime(2026, 9, 1, tzinfo=timezone.utc))
        self.assertEqual([n.id for n in found.trusted], ["a-held"])
        self.assertEqual([n.id for n in found.pending], ["b-suspicious", "c-unverifiable"])

    def test_recall_is_temporal(self):
        past = recall(self.graph, as_of=T0 - timedelta(days=1))
        self.assertEqual(past.trusted, [])
        self.assertEqual(past.pending, [])
        at_start = recall(self.graph, as_of=T0)
        self.assertEqual([n.id for n in at_start.trusted], ["a-held", "e-expired"])

    def test_recall_text_filter(self):
        found = recall(self.graph, as_of=T1 + timedelta(days=1), text="B-SUSPICIOUS")
        self.assertEqual([n.id for n in found.pending], ["b-suspicious"])

    def test_slice_neighbors_and_ancestors(self):
        nodes = [make_node("child"), make_node("parent"), make_node("grand")]
        edges = [
            SpecEdge("child", "parent", "parent"),
            SpecEdge("parent", "grand", "parent"),
            SpecEdge("child", "grand", "references"),
        ]
        graph = make_graph(nodes, edges)
        rows = slice_graph(graph, "child", "ancestors", 2, as_of=T1)
        self.assertEqual([row.id for row in rows], ["child", "parent", "grand"])
        rows = slice_graph(graph, "grand", "subtree", 2, as_of=T1)
        self.assertEqual([row.id for row in rows], ["grand", "parent", "child"])
        rows = slice_graph(graph, "child", "neighbors", 1, as_of=T1)
        self.assertEqual({row.id for row in rows}, {"child", "parent", "grand"})

    def test_invalidated_node_stops_the_walk(self):
        nodes = [make_node("child"), make_node("parent", belief="invalidated")]
        graph = make_graph(nodes, [SpecEdge("child", "parent", "parent")])
        self.assertEqual([row.id for row in slice_graph(graph, "child", "ancestors", 2, as_of=T1)],
                         ["child"])

    def test_edge_current(self):
        nodes = [make_node("a"), make_node("b", belief="invalidated")]
        graph = make_graph(nodes, [SpecEdge("a", "b", "depends-on")])
        self.assertFalse(edge_current(graph, graph.edges[0], T1))


class ValidationTests(unittest.TestCase):
    def test_clean_graph_is_valid(self):
        graph = make_graph([make_node("a"), make_node("b")])
        self.assertTrue(validate_graph(graph).ok)

    def test_dangling_and_cycle_and_interval(self):
        graph = make_graph(
            [
                make_node("a", valid_from=T1, valid_to=T0, belief="bogus"),
                make_node("b", anchors=[]),
            ],
            [
                SpecEdge("a", "missing", "references"),
                SpecEdge("a", "b", "parent"),
                SpecEdge("b", "a", "parent"),
                SpecEdge("a", "b", "depends-on", belief="unknown"),
            ],
        )
        report = validate_graph(graph)
        self.assertFalse(report.ok)
        self.assertTrue(report.dangling)
        self.assertTrue(report.parent_cycles)
        self.assertTrue(report.intervals)
        self.assertEqual(len(report.beliefs), 2)
        self.assertTrue(any("b has no anchors" in item for item in report.warnings))

    def test_warnings_do_not_fail_validation(self):
        graph = make_graph([make_node("a", anchors=[])])
        report = validate_graph(graph)
        self.assertTrue(report.ok)
        self.assertTrue(report.warnings)


class GovernanceTests(unittest.TestCase):
    def test_anchor_matching(self):
        anchors = ["pkg/mod.py#Thing", "pkg/**/*.ts#Repo"]
        self.assertTrue(govern._anchor_hit(anchors, "pkg/mod.py", "Thing"))
        self.assertFalse(govern._anchor_hit(anchors, "pkg/mod.py", "Other"))
        self.assertTrue(govern._anchor_hit(anchors, "pkg/deep/x.ts", "Repo"))
        self.assertTrue(govern._anchor_hit(["pkg"], "pkg/mod.py", "anything"))

    def test_propagation_is_one_hop_and_kind_limited(self):
        ids = ["a", "impl-target", "implementer", "dependent", "parent-node", "ref-node"]
        graph = make_graph(
            [make_node(name, anchors=[]) for name in ids],
            [
                SpecEdge("a", "impl-target", "implements"),
                SpecEdge("implementer", "a", "implements"),
                SpecEdge("dependent", "a", "depends-on"),
                SpecEdge("a", "parent-node", "parent"),
                SpecEdge("a", "ref-node", "references"),
            ],
        )
        self.assertEqual(
            govern._propagate(graph, {"a"}),
            {"a", "impl-target", "implementer", "dependent"},
        )

    def test_verify_requires_a_valid_verdict_and_reason(self):
        with self.assertRaises(SpecGraphError):
            govern.verify(".", "a", "maybe", "reason")
        with self.assertRaises(SpecGraphError):
            govern.verify(".", "a", "held", "   ")

    def test_verify_held_clears_perturbations(self):
        node = make_node("a", belief="suspicious", perturbed_by=["old"])
        with patch("specgraph.govern.load_graph", return_value=make_graph([node])), \
             patch("specgraph.govern.update_belief") as update, \
             patch("specgraph.govern.append_event") as append:
            event = govern.verify(".", "a", "held", "still matches", now=T1)
        self.assertEqual(update.call_args.args[2:], ("held", "still matches", T1, [], None))
        self.assertEqual(event["op"], "verify")
        self.assertEqual(event["verdict"], "held")
        self.assertEqual(append.call_args.args[1]["op"], "verify")

    def test_verify_invalidated_closes_interval(self):
        node = make_node("a", belief="suspicious")
        with patch("specgraph.govern.load_graph", return_value=make_graph([node])), \
             patch("specgraph.govern.update_belief") as update, \
             patch("specgraph.govern.append_event"):
            govern.verify(".", "a", "invalidated", "no longer true", now=T1)
        self.assertEqual(update.call_args.args[2], "invalidated")
        self.assertEqual(update.call_args.args[6], T1)

    def test_consume_three_states(self):
        nodes = [
            make_node("trusted"),
            make_node("pending", belief="suspicious"),
            make_node("absent", belief="invalidated"),
        ]
        with patch("specgraph.govern.load_graph", return_value=make_graph(nodes)):
            self.assertEqual(govern.consume(".", "trusted", as_of=T1).status, "trusted")
            self.assertEqual(govern.consume(".", "pending", as_of=T1).status, "pending")
            self.assertEqual(govern.consume(".", "absent", as_of=T1).status, "absent")
            self.assertEqual(govern.consume(".", "unknown", as_of=T1).status, "absent")

    def test_create_spec_rejects_unknown_type(self):
        with self.assertRaises(SpecGraphError):
            govern.create_spec(".", "a", "not-a-type", "title")

    def test_create_spec_forwards_new_fields(self):
        with patch("specgraph.govern.create_spec_node") as create, \
             patch("specgraph.govern.append_event"):
            govern.create_spec(
                ".", "a", "module-design", "Title",
                statement="statement", references=["b"], tags=["t"], status="draft", now=T1,
            )
        self.assertEqual(create.call_args.kwargs["references"], ["b"])
        self.assertEqual(create.call_args.kwargs["tags"], ["t"])
        self.assertEqual(create.call_args.kwargs["status"], "draft")
        self.assertEqual(create.call_args.args[4], "statement")

    def test_status_and_link_write_events(self):
        with patch("specgraph.govern.set_status_node") as set_node, \
             patch("specgraph.govern.append_event") as append:
            event = govern.set_status(".", "a", "done", now=T1)
        self.assertEqual(set_node.call_args.args[2], "done")
        self.assertEqual(event["op"], "status")
        with patch("specgraph.govern.link_specs_node") as link_node, \
             patch("specgraph.govern.append_event"):
            event = govern.link(".", "a", "b", "references", now=T1)
        self.assertEqual(link_node.call_args.args[3], "references")
        self.assertEqual(event["kind"], "references")
        with self.assertRaises(SpecGraphError):
            govern.link(".", "a", "b", "unknown")

    def test_scan_baseline_only_writes_a_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch("specgraph.govern.code_fingerprints", return_value={}), \
             patch("specgraph.govern.load_snapshot", return_value=None), \
             patch("specgraph.govern.save_snapshot") as save, \
             patch("specgraph.govern.append_event") as append, \
             patch("specgraph.govern.load_graph") as load:
            report = govern.scan(tmp, now=T1)
        self.assertTrue(report.baseline)
        self.assertEqual(report.marked, [])
        self.assertEqual(append.call_args.args[1]["op"], "baseline")
        save.assert_called_once()
        load.assert_not_called()

    def test_scan_marks_anchor_and_dependent(self):
        previous = {"pkg/mod.py": {"text": "old", "parser": "python", "symbols": {"Thing": "sig-old"}}}
        current = {"pkg/mod.py": {"text": "new", "parser": "python", "symbols": {"Thing": "sig-new"}}}
        nodes = [make_node("a"), make_node("b", anchors=[])]
        graph = make_graph(nodes, [SpecEdge("b", "a", "depends-on")])
        with tempfile.TemporaryDirectory() as tmp, \
             patch("specgraph.govern.code_fingerprints", return_value=current), \
             patch("specgraph.govern.load_snapshot", return_value=previous), \
             patch("specgraph.govern.load_graph", return_value=graph), \
             patch("specgraph.govern.update_belief") as update, \
             patch("specgraph.govern.append_event") as append, \
             patch("specgraph.govern.save_snapshot"):
            report = govern.scan(tmp, now=T1)
        self.assertFalse(report.baseline)
        roles = {item["id"]: item["role"] for item in report.marked}
        self.assertEqual(roles, {"a": "anchor", "b": "depends-on"})
        self.assertEqual(update.call_count, 2)
        self.assertTrue(all(call.args[2] == "suspicious" for call in update.call_args_list))
        self.assertEqual({call.args[1]["op"] for call in append.call_args_list}, {"perturb"})

    def test_nonstructural_edit_marks_nothing(self):
        previous = {"pkg/mod.py": {"text": "old", "parser": "python", "symbols": {"Thing": "sig"}}}
        current = {"pkg/mod.py": {"text": "new", "parser": "python", "symbols": {"Thing": "sig"}}}
        graph = make_graph([make_node("a")])
        with tempfile.TemporaryDirectory() as tmp, \
             patch("specgraph.govern.code_fingerprints", return_value=current), \
             patch("specgraph.govern.load_snapshot", return_value=previous), \
             patch("specgraph.govern.load_graph", return_value=graph), \
             patch("specgraph.govern.update_belief") as update, \
             patch("specgraph.govern.append_event"), \
             patch("specgraph.govern.save_snapshot"):
            report = govern.scan(tmp, now=T1)
        self.assertEqual(report.nonstructural, ["pkg/mod.py"])
        self.assertEqual(report.marked, [])
        update.assert_not_called()


class StoreTests(unittest.TestCase):
    def test_snapshot_round_trip(self):
        files = {"pkg/mod.py": {"text": "abc", "parser": "python", "symbols": {"Thing": "sig"}}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store.save_snapshot(root, files)
            self.assertEqual(store.load_snapshot(root), files)
            raw = store.snapshot_path(root).read_bytes()
            self.assertTrue(raw.endswith(b"\n"))
            self.assertNotIn(b"\r\n", raw)

    def test_missing_or_broken_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIsNone(store.load_snapshot(root))
            path = store.snapshot_path(root)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("not json", encoding="utf-8")
            self.assertIsNone(store.load_snapshot(root))

    def test_ignored_directories_and_unsupported_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pkg").mkdir()
            (root / "pkg" / "mod.py").write_text("def thing(a):\n    return a\n", encoding="utf-8")
            (root / ".git").mkdir()
            (root / ".git" / "hidden.py").write_text("def hidden(): pass\n", encoding="utf-8")
            (root / "notes.md").write_text("ignored", encoding="utf-8")
            found = store.code_fingerprints(root)
            self.assertEqual(set(found), {"pkg/mod.py"})
            self.assertIn("thing", found["pkg/mod.py"]["symbols"])


class FakeResult:
    def __init__(self, row=None):
        self.row = row

    def single(self):
        return self.row


class FakeTx:
    def __init__(self, rows=None):
        self.rows = rows if rows is not None else {}
        self.calls = []

    def run(self, query, **params):
        self.calls.append((query, params))
        spec_id = params.get("id")
        return FakeResult(self.rows.get(spec_id, {"id": spec_id}))


class DatabaseTests(unittest.TestCase):
    def test_driver_is_loaded_lazily(self):
        with patch.dict(sys.modules, {"neo4j": None, "neo4j.exceptions": None}):
            with self.assertRaises(SpecGraphError) as caught:
                database._neo4j_exceptions()
        self.assertIn("neo4j driver is required", str(caught.exception))

    def test_create_spec_passes_status_tags_and_references(self):
        with patch("specgraph.database._run") as run:
            database.create_spec(
                Path("."), "a", "module-design", "Title", "body", T1,
                references=["b"], tags=["t"], status="draft",
            )
        args = run.call_args.args
        props = args[3]
        links = args[4]
        self.assertEqual(props["status"], "draft")
        self.assertEqual(props["tags"], ["t"])
        self.assertIn(("references", "b"), links)

    def test_invalid_status_or_link_kind(self):
        with self.assertRaises(SpecGraphError):
            database.create_spec(Path("."), "a", "module-design", "T", "b", T1, status="bogus")
        with self.assertRaises(SpecGraphError):
            database.link_specs(Path("."), "a", "b", "bogus")
        with self.assertRaises(SpecGraphError):
            database.set_status(Path("."), "a", "bogus", T1)

    def test_create_link_uses_typed_relationship(self):
        tx = FakeTx()
        database._create_link(tx, "project", "a", "b", "references")
        query, params = tx.calls[-1]
        self.assertIn("REFERENCES", query)
        self.assertIn("MERGE", query)
        self.assertEqual((params["src"], params["dst"]), ("a", "b"))

    def test_create_link_rejects_unknown_target(self):
        class MissingTx(FakeTx):
            def run(self, query, **params):
                return FakeResult(None)

        with self.assertRaises(SpecGraphError):
            database._create_link(MissingTx(), "project", "a", "missing", "references")

    def test_project_key_override(self):
        with patch.dict("os.environ", {"SPECGRAPH_PROJECT": "demo"}):
            self.assertEqual(database.project_key(Path(".")), "demo")


if __name__ == "__main__":
    unittest.main()
