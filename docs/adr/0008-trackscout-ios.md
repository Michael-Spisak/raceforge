# ADR-0008: Scanning app

- **Status:** accepted
- **Date:** 2026-10-07

## Context
Best track data needs LiDAR depth + poses + ARKit mesh classification + RoomPlan in one pass, multi-pass relocalisation and AR coverage guidance; the app must be completely free.

## Decision
Native **Swift/SwiftUI** app **TrackScout** (ARKit, RoomPlan, MultipeerConnectivity), LiDAR devices only, installed by each owner from Xcode with a free Apple ID (no paid program, no paid SDKs).

## Consequences
7-day re-signing; no TestFlight. Free third-party scan formats remain importable.
