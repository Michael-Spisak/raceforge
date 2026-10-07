# ADR-0003: Physics engine

- **Status:** accepted
- **Date:** 2026-10-07

## Context
Need a fast, cross-platform 3D physics engine with rangefinder/IMU/camera sensors, usable for RL, and generatable from the assembly.

## Decision
**MuJoCo** (CPU, vectorised envs) with MJCF generated from the assembly; **MuJoCo Warp/MJX** as an optional GPU tier; MuJoCo WebAssembly for interactive browser sims.

## Consequences
Ackermann steering is approximated with joint couplings; LEGO play/backlash/friction are calibratable parameters.
