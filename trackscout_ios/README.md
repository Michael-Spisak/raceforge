# TrackScout (iOS)

Free LiDAR scanning app for RaceForge tracks (spec 0007). Needs a LiDAR iPhone/iPad (iPhone 15 Pro or newer
Pro model, iPad Pro) with iOS 18+, and a Mac with Xcode. A **free Apple ID** is enough.

## Install on your iPhone
0. Xcode → Settings → Accounts → add your Apple ID (a free one is enough).
1. `brew install xcodegen`
2. `cp Config/Local.xcconfig.template Config/Local.xcconfig` and fill in:
   - `RF_DEVELOPMENT_TEAM`: your *Personal Team* ID (10 characters). Xcode does not show it directly: open the
     generated project, pick your team under Target → Signing & Capabilities, then run
     `grep -m1 DEVELOPMENT_TEAM TrackScout.xcodeproj/project.pbxproj` and copy the value here;
   - `RF_BUNDLE_PREFIX`: something unique, e.g. `com.yourname`.
3. `xcodegen generate && open TrackScout.xcodeproj`
4. Connect the iPhone by cable ("Trust this computer"), enable Settings → Privacy & Security → *Developer Mode*
   on the phone, select it as run destination, press **Run**.
5. First launch only: on the iPhone open Settings → General → VPN & Device Management → trust your Apple ID,
   then press Run again.

Free Apple ID builds expire after **7 days**: just press Run again. Recordings stay on the phone.

## Pair with the team backend
Desktop app → Team tab → **Pair TrackScout** → scan the QR code in TrackScout → Settings → Pair.

### Test without the team server (backend on your laptop, phone in the same Wi-Fi)
1. `uv run raceforge backend dev --lan --admin admin:<password>`: it prints the address to use, e.g.
   `http://192.168.1.20:8080` (plain HTTP, test data only).
2. `uv run raceforge ui` → Team tab → log in with **that** address (not `127.0.0.1`: the phone cannot reach it,
   and the Team tab warns about it) → create a workspace → **Pair TrackScout**.
3. On the phone allow "Local Network" when iOS asks, then scan the QR code.
4. For cable/Bluetooth transfers to the laptop: `uv sync --extra transfer` once.

## Develop
- Logic without camera lives in `TrackScoutKit` (`swift test` on the Mac).
- `swift run tscan-synth out.tscan` writes a synthetic pass; `raceforge capture info out.tscan` reads it.
- Device test before every release: [`CHECKLIST.md`](CHECKLIST.md).
