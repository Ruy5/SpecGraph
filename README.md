# SpecGraph

Project-level temporal spec graph for coding agents. Specs, relationships, validity intervals, beliefs, and governance events are stored in Neo4j.

Set a connection before calling the library:

- `SPECGRAPH_NEO4J_URI` (default `bolt://localhost:7687`)
- `SPECGRAPH_NEO4J_USER` (default `neo4j`)
- `SPECGRAPH_NEO4J_PASSWORD` (required)
- `SPECGRAPH_NEO4J_DATABASE` (default `neo4j`)
- `SPECGRAPH_PROJECT` (optional project key; the repository path is used when this is unset)

`.specgraph/structure.json` keeps the last code-structure fingerprint so a scan can see what changed. That file is a diff baseline, not the spec memory.
