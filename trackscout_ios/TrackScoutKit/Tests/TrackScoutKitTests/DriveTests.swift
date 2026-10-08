import Foundation
import XCTest

@testable import TrackScoutKit

/// Spec 0010 AC6/AC7: TrackScout drive mode — pairing code, input mapping, send loop, car messages.
final class DriveTests: XCTestCase {
    let limits = DriveLimits(maxSteer: 0.5, speedLimit: 1.2, reverseFactor: 0.5)

    func testCarPairingCodeRoundTrip() throws {
        let p = CarPairing(url: "ws://192.168.1.50:8765", token: "abcdefghijklmnop")
        let code = try p.code()
        XCTAssertTrue(code.hasPrefix("raceforge://car?v=1&d="))
        XCTAssertEqual(try CarPairing.parse(code), p)
        XCTAssertEqual(try p.socketURL().absoluteString, "ws://192.168.1.50:8765/?token=abcdefghijklmnop")
        XCTAssertEqual(try CarPairing(url: "raceforge-car.local:8765", token: nil).socketURL().absoluteString, "ws://raceforge-car.local:8765/")
        XCTAssertThrowsError(try CarPairing.parse("raceforge://pair?v=1&d=xx"))
        XCTAssertThrowsError(try CarPairing(url: "http://x", token: nil).socketURL())
    }

    /// The desktop encodes the QR in Python (base64url JSON, no padding); same golden as tests/server.
    func testParsesTheDesktopCode() throws {
        let code = "raceforge://car?v=1&d=eyJ1cmwiOiAid3M6Ly8xMC4wLjAuNzo4NzY1IiwgInRva2VuIjogInRvay0xMjM0NTY3ODkwYWJjZGVmIn0"
        XCTAssertEqual(try CarPairing.parse(code), CarPairing(url: "ws://10.0.0.7:8765", token: "tok-1234567890abcdef"))
    }

    func testTouchPads() {
        XCTAssertFalse(DriveMapper.touch(TouchInput(steerX: 1, throttleY: 1, throttleActive: false), limits).engaged)
        let s = DriveMapper.touch(TouchInput(steerX: 1, throttleY: 1, throttleActive: true), limits)
        XCTAssertTrue(s.engaged)
        XCTAssertEqual(s.speed, 1.2, accuracy: 1e-9)
        XCTAssertEqual(s.steer, -0.5, accuracy: 1e-9)  // finger right = steer right (negative)
        XCTAssertEqual(DriveMapper.touch(TouchInput(throttleY: -1, throttleActive: true), limits).speed, -0.6, accuracy: 1e-9)
        XCTAssertEqual(DriveMapper.touch(TouchInput(steerX: 0.05, throttleY: 0.05, throttleActive: true), limits).speed, 0)
        XCTAssertEqual(DriveMapper.touch(TouchInput(throttleActive: true), limits).steer.sign, .plus)  // no -0
    }

    func testGamepadDeadManAndStop() {
        XCTAssertFalse(DriveMapper.gamepad(PadInput(leftX: 1, rightTrigger: 1), limits).engaged)
        let s = DriveMapper.gamepad(PadInput(leftX: 1, rightTrigger: 1, rightShoulder: true), limits)
        XCTAssertTrue(s.engaged)
        XCTAssertEqual(s.speed, 1.2, accuracy: 1e-9)
        let b = DriveMapper.gamepad(PadInput(rightTrigger: 1, rightShoulder: true, buttonB: true), limits)
        XCTAssertTrue(b.stop)
        XCTAssertFalse(b.engaged)
        XCTAssertEqual(DriveMapper.gamepad(nil, limits), .idle)
    }

    func testStopWinsAndGamepadBeforeTouch() {
        let pad = DriveMapper.gamepad(PadInput(rightTrigger: 1, rightShoulder: true), limits)
        let touch = DriveMapper.touch(TouchInput(throttleY: 0.5, throttleActive: true), limits)
        XCTAssertEqual(DriveMapper.combine(pad, touch).source, .gamepad)
        let stop = DriveMapper.gamepad(PadInput(buttonB: true), limits)
        XCTAssertTrue(DriveMapper.combine(touch, stop).stop)
    }

