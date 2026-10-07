# ADR-0002: Core language

- **Status:** accepted
- **Date:** 2026-10-07

## Context
The car team knows Java/C#/JS; the tool owner knows Python and TypeScript. Robotics/ML ecosystem (MuJoCo, PyTorch, SB3, Open3D) is Python-first, and the same controller code must run in sim and on the car.

## Decision
Python ≥ 3.12 for core, sim, training, backend, workers and controllers, managed with **uv**. Controllers are written in Python by the car team with an SDK designed for Java/C# developers.

## Consequences
One language end-to-end on the Python side. Team needs a Python primer (docs).
