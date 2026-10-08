# ADR-0023: WebSocket client for the car live link (websockets)

- **Status:** accepted
- **Date:** 2026-10-08

## Context
Spec 0010 (teleop) has the engine relay the car runtime's telemetry/teleop WebSocket (spec 0005) to the UI,
because front-ends only talk to `raceforge.api`. The engine needs an asyncio WebSocket **client** with ping/pong
(round-trip time for the 100 ms latency warning). httpx has no WebSocket support.

## Decision
Use **websockets** (BSD-3-Clause, pure Python, asyncio) as a direct dependency. It is already installed as part of
`uvicorn[standard]`, so the environment does not change; declaring it pins our direct use. Alternatives:
`wsproto` (lower level, we would write the client loop), `aiohttp` (much larger).

## Consequences
One more declared dependency (no new package in the lock file). Tests use a websockets server as a fake car.
