import SwiftUI
import TrackScoutKit
import VisionKit

/// Drive the car (spec 0010 delivery C). iPhone: controls only (landscape); iPad: controls + live stats.
struct DriveView: View {
    @StateObject private var model = DriveModel()
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.horizontalSizeClass) private var hSize
    @State private var showPairing = false

    private var isPad: Bool { UIDevice.current.userInterfaceIdiom == .pad }

    var body: some View {
        NavigationStack {
            Group {
                if model.pairing == nil {
                    ContentUnavailableView {
                        Label("No car paired", systemImage: "car.side")
                    } description: {
                        Text("In RaceForge open Live → Pair phone with car and scan the QR code.")
                    } actions: {
                        Button("Pair car") { showPairing = true }.buttonStyle(.borderedProminent)
                    }
                } else if isPad && hSize == .regular {
                    HStack(spacing: 0) {
                        StatsPanel(model: model).frame(width: 340)
                        Divider()
                        DriveControls(model: model)
                    }
                } else {
                    DriveControls(model: model, compactStatus: true)
                }
            }
            .navigationTitle(model.carName ?? String(localized: "Drive"))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") {
                        model.disconnect()
                        dismiss()
                    }
                }
                ToolbarItem(placement: .primaryAction) {
                    Menu {
                        Button("Pair car", systemImage: "qrcode.viewfinder") { showPairing = true }
                        if model.link == .connected {
                            Button("Disconnect", systemImage: "bolt.slash") { model.disconnect() }
                        } else if model.pairing != nil {
                            Button("Connect", systemImage: "bolt") { model.connect() }
                        }
                    } label: {
                        Image(systemName: "ellipsis.circle")
                    }
                }
            }
            .sheet(isPresented: $showPairing) { CarPairingView(model: model) }
        }
        .onAppear { if model.pairing != nil { model.connect() } }
        .onChange(of: scenePhase) { _, phase in if phase != .active { model.releaseNow() } }
        .onDisappear { model.disconnect() }
        .persistentSystemOverlays(.hidden)
    }
}

/// Steering pad (left thumb), throttle pad (right thumb, finger down = dead-man), STOP and speed limit.
/// Wide screens (landscape, iPad): pads left and right of the readout; narrow (iPhone portrait): stacked.
struct DriveControls: View {
    @ObservedObject var model: DriveModel
    var compactStatus = false

    private var enabled: Bool { model.link == .connected && !model.raceMode }

    var body: some View {
        GeometryReader { g in
            let wide = g.size.width >= 600
            VStack(spacing: 12) {
                HStack {
                    if compactStatus { StatusLine(model: model) }
                    Spacer()
                    stopButton
                }
                if wide {
                    Spacer(minLength: 0)
                    HStack(alignment: .center, spacing: 24) {
                        SteerPad(value: $model.touch.steerX)
                        middle.frame(maxWidth: 240)
                        ThrottlePad(value: $model.touch.throttleY, active: $model.touch.throttleActive)
                    }
                    .disabled(!enabled).opacity(enabled ? 1 : 0.4)
                    Spacer(minLength: 0)
                } else {
                    middle
                    Spacer(minLength: 0)
                    HStack(alignment: .bottom, spacing: 16) {
                        SteerPad(value: $model.touch.steerX)
                        ThrottlePad(value: $model.touch.throttleY, active: $model.touch.throttleActive)
                    }
                    .disabled(!enabled).opacity(enabled ? 1 : 0.4)
                    if compactStatus { Text("Turn the phone sideways for more room.").font(.caption2).foregroundStyle(.secondary) }
                }
                footer
            }
            .padding()
            .frame(width: g.size.width, height: g.size.height)
        }
    }

    private var stopButton: some View {
        Button {
            model.stop()
        } label: {
            Text("STOP").font(.title2.bold()).frame(minWidth: 140, minHeight: 50)
        }
        .buttonStyle(.borderedProminent).tint(.red)
        .accessibilityIdentifier("drive-stop")
    }

