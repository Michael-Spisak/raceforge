# TrackScout device checklist (spec 0007 AC10)

Run on a real LiDAR device before merging TrackScout changes that touch recording or transfer.
Note device, iOS version, date and the result of every line in the PR.

## Part A — recording, export, upload
- [ ] Fresh install with a free Apple ID following `README.md`.
- [ ] Non-LiDAR device (or simulator) shows "LiDAR required" and cannot record.
- [ ] New project, first pass `walkthrough`, preset *High*: record ~5 min of corridor; storage/remaining minutes shown.
- [ ] Warnings appear when walking too fast, in the dark and when covering the camera (tracking limited).
- [ ] Pause → walk a few metres → Continue: a second segment exists; "discard last 10 s" marks a range.
- [ ] Second pass (`low`) relocalises into the first pass's world map; the pass shows "aligned".
- [ ] RoomPlan pass completes and is attached to the project.
- [ ] Pass list shows duration, size, segments and alignment; the 3D mesh preview opens; delete works.
- [ ] Share sheet exports the `.tscan` (AirDrop to the Mac).
- [ ] Pair via QR code; upload over Wi-Fi finishes; the capture object appears in the desktop Team tab.
- [ ] Cellular: upload asks before using mobile data.
- [ ] `raceforge capture info <file>` lists passes, duration, frames and alignment; in the importer the poses
      line up with the mesh (spot check in a notebook or the scan viewer).

## Part B — slow backend, cable and Bluetooth (after PR B)
- [ ] With the backend throttled (Network Link Conditioner, "3G"), the routing choice appears with ETAs.
- [ ] Cable: the pass reaches the paired laptop; the laptop relays it to the backend.
- [ ] Bluetooth: a small pass reaches the laptop; disconnect mid-transfer resumes.
- [ ] An unpaired laptop is rejected (cable and Bluetooth).
