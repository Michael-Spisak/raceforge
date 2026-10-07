# ADR-0004: Front-end and desktop shell

- **Status:** accepted
- **Date:** 2026-10-07

## Context
A full 3D LEGO brick editor needs custom 3D interaction (snapping, gizmos, picking) and consistent WebGL on Windows, macOS and Linux; the same UI must also run in a browser.

## Decision
**TypeScript + React + three.js (React Three Fiber)**; desktop shell **Electron** with the Python engine as a sidecar installed by uv on first start; the same front-end is served by the backend for browsers.

## Consequences
Larger installer (~150 MB) but identical rendering everywhere.