    private var middle: some View {
        VStack(spacing: 6) {
            Text("Speed limit \(model.speedLimit, specifier: "%.2f") m/s").font(.caption).monospacedDigit()
            Slider(value: $model.speedLimit, in: 0.1...model.maxSpeed, step: 0.05)
            Readout(sample: model.sample, gamepad: model.gamepadName)
        }
        .fixedSize(horizontal: false, vertical: true)
    }

    @ViewBuilder private var footer: some View {
        if model.raceMode {
            Text("Race mode: the car refuses teleop.").foregroundStyle(.orange)
        } else if model.link != .connected {
            LinkHint(model: model)
        } else {
            Text("Right pad: hold to drive, up = forward. Left pad: steer. Xbox: hold RB, RT/LT, left stick, B = stop.")
                .font(.caption).foregroundStyle(.secondary).multilineTextAlignment(.center)
        }
    }
}

struct LinkHint: View {
    @ObservedObject var model: DriveModel
    var body: some View {
        switch model.link {
        case .idle: Button("Connect") { model.connect() }
        case .connecting: ProgressView("Connecting to the car…")
        case .connected: EmptyView()
        case .closed(let reason):
            VStack {
                Text(verbatim: reason).font(.caption).foregroundStyle(.red)
                Button("Reconnect") { model.connect() }
            }
        }
    }
}

struct Readout: View {
    let sample: DriveSample
    let gamepad: String?
    var body: some View {
        VStack(spacing: 2) {
            Text(sample.engaged ? "Driving" : "Released").font(.headline).foregroundStyle(sample.engaged ? .green : .secondary)
            Text("\(Int((sample.steer * 180 / .pi).rounded()))° · \(sample.speed, specifier: "%.2f") m/s").monospacedDigit()
            if let gamepad {
                Label { Text(verbatim: gamepad).lineLimit(1) } icon: { Image(systemName: "gamecontroller") }.font(.caption)
            }
        }
    }
}

/// Horizontal pad: drag left/right to steer; springs back to straight when released.
struct SteerPad: View {
    @Binding var value: Double
    var body: some View {
        GeometryReader { g in
            let w = g.size.width
            ZStack {
                Capsule().fill(.quaternary)
                Circle().fill(.blue).frame(width: 64, height: 64)
                    .offset(x: value * (w / 2 - 32))
            }
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { v in value = max(-1, min(1, (v.location.x - w / 2) / (w / 2 - 32))) }
                    .onEnded { _ in value = 0 })
        }
        .frame(minWidth: 180, maxWidth: 300, minHeight: 80, maxHeight: 90)
        .accessibilityIdentifier("drive-steer")
    }
}

/// Vertical pad: finger down = dead-man engaged; up = forward, down = reverse.
struct ThrottlePad: View {
    @Binding var value: Double
    @Binding var active: Bool
    var body: some View {
        GeometryReader { g in
            let h = g.size.height
            ZStack {
                Capsule().fill(active ? Color.green.opacity(0.25) : Color.secondary.opacity(0.2))
                Circle().fill(active ? .green : .gray).frame(width: 70, height: 70)
                    .offset(y: -value * (h / 2 - 35))
            }
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { v in
                        active = true
                        value = max(-1, min(1, -(v.location.y - h / 2) / (h / 2 - 35)))
                    }
                    .onEnded { _ in
                        active = false
                        value = 0
                    })
        }
        .frame(minWidth: 90, maxWidth: 110, minHeight: 180, maxHeight: 280)
        .accessibilityIdentifier("drive-throttle")
    }
}

/// iPhone: one line with link, car state, latency and battery.
struct StatusLine: View {
    @ObservedObject var model: DriveModel
    var body: some View {
        HStack(spacing: 10) {
            Circle().fill(model.link == .connected ? .green : .red).frame(width: 10, height: 10)
            Text(verbatim: model.frame?.state ?? "–").font(.headline)
            if let rtt = model.rtt {
                Text("\(Int(rtt)) ms").monospacedDigit().foregroundStyle(model.slow ? .red : .secondary)
            }
            if let v = model.frame?.batteryV { Text("\(v, specifier: "%.1f") V").monospacedDigit().foregroundStyle(.secondary) }
        }
        .font(.subheadline)
    }
}

