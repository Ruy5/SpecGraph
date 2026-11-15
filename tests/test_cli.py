"""Tests for the command-line entry point, including the driver-free path."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from specgraph.cli import main
from specgraph.errors import SpecGraphError
from specgraph.govern import Consumption
from specgraph.model import SpecGraph, SpecNode


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "pkg").mkdir()
        (self.root / "pkg" / "mod.py").write_text(
            "def thing(a):\n    return a\n", encoding="utf-8"
        )

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = main(argv)
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_help_lists_all_commands(self):
        code, out, _ = self.run_cli(["--help"])
        self.assertEqual(code, 0)
        for command in ("init", "create", "scan", "recall", "consume", "verify",
                        "slice", "events", "validate", "fingerprint", "link", "status"):
            self.assertIn(command, out)

    def test_fingerprint_needs_no_neo4j_driver(self):
        code, out, _ = self.run_cli(["fingerprint", "--root", str(self.root)])
        self.assertEqual(code, 0)
        self.assertIn("pkg/mod.py", out)
        self.assertIn("1 code file", out)

    def test_fingerprint_symbols_flag(self):
        code, out, _ = self.run_cli(["fingerprint", "--root", str(self.root), "--symbols"])
        self.assertEqual(code, 0)
        self.assertIn("thing:", out)

    def test_json_flag_works_before_and_after_the_command(self):
        for argv in (
            ["--json", "fingerprint", "--root", str(self.root)],
            ["fingerprint", "--root", str(self.root), "--json"],
        ):
            with self.subTest(argv=argv):
                code, out, _ = self.run_cli(argv)
                self.assertEqual(code, 0)
                payload = json.loads(out)
                self.assertIn("pkg/mod.py", payload)
                self.assertIn("thing", payload["pkg/mod.py"]["symbols"])

    def test_invalid_as_of_is_rejected_before_connecting(self):
        code, _, err = self.run_cli(["recall", "--root", str(self.root), "--as-of", "not-a-time"])
        self.assertEqual(code, 2)
        self.assertIn("invalid --as-of", err)

    def test_graph_error_is_reported_without_traceback(self):
        with patch("specgraph.database._run", side_effect=SpecGraphError("Neo4j is not available")):
            code, _, err = self.run_cli(["recall", "--root", str(self.root)])
        self.assertEqual(code, 2)
        self.assertIn("Neo4j is not available", err)
        self.assertNotIn("Traceback", err)

    def test_validate_reports_a_clean_graph(self):
        graph = SpecGraph(
            root=self.root,
            nodes={"a": SpecNode(id="a", type="module-design", title="A", body="",
                                 anchors=["pkg/mod.py#thing"])},
            edges=[],
            duplicate_ids={},
        )
        with patch("specgraph.cli.load_graph", return_value=graph):
            code, out, _ = self.run_cli(["validate", "--root", str(self.root)])
        self.assertEqual(code, 0)
        self.assertIn("graph is valid", out)

    def test_validate_exit_code_on_broken_graph(self):
        graph = SpecGraph(root=self.root, nodes={}, edges=[], duplicate_ids={})
        from specgraph.model import SpecEdge

        graph.edges.append(SpecEdge("a", "missing", "references"))
        with patch("specgraph.cli.load_graph", return_value=graph):
            code, out, _ = self.run_cli(["validate", "--root", str(self.root)])
        self.assertEqual(code, 1)
        self.assertIn("dangling", out)

    def test_consume_json_payload(self):
        decision = Consumption("pending", "a", "do not use until verify")
        with patch("specgraph.cli.consume", return_value=decision):
            code, out, _ = self.run_cli(["consume", "a", "--root", str(self.root), "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["status"], "pending")

    def test_init_reports_counts(self):
        graph = SpecGraph(root=self.root, nodes={}, edges=[], duplicate_ids={})
        with patch("specgraph.cli.load_graph", return_value=graph):
            code, out, _ = self.run_cli(["init", "--root", str(self.root)])
        self.assertEqual(code, 0)
        self.assertIn("0 specs, 0 edges", out)

    def test_missing_required_argument_exits_two(self):
        code, _, err = self.run_cli(["verify", "a", "--verdict", "held"])
        self.assertEqual(code, 2)
        self.assertIn("--reason", err)

    def test_unknown_subcommand_exits_two(self):
        code, _, _ = self.run_cli(["does-not-exist"])
        self.assertEqual(code, 2)

    def test_create_and_verify_command_wiring(self):
        with patch("specgraph.cli.create_spec") as create, \
             patch("specgraph.cli.verify", return_value={"id": "ev1", "op": "verify"}):
            code, out, _ = self.run_cli([
                "create", "a", "--type", "module-design", "--title", "Title",
                "--root", str(self.root), "--anchor", "pkg/mod.py#thing",
                "--references", "b", "--status", "draft",
            ])
            self.assertEqual(code, 0)
            self.assertIn("created a", out)
            self.assertEqual(create.call_args.kwargs["references"], ["b"])
            self.assertEqual(create.call_args.kwargs["status"], "draft")
            code, out, _ = self.run_cli([
                "verify", "a", "--verdict", "held", "--reason", "still true",
                "--root", str(self.root),
            ])
        self.assertEqual(code, 0)
        self.assertIn("verified a", out)

    def test_recall_renders_trusted_and_pending(self):
        graph = SpecGraph(
            root=self.root,
            nodes={
                "a": SpecNode(id="a", type="module-design", title="A", body="", belief="held"),
                "b": SpecNode(id="b", type="module-design", title="B", body="",
                              belief="suspicious", belief_reason="signature changed"),
            },
            edges=[],
            duplicate_ids={},
        )
        with patch("specgraph.cli.load_graph", return_value=graph):
            code, out, _ = self.run_cli([
                "recall", "--root", str(self.root), "--as-of", "2026-06-01T00:00:00Z"
            ])
        self.assertEqual(code, 0)
        self.assertIn("trusted (1)", out)
        self.assertIn("pending (1)", out)
        self.assertIn("signature changed", out)


if __name__ == "__main__":
    unittest.main()
