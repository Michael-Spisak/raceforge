# Spec 0007: TrackScout v1 — LiDAR recording, passes, `.tscan`, upload, desktop importer

- **Status:** approved (2026-10-08) with the owner's upload routing (scope 8–9)
- **Owner:** @Michael-Spisak
- **Plan section:** docs/PLAN.md §2a (capture), §2a2 (multiple scans), §2f (TrackScout), §9 (raw capture storage)
- **Related ADRs:** ADR-0008 (TrackScout: Swift/SwiftUI, free Apple ID), ADR-0009 (public repo, no scans in git),
  ADR-0019 (backend); new ADR-0020 (iOS project generation with XcodeGen), ADR-0021 (phone ↔ laptop
  transfer: USB via pymobiledevice3, Bluetooth LE via bleak)
- **Depends on:** Spec 0006 (backend: API tokens, chunked uploads, objects/versions)

## Purpose
Record the school corridor with a LiDAR iPhone/iPad in **one pass and one format**: depth + confidence, RGB, camera
poses, Apple's classified scene mesh and RoomPlan walls/doors. Passes of one track share a coordinate frame,
recording can be paused, and recordings go to the team backend so the desktop tool (and later the scan pipeline)
can use them. The app stays **completely free**: only Apple frameworks, no accounts besides our own backend,
no paid SDKs, no ads, installable with a free Apple ID.

## Scope
- In scope (v1, plan weeks 2–5):
  1. **App shell** (`trackscout_ios/`): SwiftUI, iOS 18+, iPhone layout (iPad uses it scaled), DE + EN,
     LiDAR devices only (other devices: "LiDAR required" screen, no recording).
  2. **Projects and passes:** a project = one track. A pass has a type (`walkthrough`, `high`, `low`, `detail`,
     `gap_fill`), conditions (lights on/off, doors open/closed, free text), device and time.
  3. **Recording** (ARKit world tracking + scene reconstruction with classification + `sceneDepth`):
     - RGB video (HEVC), depth (float16) + confidence (uint8) per frame, camera pose + intrinsics + exposure per
       frame, gravity and IMU (CoreMotion), the ARKit mesh with per-face classification at the end of each segment;
     - quality presets **Maximum** (4K RGB if supported, depth every frame), **High** (1080p, depth every frame),
       **Economy** (1080p at 15 fps, depth every 2nd frame); free storage and remaining minutes are shown;
     - warnings: moving too fast, low light, tracking limited/lost, too far from surfaces (> 4 m median depth).
  4. **Pause / Continue / Discard:** tracking keeps running while paused; Continue starts a new segment of the same
     pass; "discard last N seconds" (5/10/30 s) marks that range as discarded (the importer skips it).
  5. **Shared coordinate frame:** the first pass saves an `ARWorldMap`; later passes start from the project's map,
     show the relocalisation status and record whether and when they were aligned. (Marker-based chaining for very
     long corridors and live coverage come with TrackScout v2.)
  6. **RoomPlan pass** (optional per project): parametric walls, doors, windows and objects (`CapturedRoom` JSON +
     USDZ), recorded in the same world frame.
  7. **`.tscan` v1 export** (format below), share sheet (AirDrop / Files / USB) always available.
  8. **Upload routing (owner decision 2026-10-08):** a finished pass goes **directly to the team backend** by default.
     - Pairing: scan a QR code shown in the desktop app (Team tab → "Pair TrackScout"). It creates an API token
       (client `trackscout`, scopes `read` + `edit`) and also carries the laptop's pairing key for direct transfer.
     - Resumable chunked upload with the spec 0006 protocol in a background `URLSession`; Wi-Fi automatic,
       **cellular only after asking**.
     - The phone measures the backend throughput while uploading (and with a short probe before it). If the
       estimated time is above a threshold (default: > 10 min or < 1 MB/s; adjustable in settings), it pauses and
       offers: **keep uploading to the backend** (with ETA), **send to the laptop by cable**, or **send to the laptop
       by Bluetooth** (with ETA; recommended only for small passes because Bluetooth LE manages ~0.1–0.2 MB/s).
     - Parts that already reached the backend are not lost: if the user later switches back to the backend, the
       upload resumes.
     - The uploaded file becomes a version of a `capture` object (fileset) in the chosen workspace.
  9. **Phone → laptop transfer and relay (desktop engine):**
     - **Cable (USB):** the engine detects a connected iPhone/iPad (macOS, Windows with Apple's device driver, Linux
       with usbmuxd) and copies finished passes out of TrackScout's shared documents folder; the phone shows the
       progress through a small status file. Works without Finder or iTunes.
     - **Bluetooth LE:** TrackScout acts as a peripheral; the paired laptop connects and pulls the pass in checksummed
       chunks that resume after a disconnect (L2CAP channel where the laptop supports it, GATT otherwise).
     - The laptop stores the pass in its local workspace (blob cache of spec 0006) and then decides like the phone:
       it measures its own connection to the backend; if it is fast enough, it relays the upload automatically
       (resumable); if it is slow or offline, the pass stays local, marked "waiting for upload", and the Team tab
       offers **upload now**, **upload when the connection is faster** (background sync re-checks) or **keep only on
       this laptop**.
     - Only a laptop paired with this phone (key from the QR code) is accepted, for cable and Bluetooth alike.
  10. **On-phone review:** list of passes with duration, size, segment count, alignment state, and a 3D preview of the
     pass mesh (SceneKit) before upload; delete a pass.
  11. **Desktop importer** (`raceforge.capture.tscan`): open and validate a `.tscan` (checksums, schema), iterate
     frames (pose, intrinsics, depth, confidence; discarded ranges skipped), read mesh + classification and RoomPlan
     data, convert ARKit's Y-up frame to RaceForge's Z-up SI frame; CLI `raceforge capture info|import <file>`;
     the Team tab lists capture objects (download on demand via the blob cache of spec 0006).
