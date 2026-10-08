# ADR-0021: Phone → laptop transfer (pymobiledevice3, bleak, GATT protocol)

- **Status:** accepted
- **Date:** 2026-10-08

## Context
Spec 0007 lets TrackScout send a pass to a paired laptop by cable or Bluetooth when the backend connection is
slow; the laptop relays it later. The desktop engine must reach the app's files over USB on macOS, Windows and
Linux without Finder/iTunes, and talk Bluetooth LE on all three. Dependencies must be GPL-3.0-compatible.

## Decision
- **Cable: pymobiledevice3** (GPL-3.0, pure Python, ≥ 11 with an async API) reads/writes TrackScout's
  Documents through Apple's house-arrest/AFC service (the app sets `UIFileSharingEnabled`). Its ~100
  transitive packages were checked: MIT/BSD/Apache/PSF/LGPL/GPL only (2026-10-08).
- **Bluetooth: bleak** (MIT) as GATT central. bleak has no L2CAP channel API, so v1 uses GATT only: one write
  and one notify characteristic, messages split into MTU-sized fragments (protocol "rftx1" in spec 0007).
  At ~0.1–0.2 MB/s Bluetooth is offered only for small passes; L2CAP can be added later if needed.
- Both live in the optional extra **`transfer`** (`uv sync --extra transfer`), imported lazily: the core
  engine, CI and the backend image stay small; without the extra the Team tab reports what is missing.
- Security: only a laptop that knows the pairing key from the QR code gets passes — HMAC challenge/response on
  Bluetooth, HMAC tags in `outbox.json` on cable; every file is checked with SHA-256.

## Consequences
The desktop installer (later spec) must include the `transfer` extra. Device paths are covered by the device
checklist (`trackscout_ios/CHECKLIST.md`, part B); CI tests the protocol with an in-memory link and a fake
Documents folder.
