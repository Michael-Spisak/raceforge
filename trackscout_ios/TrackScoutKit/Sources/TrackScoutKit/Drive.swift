import Foundation

// TrackScout drive mode (spec 0010 delivery C): drive the car runtime (spec 0005) directly over Wi-Fi.
// Same mapping and dead-man rules as the desktop teleop panel (frontend/src/teleop/input.ts).

/// Content of the desktop's "pair phone with car" QR code: `raceforge://car?v=1&d=<base64url(JSON)>`.
public struct CarPairing: Codable, Equatable, Sendable {
    public var url: String
    public var token: String?

    public init(url: String, token: String?) {
        self.url = url
        self.token = token
    }

    public enum ParseError: Error, Equatable { case notACarCode, unsupportedVersion, invalidPayload }

    public static func parse(_ text: String) throws -> CarPairing {
        guard let comps = URLComponents(string: text.trimmingCharacters(in: .whitespacesAndNewlines)),
            comps.scheme == "raceforge", comps.host == "car"
        else { throw ParseError.notACarCode }
        let items = Dictionary((comps.queryItems ?? []).map { ($0.name, $0.value ?? "") }, uniquingKeysWith: { a, _ in a })
        guard items["v"] == "1" else { throw ParseError.unsupportedVersion }
        guard let d = items["d"], let json = Data(base64URL: d),
            let p = try? JSONDecoder().decode(CarPairing.self, from: json), (try? p.socketURL()) != nil
        else { throw ParseError.invalidPayload }
        return p
    }

    public func code() throws -> String {
        "raceforge://car?v=1&d=\(try JSONEncoder().encode(self).base64URL)"
    }

    /// `ws://host:port/?token=…` (scheme optional in `url`).
    public func socketURL() throws -> URL {
        let raw = url.contains("://") ? url : "ws://\(url)"
        guard var comps = URLComponents(string: raw), ["ws", "wss"].contains(comps.scheme ?? ""), comps.host?.isEmpty == false
        else { throw ParseError.invalidPayload }
        if comps.path.isEmpty { comps.path = "/" }
        if let token, !token.isEmpty { comps.queryItems = [URLQueryItem(name: "token", value: token)] }
        guard let u = comps.url else { throw ParseError.invalidPayload }
        return u
    }
}

public struct DriveLimits: Equatable, Sendable {
    public var maxSteer: Double  // rad
    public var speedLimit: Double  // m/s
    public var reverseFactor: Double

    public init(maxSteer: Double = 30 * .pi / 180, speedLimit: Double = 1.0, reverseFactor: Double = 0.5) {
        self.maxSteer = maxSteer
        self.speedLimit = speedLimit
        self.reverseFactor = reverseFactor
    }
}

public struct DriveSample: Equatable, Sendable {
    public enum Source: String, Sendable { case gamepad, touch, none }
    public var source: Source
    public var engaged: Bool
    public var stop: Bool
    public var steer: Double
    public var speed: Double

    public static let idle = DriveSample(source: .none, engaged: false, stop: false, steer: 0, speed: 0)
}

/// Xbox mapping (GameController framework): left stick X steers, RT/LT forward/reverse, RB = dead-man, B = stop.
public struct PadInput: Equatable, Sendable {
    public var leftX: Double
    public var rightTrigger: Double
    public var leftTrigger: Double
    public var rightShoulder: Bool
    public var buttonB: Bool

    public init(leftX: Double = 0, rightTrigger: Double = 0, leftTrigger: Double = 0, rightShoulder: Bool = false, buttonB: Bool = false) {
        self.leftX = leftX
        self.rightTrigger = rightTrigger
        self.leftTrigger = leftTrigger
        self.rightShoulder = rightShoulder
        self.buttonB = buttonB
    }
}

/// Two thumb pads on the screen: steering (x in -1…1, right = +) and throttle (y in -1…1, up = forward).
/// A finger on the throttle pad is the dead-man; the steering pad alone does not drive.
public struct TouchInput: Equatable, Sendable {
    public var steerX: Double
    public var throttleY: Double
    public var throttleActive: Bool

    public init(steerX: Double = 0, throttleY: Double = 0, throttleActive: Bool = false) {
        self.steerX = steerX
        self.throttleY = throttleY
        self.throttleActive = throttleActive
    }
}

public enum DriveMapper {
    public static let deadZone = 0.12

    public static func applyDeadZone(_ v: Double, _ dz: Double = deadZone) -> Double {
        guard v.isFinite, abs(v) > dz else { return 0 }
        return (v < 0 ? -1 : 1) * (abs(v) - dz) / (1 - dz)
    }

