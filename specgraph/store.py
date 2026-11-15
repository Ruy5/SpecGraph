from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from specgraph.database import append_event, load_graph, read_events
from specgraph.fingerprint import Fingerprint, fingerprint_source, language_of

IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".specgraph",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    ".claude",
    ".superpowers",
    ".gsd",
    ".mockups",
}

SNAPSHOT_VERSION = 1


def code_fingerprints(root: Path) -> dict[str, dict]:
    root = Path(root)
    found: dict[str, dict] = {}
    for path in _code_files(root):
        source = _read_code(path)
        rel = path.relative_to(root).as_posix()
        if source is None:
            fingerprint = Fingerprint("unsupported")
            digest = ""
        else:
            fingerprint = fingerprint_source(rel, source)
            digest = hashlib.sha256(source.encode("utf-8", errors="replace")).hexdigest()
        found[rel] = {"text": digest, **fingerprint.to_json()}
    return found


def snapshot_path(root: Path) -> Path:
    return Path(root) / ".specgraph" / "structure.json"


def load_snapshot(root: Path) -> dict[str, dict] | None:
    path = snapshot_path(root)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("version") != SNAPSHOT_VERSION or not isinstance(payload.get("files"), dict):
        return None
    return payload["files"]


def save_snapshot(root: Path, files: dict[str, dict]) -> None:
    path = snapshot_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps({"version": SNAPSHOT_VERSION, "files": files}, indent=2, sort_keys=True)
    path.write_text(text + "\n", encoding="utf-8", newline="\n")


def _code_files(root: Path) -> list[Path]:
    found = []
    for path in _walk(root):
        if language_of(path.name):
            found.append(path)
    return found


def _walk(root: Path) -> list[Path]:
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in IGNORED_DIRS and not name.endswith(".egg-info"))
        for name in sorted(filenames):
            found.append(Path(dirpath) / name)
    return found


def _read_code(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data or len(data) > 1_000_000:
        return None
    return data.decode("utf-8", errors="replace")
