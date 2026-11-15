"""Reviewers that turn a suspicious spec into one of the three verdicts.

``OracleReviewer`` reads the scenario's hand-authored ground truth, so it
measures the mechanism's ceiling. ``DeepSeekReviewer`` asks a model served over
an OpenAI-compatible endpoint, which is the reviewer the reported numbers used.

The HTTP client is built on ``urllib`` so the harness gains no dependency; any
server that speaks ``POST {base_url}/chat/completions`` works (vLLM, Ollama,
LM Studio, a hosted gateway).
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from specgraph.errors import SpecGraphError
VERDICTS = ("held", "invalidated", "unverifiable")

DEFAULT_BASE_URL = "http://localhost:8000/v1"
DEFAULT_MODEL = "deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B"

SYSTEM_PROMPT = (
    "You review a written engineering spec against a code change. "
    "Answer with exactly one JSON object and nothing else: "
    '{"verdict": "held" | "invalidated" | "unverifiable", "reason": "<one sentence>"}. '
    "Use held only when the changed code still satisfies every claim in the spec. "
    "Use invalidated when the code now contradicts the spec. "
    "Use unverifiable when the change does not contain enough evidence."
)

USER_TEMPLATE = """Spec:
id: {id}
type: {type}
title: {title}
statement: {body}
anchors: {anchors}

Code change at step "{step}":
{changes}
"""


class Reviewer(Protocol):
    name: str

    def decide(self, node, step, stale) -> tuple[str, str]:
        """Return ``(verdict, reason)`` for one suspicious spec."""


@dataclass(frozen=True)
class OracleReviewer:
    """Ground-truth reviewer: the ceiling, not a claim about real reviewers."""

    name: str = "oracle"

    def decide(self, node, step, stale) -> tuple[str, str]:
        if node.id in stale:
            return "invalidated", "oracle reviewer: the spec no longer matches the code"
        return "held", "oracle reviewer: the spec still matches the code"


@dataclass(frozen=True)
class DeepSeekReviewer:
    """Ask a model for the verdict; fail loudly when the endpoint is unusable."""

    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    api_key: str | None = None
    timeout: float = 120.0
    max_tokens: int = 400
    name: str = "deepseek"

    @classmethod
    def from_env(cls) -> "DeepSeekReviewer":
        return cls(
            model=os.environ.get("SPECGRAPH_REVIEWER_MODEL", DEFAULT_MODEL),
            base_url=os.environ.get("SPECGRAPH_REVIEWER_BASE_URL", DEFAULT_BASE_URL),
            api_key=os.environ.get("SPECGRAPH_REVIEWER_API_KEY") or None,
        )

    def decide(self, node, step, stale) -> tuple[str, str]:
        prompt = USER_TEMPLATE.format(
            id=node.id,
            type=node.type,
            title=node.title,
            body=_clip(node.body or "(no statement)", 2000),
            anchors=", ".join(node.anchors) or "(none)",
            step=step.label,
            changes=_render_changes(step),
        )
        reply = self._chat(prompt)
        verdict, reason = parse_decision(reply)
        return verdict, _clip(reason, 500)

    def _chat(self, prompt: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "max_tokens": self.max_tokens,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        request = urllib.request.Request(
            self.base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError) as exc:
            raise SpecGraphError(
                "reviewer endpoint %s is not available: %s"
                % (self.base_url, exc)
            ) from exc
        except json.JSONDecodeError as exc:
            raise SpecGraphError("reviewer endpoint returned non-JSON output") from exc
        try:
            message = body["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise SpecGraphError(
                "reviewer endpoint returned an unexpected chat-completions payload"
            ) from exc
        content = message.get("content") if isinstance(message, dict) else None
        if not content and isinstance(message, dict):
            content = message.get("reasoning_content")
        return content or ""


def parse_decision(text: str) -> tuple[str, str]:
    """Read the verdict out of a model reply, tolerating reasoning preambles."""
    for candidate in reversed(list(_json_objects(text or ""))):
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        verdict = str(payload.get("verdict") or "").strip().lower()
        if verdict in VERDICTS:
            reason = str(payload.get("reason") or "").strip()
            return verdict, reason or "reviewer gave no reason"
    match = re.search(r"verdict\s*[:=]\s*[\"']?(held|invalidated|unverifiable)\b",
                      text or "", re.IGNORECASE)
    if match:
        return match.group(1).lower(), "reviewer reply parsed without JSON"
    return "unverifiable", "reviewer reply did not contain a verdict"


def _json_objects(text: str):
    """Yield every balanced ``{...}`` span, innermost calls included."""
    starts = [index for index, char in enumerate(text) if char == "{"]
    for start in starts:
        depth = 0
        for end in range(start, len(text)):
            if text[end] == "{":
                depth += 1
            elif text[end] == "}":
                depth -= 1
                if depth == 0:
                    yield text[start : end + 1]
                    break


def _render_changes(step) -> str:
    blocks = []
    for change in step.changes:
        lines = [change.path]
        if change.before == change.after:
            lines.append("  symbols unchanged")
        else:
            for name in sorted(set(change.before) | set(change.after)):
                before = change.before.get(name)
                after = change.after.get(name)
                if before == after:
                    continue
                lines.append(
                    "  %s: %s -> %s"
                    % (
                        name,
                        _clip(before, 300) if before is not None else "<absent>",
                        _clip(after, 300) if after is not None else "<absent>",
                    )
                )
        if change.body_changed:
            lines.append("  body changed")
        blocks.append("\n".join(lines))
    return "\n".join(blocks) or "(no code change)"


def _clip(text: str, limit: int) -> str:
    text = str(text)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."
