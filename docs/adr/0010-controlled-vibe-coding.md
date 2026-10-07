# ADR-0010: Development method

- **Status:** accepted
- **Date:** 2026-10-07

## Context
One human developer with Claude Pro and Copilot Student must build a large system by late February.

## Decision
Spec-first, test-first, small PRs; Claude Code (local + cloud) and the Copilot coding agent implement; Claude /code-review + Copilot review + human review; auto-merge only for low-risk paths; contracts are human-owned (AGENTS.md).

## Consequences
Capacity risk accepted; burn-down rule in docs/PLAN.md decides what moves after the race.
