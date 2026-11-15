"""Project-level temporal spec graph for coding agents.

Specs, validity intervals, beliefs, and governance events are stored in Neo4j.
``parent``, ``depends-on``, ``references``, and ``implements`` are relationships.
Code-structure changes may set a belief to ``suspicious``; only an agent
verification at consumption time may set it to ``invalidated``.
"""

from specgraph.govern import consume, scan, verify
from specgraph.query import recall
from specgraph.store import load_graph

__all__ = ["consume", "load_graph", "recall", "scan", "verify"]
__version__ = "0.1.0"
