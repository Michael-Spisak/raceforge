# ADR-0031: MCP Python SDK for the MCP server (spec 0028)

- **Status:** accepted
- **Date:** 2026-10-09

## Context
docs/PLAN.md §8 and ADR-0001 plan an MCP server so AI assistants (Claude Desktop/Code, Cursor, Perplexity) can
operate RaceForge. Implementing the protocol (JSON-RPC, capability negotiation, stdio and streamable HTTP
transports, protocol revisions) by hand would be a large, fragile surface.

## Decision
Use the official **MCP Python SDK** (`mcp` ≥ 2.3, < 3; MIT) — its `MCPServer` (formerly FastMCP) registers tools
from typed Python functions and serves stdio and streamable HTTP. It is a normal runtime dependency because the
`raceforge mcp` command ships with every install.

New transitive dependencies (all GPL-3.0-compatible): `mcp-types` (MIT), `pyjwt` (MIT), `jsonschema` (MIT),
`sse-starlette` (BSD-3), `python-multipart` (Apache-2.0), `opentelemetry-api` (Apache-2.0), `httpx2` (BSD-3),
`pywin32` on Windows (PSF). Starlette, uvicorn, pydantic and anyio were already present.

## Consequences
- The SDK's major version 2 renamed FastMCP to `MCPServer`; pinning `< 3` keeps us on this API until a deliberate
  upgrade.
- MCP code lives in `raceforge.mcp` and, like the other front-ends, only calls `raceforge.api` (import-linter).
