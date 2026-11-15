"""Project-level temporal spec graph for coding agents.

The on-disk model follows the spec-graph shape used by ThinkRail: a Markdown
file is a spec when its frontmatter has ``id`` and ``type``, and ``parent`` /
``depends-on`` / ``references`` / ``implements`` are the graph. This package
adds what that model does not govern: a validity interval on every node and
edge, and a belief that code-structure changes may set to ``suspicious`` but
may not set to ``invalidated``. Invalidation happens only when an agent
verifies the node at consumption time.
"""

from specgraph.govern import consume, scan, verify
from specgraph.query import recall
from specgraph.store import load_graph

__all__ = ["consume", "load_graph", "recall", "scan", "verify"]
__version__ = "0.1.0"