    static func scale(steerIn: Double, throttle: Double, _ l: DriveLimits) -> (steer: Double, speed: Double) {
        let t = min(1, max(-1, throttle))
        // Positive steer = left (RaceForge convention); stick/finger right steers right.
        let steer = -min(1, max(-1, steerIn)) * l.maxSteer + 0  // + 0 turns -0 into 0
        return (steer, t >= 0 ? t * l.speedLimit : t * l.speedLimit * l.reverseFactor)
    }

    public static func gamepad(_ p: PadInput?, _ l: DriveLimits) -> DriveSample {
        guard let p else { return .idle }
        let engaged = p.rightShoulder && !p.buttonB
        let (steer, speed) = scale(
            steerIn: applyDeadZone(p.leftX), throttle: applyDeadZone(p.rightTrigger - p.leftTrigger, 0.05), l)
        return DriveSample(source: .gamepad, engaged: engaged, stop: p.buttonB, steer: engaged ? steer : 0, speed: engaged ? speed : 0)
    }

    public static func touch(_ t: TouchInput, _ l: DriveLimits) -> DriveSample {
        guard t.throttleActive else { return DriveSample(source: .touch, engaged: false, stop: false, steer: 0, speed: 0) }
        let (steer, speed) = scale(steerIn: applyDeadZone(t.steerX, 0.08), throttle: applyDeadZone(t.throttleY, 0.08), l)
        return DriveSample(source: .touch, engaged: true, stop: false, steer: steer, speed: speed)
    }

    /// STOP from any device wins; otherwise the first engaged device (gamepad before touch).
    public static func combine(_ samples: DriveSample...) -> DriveSample {
        if let s = samples.first(where: { $0.stop }) { return DriveSample(source: s.source, engaged: false, stop: true, steer: 0, speed: 0) }
        return samples.first(where: { $0.engaged }) ?? .idle
    }
}

/// Decides what to send every 50 ms (spec 0010): teleop while engaged, one `teleop_release` when letting go,
/// one `stop` on the rising edge of STOP. Pure state machine; the app drives it from a timer.
public struct TeleopLoop: Sendable {
    public static let period: TimeInterval = 0.05
    private var engaged = false
    private var stopping = false

    public init() {}