- Out of scope (later specs): live coverage overlay and AR scan missions, AprilTag/ArUco markers and segment
  chaining, on-phone annotation (start/finish), collaborative multi-device scanning (own spec, next after this one),
  follow-cam mode, fusion/segmentation/registration on the desktop (scan pipeline spec), decoding RGB video in
  Python (perception spec), import of third-party app formats.

## `.tscan` v1 format (contract — human-owned)
A ZIP file (stored, not compressed; media are already compressed) per **pass**:
```text
manifest.json        schema "tscan" v1: app/device/iOS versions, project id + name, pass id/type/conditions,
                     quality preset, created_at, coordinate frame "arkit" (Y-up, right-handed, metres),
                     world-map id + alignment {aligned, at_s, transform}, segments [{index, start_s, end_s,
                     frames, video, discarded [[t0, t1], …]}], streams {name: {file, dtype, shape, rate_hz}},
                     sha256 of every file
frames.bin           per frame: t_s (f64), segment (u16), pose 4×4 (f32, camera→world), intrinsics 3×3 (f32),
                     exposure_s (f32), tracking state (u8)    — little-endian, fixed record size
depth/<seg>.bin      per frame: zlib-compressed float16 depth (metres, 256×192) + uint8 confidence (0/1/2)
video/<seg>.mov      HEVC RGB, presentation timestamps = t_s of frames.bin
imu.bin              t_s, gravity xyz, user acceleration xyz, rotation rate xyz (f64/f32)
mesh/<seg>.ply       binary PLY: vertices (world), faces, per-face ARKit classification (uchar)
worldmap.arworldmap  only in the project's first pass (or after a re-map)
roomplan.json/.usdz  only in RoomPlan passes
```
Units SI, times on one monotonic clock per pass (`ARFrame.timestamp`), plus the wall-clock start in the manifest.

## Phone → laptop transfer v1 (contract, agent-approved 2026-10-08)
Both paths only move finished `.tscan` files; the laptop verifies the file's SHA-256 before accepting it.
`key` is the laptop pairing key from the QR code; `tag(id, sha) = hex(HMAC-SHA256(key, "rftx1|" + id + "|" + sha))`.

**Cable (USB, app's shared Documents via Apple's file-sharing service):**
```text
TrackScoutTransfer/outbox.json  written by the phone when the user picks "send to laptop by cable":
                                {"v": 1, "phone": <device name>, "passes": [{"id", "file" (path relative to
                                Documents), "size", "sha256", "project", "pass_type", "created_at", "tag"}]}
TrackScoutTransfer/status.json  written by the laptop while copying: {"v": 1, "laptop": <name>,
                                "passes": {<id>: {"received": <bytes>, "size": <bytes>, "done": bool}}}
```
The laptop ignores entries whose `tag` does not verify with its own key (an unpaired laptop gets nothing), copies
in 4 MiB reads into `<id>.part` (resuming from its size), checks the SHA-256 and then reports `done`. The phone
shows progress from `status.json` and marks the pass "on laptop" when `done` is true.

**Bluetooth LE (GATT; bleak has no L2CAP, so L2CAP is not used in v1):** the phone advertises service
`7e0f0001-5a1d-4c55-9b7e-52464f524745` with characteristic `…0002…` (laptop → phone, write with response) and
`…0003…` (phone → laptop, notify). Each GATT value is one **fragment**: `u8 flags` (bit 0 = last fragment of the
message) + payload; fragments are concatenated into **messages**: `u8 type` + body.
| type | direction | body |
|------|-----------|------|
| 1 CHALLENGE | phone → laptop | 16-byte random nonce (sent when the laptop subscribes) |
| 2 AUTH | laptop → phone | JSON `{"laptop": name, "mac": hex(HMAC-SHA256(key, "rftx1-auth|" + nonce))}` |
| 3 OFFER | phone → laptop | JSON `{"passes": [same entries as outbox.json, without "file"]}` — or `{"error": "unpaired"}` and disconnect |
| 4 GET | laptop → phone | JSON `{"id", "offset"}` |
| 5 CHUNK | phone → laptop | `u64 offset` (LE) + `u32 crc32` (LE) + data (≤ 64 KiB) — chunks follow until the end of the file |
| 6 DONE | laptop → phone | JSON `{"id", "sha256"}` after verifying the whole file; the phone marks the pass "on laptop" |
| 7 ERROR | both | JSON `{"error": text}` |
A broken connection resumes with a new CHALLENGE/AUTH and `GET` from the size of the laptop's `.part` file.

