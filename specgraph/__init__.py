"""Project-level temporal spec graph for coding agents.

Specs, validity intervals, beliefs, and governance events are stored in Neo4j.
``parent``, ``depends-on``, ``references``, and ``implements`` are relationships.
Code-structure changes may set a belief to ``suspicious``; only an agent
verification at consumption time may set it to ``invalidated``.
"""

from specgraph.database import load_graph
from specgraph.govern import consume, create_spec, events, link, scan, set_status, verify
from specgraph.query import recall, slice_graph
from specgraph.validate import validate_graph

__all__ = [
    "consume",
    "create_spec",
    "events",
    "link",
    "load_graph",
    "recall",
    "scan",
    "set_status",
    "slice_graph",
    "validate_graph",
    "verify",
]
__version__ = "0.1.0"