/// iPad: live stats and events next to the controls.
struct StatsPanel: View {
    @ObservedObject var model: DriveModel
    @State private var note = ""

    var body: some View {
        List {
            Section("Car") {
                row("Connection", model.link == .connected ? String(localized: "connected") : String(localized: "not connected"))
                row("Mode", model.frame?.mode ?? model.mode ?? "–")
                row("State", model.frame?.state ?? "–")
                LabeledContent("Latency") {
                    Text(model.rtt.map { "\(Int($0)) ms" } ?? "–").monospacedDigit()
                        .foregroundStyle(model.slow ? .red : .primary)
                }
                if model.slow { Text("Above 100 ms — drive carefully.").font(.caption).foregroundStyle(.red) }
            }
            Section("Drive") {
                row("Command", model.frame?.cmd.map { String(format: "%.0f° · %.2f m/s", $0.steeringRad * 180 / .pi, $0.speedMS) } ?? "–")
                row("Measured speed", model.frame?.meas?.speedMS.map { String(format: "%.2f m/s", $0) } ?? "–")
                row("Battery", model.frame?.batteryV.map { String(format: "%.1f V", $0) } ?? "–")
                row("Control loop", model.frame?.loop.map { String(format: "%.0f Hz · %.1f ms", $0.rateHz, $0.jitterMs ?? 0) } ?? "–")
                row("Faults", model.frame?.faults?.isEmpty == false ? model.frame!.faults!.joined(separator: ", ") : "–")
            }
            Section("Note in the run log") {
                HStack {
                    TextField("Note", text: $note)
                    Button("Add") {
                        model.note(note)
                        note = ""
                    }.disabled(note.isEmpty || model.link != .connected)
                }
            }
            Section("Events") {
                if model.events.isEmpty { Text("No events yet").foregroundStyle(.secondary) }
                ForEach(model.events) { e in
                    VStack(alignment: .leading) {
                        Text(e.at, style: .time).font(.caption2).foregroundStyle(.secondary)
                        Text(verbatim: e.text).font(.caption)
                    }
                }
            }
        }
    }

    private func row(_ title: LocalizedStringKey, _ value: String) -> some View {
        LabeledContent(title) { Text(verbatim: value).monospacedDigit() }
    }
}

/// Scan the desktop's "pair phone with car" QR code, or type the address and token.
struct CarPairingView: View {
    @ObservedObject var model: DriveModel
    @Environment(\.dismiss) private var dismiss
    @State private var scanning = false
    @State private var url = ""
    @State private var token = ""
    @State private var error: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text("In RaceForge open Live, enter the car's address and token, then click “Pair phone with car”.").font(.caption)
                    if DataScannerViewController.isSupported {
                        Button("Scan QR code", systemImage: "qrcode.viewfinder") { scanning = true }
                    }
                }
                Section("Or enter by hand") {
                    TextField("ws://raceforge-car.local:8765", text: $url)
                        .textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL)
                    SecureField("Token", text: $token)
                    Button("Save") { save(CarPairing(url: url, token: token.isEmpty ? nil : token)) }.disabled(url.isEmpty)
                }
                if let error { Text(verbatim: error).foregroundStyle(.red) }
            }
            .navigationTitle("Pair car")
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } } }
            .sheet(isPresented: $scanning) {
                QRScanner(prefix: "raceforge://car") { text in
                    scanning = false
                    do { save(try CarPairing.parse(text)) } catch { self.error = String(localized: "This is not a RaceForge car code.") }
                }
                .ignoresSafeArea()
            }
            .onAppear {
                url = model.pairing?.url ?? ""
            }
        }
    }

    private func save(_ p: CarPairing) {
        guard (try? p.socketURL()) != nil else {
            error = String(localized: "Invalid car address")
            return
        }
        model.pair(p)
        model.connect()
        dismiss()
    }
}
