"""Tests for the evaluation backends: reviewer transport and Neo4j isolation."""

from __future__ import annotations

import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from experiments.graph_store import Neo4jStore, neo4j_store
from experiments.reviewer import DeepSeekReviewer, OracleReviewer, parse_decision


def make_node(spec_id="auth-boundary"):
    return SimpleNamespace(
        id=spec_id,
        type="module-design",
        title="Auth module boundary",
        body="Session handling stays inside the auth module.",
        anchors=["svc/auth.py#Session"],
    )


def make_step():
    return SimpleNamespace(
        label="session-token",
        changes=[
            SimpleNamespace(
                path="svc/auth.py",
                before={"Session": "(token)"},
                after={"Session": "(token, ttl)"},
                body_changed=False,
                parser="python",
            )
        ],
    )


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class ParseDecisionTests(unittest.TestCase):
    def test_json_after_reasoning(self):
        reply = (
            'Let me think about {"nested": {"depth": 1}} first.\n'
            '{"verdict": "invalidated", "reason": "the session token now expires"}'
        )
        self.assertEqual(
            parse_decision(reply),
            ("invalidated", "the session token now expires"),
        )

    def test_plain_text_fallback(self):
        verdict, reason = parse_decision("Verdict: held. The boundary is unchanged.")
        self.assertEqual(verdict, "held")
        self.assertIn("without JSON", reason)

    def test_unknown_reply_is_unverifiable(self):
        verdict, reason = parse_decision("I cannot answer that.")
        self.assertEqual(verdict, "unverifiable")
        self.assertTrue(reason)


class OracleTests(unittest.TestCase):
    def test_reads_ground_truth(self):
        reviewer = OracleReviewer()
        node = make_node()
        self.assertEqual(reviewer.decide(node, make_step(), {"auth-boundary"})[0],
                         "invalidated")
        self.assertEqual(reviewer.decide(node, make_step(), set())[0], "held")


class DeepSeekTransportTests(unittest.TestCase):
    def test_posts_openai_payload_and_parses_reply(self):
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["url"] = request.full_url
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["auth"] = request.headers.get("Authorization")
            captured["timeout"] = timeout
            return FakeResponse(
                {"choices": [{"message": {"content":
                    '{"verdict": "held", "reason": "the boundary still holds"}'}}]}
            )

        reviewer = DeepSeekReviewer(
            model="deepseek-r1-distill-qwen-1.5b",
            base_url="http://example.test/v1/",
            api_key="secret",
        )
        with patch("experiments.reviewer.urllib.request.urlopen", fake_urlopen):
            verdict, reason = reviewer.decide(make_node(), make_step(), set())

        self.assertEqual((verdict, reason), ("held", "the boundary still holds"))
        self.assertEqual(captured["url"], "http://example.test/v1/chat/completions")
        self.assertEqual(captured["body"]["model"], "deepseek-r1-distill-qwen-1.5b")
        self.assertEqual(captured["body"]["temperature"], 0)
        self.assertEqual(captured["auth"], "Bearer secret")
        prompt = captured["body"]["messages"][1]["content"]
        self.assertIn("auth-boundary", prompt)
        self.assertIn("svc/auth.py", prompt)
        self.assertIn("(token) -> (token, ttl)", prompt)


class Neo4jIsolationTests(unittest.TestCase):
    def test_uses_one_project_key_and_restores_the_environment(self):
        previous = os.environ.pop("SPECGRAPH_PROJECT", None)
        calls = []

        def fake_delete(root):
            calls.append(os.environ.get("SPECGRAPH_PROJECT"))

        try:
            with patch("experiments.graph_store.database.delete_project", fake_delete):
                with neo4j_store("checkout-service", "specgraph") as store:
                    inside = os.environ["SPECGRAPH_PROJECT"]
                    self.assertTrue(inside.startswith(
                        "specgraph-eval:checkout-service:specgraph:"
                    ))
                    self.assertEqual(store.project, inside)
                    self.assertIsInstance(store, Neo4jStore)
                self.assertNotIn("SPECGRAPH_PROJECT", os.environ)
        finally:
            if previous is not None:
                os.environ["SPECGRAPH_PROJECT"] = previous

        self.assertEqual(calls, [inside, inside])

    def test_restores_a_preexisting_project_key(self):
        os.environ["SPECGRAPH_PROJECT"] = "outer"
        try:
            with patch("experiments.graph_store.database.delete_project"):
                with neo4j_store("scenario", "variant"):
                    self.assertNotEqual(os.environ["SPECGRAPH_PROJECT"], "outer")
            self.assertEqual(os.environ["SPECGRAPH_PROJECT"], "outer")
        finally:
            os.environ.pop("SPECGRAPH_PROJECT", None)

    def test_does_not_clear_twice_when_the_first_cleanup_fails(self):
        from specgraph.errors import SpecGraphError

        calls = []

        def failing_delete(root):
            calls.append(root)
            raise SpecGraphError("Neo4j is not available")

        with patch("experiments.graph_store.database.delete_project", failing_delete):
            with self.assertRaises(SpecGraphError):
                with neo4j_store("scenario", "variant"):
                    self.fail("the block must not run without a database")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