**Laptop relay:** a received pass is kept in the engine's TrackScout inbox. Policy `auto` (default) uploads it when
the measured backend throughput is good enough (same thresholds as the phone: estimated time ≤ 10 min and
≥ 1 MB/s, measured on the first parts of the real upload, which stay on the server); otherwise the pass waits
("waiting for upload") and the background loop re-checks every 5 minutes. The Team tab offers **upload now**
(ignores the speed), **upload when the connection is faster** (`auto`) and **keep only on this laptop**.

## Non-functional targets
- Recording at High quality runs ≥ 20 min on an iPhone 15 Pro without dropped depth frames (< 1 %) or thermal stop.
- Pause → Continue keeps alignment (pose jump between segments < 2 cm).
- Upload of a 2 GB pass resumes after a network loss without resending finished parts (spec 0006).
- Importer reads 10,000 frames' poses + depth in < 10 s on a MacBook Air M2.

## Acceptance criteria (→ tests)
- [ ] AC1: `TrackScoutKit` (Swift package, macOS + iOS) unit tests run with `swift test` in CI: segment state
      machine (record/pause/continue/discard), manifest + binary writers, zip writer with checksums, storage
      estimate per preset, pairing-URL parsing, upload client (mock `URLProtocol`: parts, resume, cellular policy).
- [ ] AC2: The app builds for the iOS Simulator in CI without signing (`xcodebuild … CODE_SIGNING_ALLOWED=NO`); the
      project is generated from `project.yml`; a check fails if any third-party package or paid capability appears.
- [ ] AC3: Cross-language contract: CI generates a synthetic `.tscan` with `TrackScoutKit` and the Python importer
      opens it (manifest, frames, depth, mesh, discarded ranges, checksum failure on a flipped byte).
- [ ] AC4: Importer converts poses to Z-up correctly (golden: known ARKit pose → expected RaceForge pose) and skips
      discarded ranges; `raceforge capture info` prints passes, duration, frames, alignment.
- [ ] AC5: Upload end-to-end against the spec 0006 dev backend (Python test with a recorded `.tscan` fixture made by
      the Swift generator): capture object + version created, blob deduplicated on re-upload.
- [ ] AC6: Desktop "Pair TrackScout" shows a QR code that encodes server URL + new `trackscout` token + laptop
      pairing key; the token has only `read` + `edit` (Playwright + pytest).
- [ ] AC7: Upload routing (Swift unit tests with a throttled mock backend): fast backend → direct upload; slow
      backend → the choice is offered with ETAs; switching back resumes without resending parts.
- [ ] AC8: Laptop relay (pytest): a pass received from the phone is stored locally; with a fast (mock) backend it is
      uploaded automatically; with a slow/offline backend it waits, the Team tab shows "waiting for upload", and the
      three choices work; a later fast connection uploads it in the background.
- [ ] AC9: Transfer protocols (pytest + Swift tests on both ends of a loopback transport): chunking, checksums,
      resume after disconnect, unpaired laptop rejected. The USB path is tested against a fake device folder; the
      Bluetooth path against an in-memory transport.
- [ ] AC10 (manual, device checklist in `trackscout_ios/CHECKLIST.md`): install with a free Apple ID; record a 5-min
      corridor pass with one pause; second pass relocalises into the first; RoomPlan pass; upload over Wi-Fi; with
      the backend throttled (Network Link Conditioner) the choice appears and the pass reaches the laptop by cable
      and by Bluetooth, and the laptop relays it; the file opens with `raceforge capture info` and the poses line up
      with the mesh.

## Decisions
- **Upload routing:** backend first; on a slow backend connection the phone offers cable or Bluetooth to a paired
  laptop, which stores the pass and relays it when its own connection is good enough (owner, 2026-10-08). The share
  sheet (AirDrop / Files) stays as an always-free fallback. Local Wi-Fi/Bonjour transfer is not planned.
- **Xcode project:** generated from a reviewed `project.yml` with **XcodeGen** (MIT, build tool only, not
  shipped; ADR-0020), so the project file is readable in reviews and AI agents can edit it safely.
- **Laptop side transfer libraries (ADR-0021):** `pymobiledevice3` (GPL-3.0) for USB access to the app's shared
  documents, `bleak` (MIT) for Bluetooth LE. The iOS side uses only Apple frameworks (CoreBluetooth,
  `UIFileSharingEnabled`).
- **Personal signing:** each owner sets team ID and bundle-id prefix in a git-ignored `Local.xcconfig`
  (template committed), so nobody's Apple ID is in the public repo.
- Collaborative scanning (several phones at once) becomes its own spec right after this one.
- **Delivery in two PRs:** (A) app, `.tscan`, importer, backend upload, pairing; (B) routing decision, cable and
  Bluetooth transfer, laptop relay.

## Open questions
- None blocking. Exact UI layout is proposed in the PR with simulator screenshots (recording itself only works on
  a real LiDAR device).