    func testSendLoop() throws {
        var loop = TeleopLoop()
        XCTAssertEqual(loop.tick(.idle), [])
        let drive = DriveMapper.touch(TouchInput(throttleY: 1, throttleActive: true), limits)
        let first = loop.tick(drive)
        XCTAssertEqual(first.count, 1)
        let msg = try JSONSerialization.jsonObject(with: Data(first[0].utf8)) as! [String: Any]
        XCTAssertEqual(msg["type"] as? String, "teleop")
        XCTAssertEqual(msg["speed"] as? Double, 1.2)
        XCTAssertEqual(loop.tick(drive).count, 1)  // keeps refreshing (dead-man)
        XCTAssertEqual(loop.tick(.idle), [#"{"type":"teleop_release"}"#])
        XCTAssertEqual(loop.tick(.idle), [])
        _ = loop.tick(drive)
        XCTAssertEqual(loop.release(), [#"{"type":"teleop_release"}"#])  // app went to background
        XCTAssertEqual(loop.release(), [])
        let stop = DriveSample(source: .touch, engaged: false, stop: true, steer: 0, speed: 0)
        XCTAssertEqual(loop.tick(stop).first?.contains(#""type":"stop""#), true)
        XCTAssertEqual(loop.tick(stop), [])  // STOP is sent once per press
    }

    func testCarMessages() {
        XCTAssertEqual(CarMessage.parse(#"{"type":"hello","car":"car-1","mode":"test","rate_hz":20}"#), .hello(car: "car-1", mode: "test"))
        let t = CarMessage.parse(
            #"{"type":"telemetry","frame":{"state":"teleop","mode":"test","seq":3,"cmd":{"steering_rad":0.1,"speed_m_s":0.5},"power":{"ev3_battery_v":7.9},"loop":{"rate_hz":50,"jitter_ms":0.4},"faults":[]}}"#)
        guard case .telemetry(let f) = t else { return XCTFail("\(t)") }
        XCTAssertEqual(f.state, "teleop")
        XCTAssertEqual(f.cmd?.speedMS, 0.5)
        XCTAssertEqual(f.batteryV, 7.9)
        XCTAssertEqual(f.loop?.rateHz, 50)
        XCTAssertEqual(CarMessage.parse(#"{"type":"ack","cmd":"teleop","ok":false,"detail":"race mode"}"#), .ack(cmd: "teleop", ok: false, detail: "race mode"))
        XCTAssertEqual(CarMessage.parse("nonsense"), .other)
    }

    /// CI starts `tests/fake_car_server.py` and sets RF_FAKE_CAR_URL / RF_FAKE_CAR_TOKEN.
    func testAgainstThePythonFakeCar() throws {
        let env = ProcessInfo.processInfo.environment
        guard let url = env["RF_FAKE_CAR_URL"] else { throw XCTSkip("RF_FAKE_CAR_URL not set") }
        final class Seen: @unchecked Sendable {
            let lock = NSLock()
            var hello = false
            var states: [String] = []
            var rtt: Double?
        }
        let seen = Seen()
        let link = CarLink()
        link.onMessage = { m in
            seen.lock.withLock {
                if case .hello = m { seen.hello = true }
                if case .telemetry(let f) = m, let s = f.state { seen.states.append(s) }
            }
        }
        link.onRTT = { r in seen.lock.withLock { seen.rtt = r } }
        try link.connect(CarPairing(url: url, token: env["RF_FAKE_CAR_TOKEN"]))
        func wait(_ what: String, _ ok: @escaping () -> Bool) {
            let deadline = Date().addingTimeInterval(5)
            while Date() < deadline, !seen.lock.withLock(ok) { Thread.sleep(forTimeInterval: 0.02) }
            XCTAssertTrue(seen.lock.withLock(ok), what)
        }
        wait("hello") { seen.hello }
        var loop = TeleopLoop()
        let drive = DriveMapper.touch(TouchInput(throttleY: 0.5, throttleActive: true), limits)
        for _ in 0..<6 {
            loop.tick(drive).forEach(link.send)
            Thread.sleep(forTimeInterval: TeleopLoop.period)
        }
        wait("teleop state") { seen.states.contains("teleop") }
        loop.tick(DriveSample(source: .touch, engaged: false, stop: true, steer: 0, speed: 0)).forEach(link.send)
        wait("stop state") { seen.states.last == "stop" }
        wait("rtt") { seen.rtt != nil }
        link.disconnect()
    }
}
