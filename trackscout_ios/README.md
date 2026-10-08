# TrackScout (iOS)

Free LiDAR scanning app for RaceForge tracks (spec 0007). Needs a LiDAR iPhone/iPad (iPhone 15 Pro or newer
Pro model, iPad Pro) with iOS 18+, and a Mac with Xcode. A **free Apple ID** is enough.

## Install on your iPhone
1. `brew install xcodegen`
2. `cp Config/Local.xcconfig.template Config/Local.xcconfig` and fill in:
   - `RF_DEVELOPMENT_TEAM`: Xcode → Settings → Accounts → your Apple ID → *Personal Team* (10-character ID);
   - `RF_BUNDLE_PREFIX`: something unique, e.g. `com.yourname`.
3. `xcodegen generate && open TrackScout.xcodeproj`
4. Connect the iPhone by cable, select it as run destination, press **Run**.
5. First launch only: on the iPhone open Settings → General → VPN & Device Management → trust your Apple ID.
   Enable *Developer Mode* if iOS asks for it (Settings → Privacy & Security).

Free Apple ID builds expire after **7 days**: just press Run again. Recordings stay on the phone.

## Pair with the team backend
Desktop app → Team tab → **Pair TrackScout** → scan the QR code in TrackScout → Settings → Pair.

## Develop
- Logic without camera lives in `TrackScoutKit` (`swift test` on the Mac).
- `swift run tscan-synth out.tscan` writes a synthetic pass; `raceforge capture info out.tscan` reads it.
- Device test before every release: [`CHECKLIST.md`](CHECKLIST.md).
