import Foundation
import GameController
import Security
import SwiftUI
import TrackScoutKit

/// Car address + token (from the desktop's QR code) — the token can steer the car, so it lives in the Keychain.
enum CarPairingStore {
    private static let service = "org.raceforge.trackscout"
    private static let account = "car"

    static func load() -> CarPairing? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
            kSecAttrAccount as String: account, kSecReturnData as String: true,
        ]
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess, let data = item as? Data else { return nil }
        return try? JSONDecoder().decode(CarPairing.self, from: data)
    }

    static func save(_ pairing: CarPairing?) {
        let base: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(base as CFDictionary)
        guard let pairing, let data = try? JSONEncoder().encode(pairing) else { return }
        var add = base
        add[kSecValueData as String] = data
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        SecItemAdd(add as CFDictionary, nil)
    }
}

/// Drive mode state (spec 0010 delivery C): link to the car runtime, inputs, 50 ms teleop loop, live stats.
@MainActor
final class DriveModel: ObservableObject {
    enum Link: Equatable {
        case idle, connecting, connected
        case closed(String)
    }

    struct Event: Identifiable {
        let id = UUID()
        let at: Date
        let text: String
    }

    @Published private(set) var pairing: CarPairing? = CarPairingStore.load()
    @Published private(set) var link: Link = .idle
    @Published private(set) var carName: String?
    @Published private(set) var mode: String?
    @Published private(set) var frame: CarFrame?
    @Published private(set) var rtt: Double?
    @Published private(set) var events: [Event] = []
    @Published private(set) var sample = DriveSample.idle
    @Published private(set) var gamepadName: String?
    @Published var touch = TouchInput()
    @Published var speedLimit: Double = 1.0

    let maxSpeed = 2.0
    private let carLink = CarLink()
    private var loop = TeleopLoop()
    private var timer: Timer?

    var raceMode: Bool { (frame?.mode ?? mode) == "race" }
    var slow: Bool { (rtt ?? 0) > CarLink.latencyWarnMs }

    init() {
        #if DEBUG
            // Simulator/UI checks: `SIMCTL_CHILD_RF_DEMO_CAR_URL=ws://127.0.0.1:8792 xcrun simctl launch …`
            if pairing == nil, let url = ProcessInfo.processInfo.environment["RF_DEMO_CAR_URL"] {
                pairing = CarPairing(url: url, token: ProcessInfo.processInfo.environment["RF_DEMO_CAR_TOKEN"])
            }
        #endif
        carLink.onOpen = { [weak self] in Task { @MainActor in self?.link = .connected } }
        carLink.onMessage = { [weak self] m in Task { @MainActor in self?.handle(m) } }
        carLink.onRTT = { [weak self] r in Task { @MainActor in self?.rtt = r } }
        carLink.onClose = { [weak self] reason in
            Task { @MainActor in
                guard let self else { return }
                self.stopLoop()
                if self.link != .idle { self.link = .closed(reason ?? "closed") }
            }
        }
    }

    func pair(_ p: CarPairing) {
        CarPairingStore.save(p)
        pairing = p
    }

    func connect() {
        guard let pairing else { return }
        do {
            link = .connecting
            frame = nil
            rtt = nil
            try carLink.connect(pairing)
            startLoop()
        } catch {
            link = .closed(String(localized: "Invalid car address"))
        }
    }

    func disconnect() {
        releaseNow()
        stopLoop()
        link = .idle
        carLink.disconnect()
    }

    /// STOP button: sent at once (not waiting for the next tick), latched on the car.
    func stop() {
        touch = TouchInput()
        carLink.send(#"{"type":"stop","reason":"operator (TrackScout)"}"#)
        _ = loop.release()
    }

    func note(_ text: String) {
        guard let data = try? JSONSerialization.data(withJSONObject: ["type": "note", "text": text]) else { return }
        carLink.send(String(decoding: data, as: UTF8.self))
    }

    /// App left the foreground / screen closed: hand back to the controller immediately.
    func releaseNow() {
        touch = TouchInput()
        loop.release().forEach(carLink.send)
        sample = .idle
    }

    private func startLoop() {
        stopLoop()
        let t = Timer(timeInterval: TeleopLoop.period, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.tick() }
        }
        RunLoop.main.add(t, forMode: .common)  // keeps running while a finger drags
        timer = t
    }

    private func stopLoop() {
        timer?.invalidate()
        timer = nil
    }

    private func tick() {
        let limits = DriveLimits(speedLimit: speedLimit)
        let pad = GCController.current?.extendedGamepad
        gamepadName = GCController.current?.vendorName
        let padInput = pad.map {
            PadInput(
                leftX: Double($0.leftThumbstick.xAxis.value), rightTrigger: Double($0.rightTrigger.value),
                leftTrigger: Double($0.leftTrigger.value), rightShoulder: $0.rightShoulder.isPressed,
                buttonB: $0.buttonB.isPressed)
        }
        var s = DriveMapper.combine(DriveMapper.gamepad(padInput, limits), DriveMapper.touch(touch, limits))
        if raceMode || link != .connected { s = s.stop ? s : .idle }
        loop.tick(s).forEach(carLink.send)
        if s != sample { sample = s }
    }

    private func handle(_ m: CarMessage) {
        switch m {
        case .hello(let car, let mode):
            carName = car
            self.mode = mode
            link = .connected
        case .telemetry(let f):
            frame = f
        case .event(let kind, let detail):
            log("\(kind) \(detail)")
        case .ack(let cmd, let ok, let detail):
            if !ok { log(String(localized: "\(cmd) refused: \(detail)")) }
        case .other:
            break
        }
    }

    private func log(_ text: String) {
        events.insert(Event(at: Date(), text: text), at: 0)
        if events.count > 100 { events.removeLast() }
    }
}
