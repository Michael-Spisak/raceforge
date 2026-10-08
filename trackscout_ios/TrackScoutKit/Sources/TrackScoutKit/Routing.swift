import Foundation

/// When an upload counts as "too slow" (spec 0007 scope 8; adjustable in the settings).
public struct RoutingThresholds: Codable, Equatable, Sendable {
    /// Estimated time for the rest of the pass above this → ask (seconds).
    public var maxETA: TimeInterval
    /// Measured throughput below this → ask (bytes per second).
    public var minRate: Double

    public init(maxETA: TimeInterval = 600, minRate: Double = 1_000_000) {
        self.maxETA = maxETA
        self.minRate = minRate
    }
}

/// One way to get a pass off the phone, with its estimated time.
public struct RouteOption: Equatable, Sendable {
    public enum Kind: String, Sendable { case backend, cable, bluetooth }
    public let kind: Kind
    public let eta: TimeInterval
    public let recommended: Bool
}

/// Thrown by `ThroughputGate` when the backend is too slow; the parts sent so far stay on the server.
public struct SlowBackend: Error, Equatable, Sendable {
    public let rate: Double
    public let eta: TimeInterval
    public let done: Int64
    public let total: Int64

    public var options: [RouteOption] { Routing.options(remaining: total - done, total: total, backendRate: rate) }
}

public enum Routing {
    /// Conservative transfer rates for the estimates (bytes per second).
    public static let cableRate = 20_000_000.0
    public static let bluetoothRate = 150_000.0
    /// Bluetooth LE is only recommended when it finishes within this time.
    public static let bluetoothRecommendedETA: TimeInterval = 10 * 60

    /// The three choices of the "slow backend" dialog. Backend continues with the remaining bytes; cable and
    /// Bluetooth send the whole pass to the laptop (which relays it later).
    public static func options(remaining: Int64, total: Int64, backendRate: Double) -> [RouteOption] {
        let backend = backendRate > 0 ? Double(remaining) / backendRate : .infinity
        let cable = Double(total) / cableRate
        let bluetooth = Double(total) / bluetoothRate
        return [
            RouteOption(kind: .backend, eta: backend, recommended: false),
            RouteOption(kind: .cable, eta: cable, recommended: true),
            RouteOption(kind: .bluetooth, eta: bluetooth, recommended: bluetooth <= bluetoothRecommendedETA),
        ]
    }
}

/// Measures the upload throughput from the progress callbacks and stops a slow upload.
///
/// The first callback is the baseline (parts the server already had); the decision needs at least one part
/// sent in this session. Pass it as the `progress` closure of `BackendClient.uploadPass`.
public final class ThroughputGate: @unchecked Sendable {
    let thresholds: RoutingThresholds
    let clock: @Sendable () -> TimeInterval
    let report: @Sendable (Int64, Int64) -> Void
    private let lock = NSLock()
    private var start: (time: TimeInterval, done: Int64)?

    public init(
        thresholds: RoutingThresholds,
        clock: @escaping @Sendable () -> TimeInterval = { ProcessInfo.processInfo.systemUptime },
        report: @escaping @Sendable (Int64, Int64) -> Void = { _, _ in }
    ) {
        self.thresholds = thresholds
        self.clock = clock
        self.report = report
    }

    public func check(done: Int64, total: Int64) throws {
        report(done, total)
        lock.lock()
        defer { lock.unlock() }
        let now = clock()
        guard let start else {
            self.start = (now, done)
            return
        }
        let sent = done - start.done
        let elapsed = now - start.time
        guard sent > 0, elapsed > 0, done < total else { return }
        let rate = Double(sent) / elapsed
        let eta = Double(total - done) / rate
        if eta > thresholds.maxETA || rate < thresholds.minRate {
            throw SlowBackend(rate: rate, eta: eta, done: done, total: total)
        }
    }
}
