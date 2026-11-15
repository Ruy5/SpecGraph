"""Project-level temporal spec graph for coding agents.

A Markdown file is a spec when its frontmatter has ``id`` and ``type``.
``parent``, ``depends-on``, ``references``, and ``implements`` are the graph.
Every node and edge carries a validity interval. Code-structure changes may
set a belief to ``suspicious``; only an agent verification at consumption
time may set it to ``invalidated``.
"""

from specgraph.govern import consume, scan, verify
from specgraph.query import recall
from specgraph.store import load_graph

__all__ = ["consume", "load_graph", "recall", "scan", "verify"]
__version__ = "0.1.0"