    public mutating func tick(_ s: DriveSample) -> [String] {
        var out: [String] = []
        if s.stop && !stopping { out.append(#"{"type":"stop","reason":"operator (TrackScout)"}"#) }
        stopping = s.stop
        if s.engaged {
            let steer = (s.steer * 1e4).rounded() / 1e4
            let speed = (s.speed * 1e3).rounded() / 1e3
            out.append(#"{"type":"teleop","steer":\#(steer),"speed":\#(speed)}"#)
        } else if engaged {
            out.append(#"{"type":"teleop_release"}"#)
        }
        engaged = s.engaged
        return out
    }

    /// App went to the background, the screen closed or the link dropped: hand back now.
    public mutating func release() -> [String] {
        defer { engaged = false }
        return engaged ? [#"{"type":"teleop_release"}"#] : []
    }
}

// MARK: - messages from the car runtime (spec 0005 rf-telemetry)

public struct CarFrame: Codable, Equatable, Sendable {
    public struct Cmd: Codable, Equatable, Sendable {
        public var steeringRad: Double
        public var speedMS: Double
        enum CodingKeys: String, CodingKey {
            case steeringRad = "steering_rad"
            case speedMS = "speed_m_s"
        }
    }
    public struct Meas: Codable, Equatable, Sendable {
        public var steeringRad: Double?
        public var speedMS: Double?
        enum CodingKeys: String, CodingKey {
            case steeringRad = "steering_rad"
            case speedMS = "speed_m_s"
        }
    }
    public struct Power: Codable, Equatable, Sendable {
        public var ev3BatteryV: Double?
        public var motorBatteryV: Double?
        public var boardBatteryV: Double?
        enum CodingKeys: String, CodingKey {
            case ev3BatteryV = "ev3_battery_v"
            case motorBatteryV = "motor_battery_v"
            case boardBatteryV = "board_battery_v"
        }
    }
    public struct Loop: Codable, Equatable, Sendable {
        public var rateHz: Double
        public var jitterMs: Double?
        public var deadlineMisses: Int?
        enum CodingKeys: String, CodingKey {
            case rateHz = "rate_hz"
            case jitterMs = "jitter_ms"
            case deadlineMisses = "deadline_misses"
        }
    }
    public var state: String?
    public var mode: String?
    public var faults: [String]?
    public var cmd: Cmd?
    public var meas: Meas?
    public var power: Power?
    public var loop: Loop?

    public var batteryV: Double? { power?.ev3BatteryV ?? power?.motorBatteryV ?? power?.boardBatteryV }
}

public enum CarMessage: Equatable, Sendable {
    case hello(car: String, mode: String)
    case telemetry(CarFrame)
    case event(kind: String, detail: String)
    case ack(cmd: String, ok: Bool, detail: String)
    case other

    public static func parse(_ text: String) -> CarMessage {
        guard let data = text.data(using: .utf8),
            let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        else { return .other }
        switch obj["type"] as? String {
        case "hello":
            return .hello(car: obj["car"] as? String ?? "car", mode: obj["mode"] as? String ?? "test")
        case "telemetry":
            guard let f = obj["frame"], let raw = try? JSONSerialization.data(withJSONObject: f),
                let frame = try? JSONDecoder().decode(CarFrame.self, from: raw)
            else { return .other }
            return .telemetry(frame)
        case "event":
            return .event(kind: obj["kind"] as? String ?? "", detail: "\(obj["detail"] ?? "")")
        case "ack":
            return .ack(cmd: obj["cmd"] as? String ?? "", ok: obj["ok"] as? Bool ?? false, detail: "\(obj["detail"] ?? "")")
        default:
            return .other
        }
    }
}

// MARK: - WebSocket client

/// Connection to one car runtime. `onMessage` / `onClose` run on an arbitrary queue.
public final class CarLink: NSObject, @unchecked Sendable, URLSessionWebSocketDelegate {
    public static let latencyWarnMs = 100.0
    private var task: URLSessionWebSocketTask?
    private var session: URLSession?
    private let lock = NSLock()
    public var onMessage: @Sendable (CarMessage) -> Void = { _ in }
    public var onClose: @Sendable (String?) -> Void = { _ in }
    public var onRTT: @Sendable (Double) -> Void = { _ in }
    public var onOpen: @Sendable () -> Void = {}

    public override init() {}

    public func connect(_ pairing: CarPairing) throws {
        let url = try pairing.socketURL()
        disconnect()
        let session = URLSession(configuration: .ephemeral, delegate: self, delegateQueue: nil)
        let task = session.webSocketTask(with: url)
        lock.withLock {
            self.session = session
            self.task = task
        }
        task.resume()
        receive(task)
        ping(task)
    }

    public func send(_ text: String) {
        lock.withLock { task }?.send(.string(text)) { _ in }
    }

    public func disconnect() {
        let t = lock.withLock { () -> URLSessionWebSocketTask? in
            defer { task = nil }
            return task
        }
        t?.cancel(with: .normalClosure, reason: nil)
        lock.withLock { session }?.finishTasksAndInvalidate()
    }

    private func receive(_ task: URLSessionWebSocketTask) {
        task.receive { [weak self] result in
            guard let self, self.lock.withLock({ self.task === task }) else { return }
            switch result {
            case .success(.string(let text)):
                self.onMessage(CarMessage.parse(text))
                self.receive(task)
            case .success(.data(let data)):
                self.onMessage(CarMessage.parse(String(decoding: data, as: UTF8.self)))
                self.receive(task)
            case .success:
                self.receive(task)
            case .failure(let error):
                self.finish(task, error.localizedDescription)
            }
        }
    }

    /// Ends `task` once if it is still the current connection: `onClose` fires exactly once, and never for
    /// a connection that a newer `connect` already replaced.
    private func finish(_ task: URLSessionWebSocketTask, _ reason: String?) {
        let current = lock.withLock { () -> Bool in
            guard self.task === task else { return false }
            self.task = nil
            return true
        }
        guard current else { return }
        task.cancel(with: .goingAway, reason: nil)
        onClose(reason)
    }

    static let pongTimeout: TimeInterval = 3

    private func ping(_ task: URLSessionWebSocketTask) {
        let t0 = Date()
        let answered = NSLock()
        var gotPong = false
        task.sendPing { [weak self] error in
            answered.withLock { gotPong = true }
            guard let self, self.lock.withLock({ self.task === task }) else { return }
            guard error == nil else {
                self.finish(task, String(localized: "car stopped answering"))
                return
            }
            self.onRTT(Date().timeIntervalSince(t0) * 1000)
            DispatchQueue.global().asyncAfter(deadline: .now() + 1) { [weak self] in self?.ping(task) }
        }
        // A silently dropped Wi-Fi link never answers: report it instead of showing a stale RTT.
        DispatchQueue.global().asyncAfter(deadline: .now() + Self.pongTimeout) { [weak self] in
            if !answered.withLock({ gotPong }) { self?.finish(task, String(localized: "car stopped answering")) }
        }
    }

    public func urlSession(_ session: URLSession, webSocketTask: URLSessionWebSocketTask, didOpenWithProtocol protocol: String?) {
        guard lock.withLock({ task === webSocketTask }) else { return }
        onOpen()
    }

    public func urlSession(
        _ session: URLSession, webSocketTask: URLSessionWebSocketTask, didCloseWith closeCode: URLSessionWebSocketTask.CloseCode,
        reason: Data?
    ) {
        finish(webSocketTask, reason.flatMap { String(data: $0, encoding: .utf8) } ?? "closed (\(closeCode.rawValue))")
    }
}
