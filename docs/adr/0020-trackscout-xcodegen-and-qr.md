# ADR-0020: TrackScout project generation (XcodeGen) and pairing QR codes (segno)

- **Status:** accepted
- **Date:** 2026-10-08

## Context
Spec 0007 adds the TrackScout iOS app and a "Pair TrackScout" QR code in the desktop engine. A hand-edited
`.xcodeproj` is unreadable in reviews and easy for AI agents to corrupt. The engine needs to render a QR code
as SVG without a browser. All dependencies must be GPL-3.0-compatible (ADR-0009), and the app must stay free
(only Apple frameworks, ADR-0008).

## Decision
- **XcodeGen** (MIT) generates `trackscout_ios/TrackScout.xcodeproj` from the reviewed `project.yml`. It is a
  build tool only, nothing of it ships in the app; the generated project is git-ignored. `TrackScoutKit` is a
  local Swift package (no third-party packages) so its logic is tested with `swift test` on macOS CI.
- **segno** (BSD-3-Clause, pure Python, no dependencies) renders the pairing QR code as SVG in the engine.
  Alternatives: `qrcode` (BSD, needs Pillow for most outputs), rendering in the frontend with a JS library
  (would put the token through one more layer).
- CI checks that `project.yml` and `Package.swift` declare no third-party packages and no paid capabilities.

## Consequences
Developers building TrackScout run `brew install xcodegen && xcodegen` once (README). segno is a small runtime
dependency of the engine.
