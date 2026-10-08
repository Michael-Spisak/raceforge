import Foundation
import XCTest

@testable import TrackScoutKit

private func hex(_ d: Data) -> String { d.map { String(format: "%02x", $0) }.joined() }

/// A minimal laptop in Swift (the real one is Python, `raceforge.capture.rftx.pull`; CI also runs that one
/// against `rftx-phone`). Collects what the phone sends over an in-memory link.
actor TestLaptop {
    var reassembler = Reassembler()
    var messages: [(RFTX.Msg, Data)] = []
    func push(_ fragment: Data) {
        if let m = reassembler.push(fragment) { messages.append(m) }
    }
    func take() -> [(RFTX.Msg, Data)] {
        defer { messages = [] }
        return messages
    }
}

final class Events: @unchecked Sendable {
    let lock = NSLock()
    var list: [PhoneTransferSession.Event] = []
    func add(_ e: PhoneTransferSession.Event) {
        lock.lock()
        list.append(e)
        lock.unlock()
    }
}

final class TransferTests: XCTestCase {
    let key = "laptop-key"

    func testGoldenVectorsMatchThePythonLaptop() {
        XCTAssertEqual(
            RFTX.tag(key: key, id: "pass-1", sha256: String(repeating: "ab", count: 32)),
            "16795163caf2e21c6d5be4d1487b14dfff7aee664c63509650081ee2c25a6e29")
        XCTAssertEqual(
            RFTX.authMAC(key: key, nonce: Data(0..<16)),
            "2ba1a772190b13db2cae5acb3268c63eb426e9060c6e97e2483a045c82f0876f")
        XCTAssertEqual(
            RFTX.fragments(.get, Data(#"{"id":1}"#.utf8), maxPayload: 4).map(hex), ["00047b2269", "0064223a31", "017d"])
        XCTAssertEqual(hex(RFTX.chunkBody(offset: 5, data: Data("hello".utf8))), "050000000000000086a6103668656c6c6f")
        XCTAssertEqual(CRC32.checksum(Data("123456789".utf8)), 0xCBF4_3926)
    }

    func file(_ bytes: Int, id: String) throws -> (offer: OfferedPass, url: URL) {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let url = dir.appendingPathComponent("\(id).tscan")
        try Data((0..<bytes).map { UInt8(($0 * 7) % 251) }).write(to: url)
        let offer = try OfferedPass(
            id: id, file: url, relativePath: "Projects/x/\(id).tscan", project: "Gang", passType: "low",
            createdAt: Date(timeIntervalSince1970: 0), key: key)
        return (offer, url)
    }

    func json(_ obj: [String: Any]) -> Data { try! JSONSerialization.data(withJSONObject: obj) }

    func session(_ passes: [(offer: OfferedPass, url: URL)], laptop: TestLaptop, events: Events) -> PhoneTransferSession {
        PhoneTransferSession(
            key: key, passes: passes, maxPayload: 50, chunkSize: 1000, nonce: Data(0..<16),
            send: { f in
                XCTAssertLessThanOrEqual(f.count, 51)
                await laptop.push(f)
            }, events: { events.add($0) })
    }

    func send(_ s: PhoneTransferSession, _ kind: RFTX.Msg, _ body: Data) async throws {
        for f in RFTX.fragments(kind, body, maxPayload: 20) { try await s.receive(f) }
    }

    func testFullTransferWithResume() async throws {
        let pass = try file(2500, id: "p1")
        let laptop = TestLaptop()
        let events = Events()
        let s = session([pass], laptop: laptop, events: events)
        try await s.start()
        var msgs = await laptop.take()
        XCTAssertEqual(msgs.first?.0, .challenge)
        XCTAssertEqual(msgs.first?.1, Data(0..<16))

        try await send(s, .auth, json(["laptop": "mac", "mac": RFTX.authMAC(key: key, nonce: Data(0..<16))]))
        msgs = await laptop.take()
        XCTAssertEqual(msgs.first?.0, .offer)
        let offer = try JSONSerialization.jsonObject(with: msgs[0].1) as! [String: Any]
        let entry = (offer["passes"] as! [[String: Any]])[0]
        XCTAssertEqual(entry["id"] as? String, "p1")
        XCTAssertNil(entry["file"])  // the phone's file layout stays private on Bluetooth
        XCTAssertEqual(entry["tag"] as? String, pass.offer.tag)

        // resume: the laptop already has 1000 bytes
        try await send(s, .get, json(["id": "p1", "offset": 1000]))
        msgs = await laptop.take()
        XCTAssertEqual(msgs.map(\.0), [.chunk, .chunk])
        var received = Data()
        for (_, body) in msgs {
            let offset = body.prefix(8).withUnsafeBytes { $0.loadUnaligned(as: UInt64.self) }
            let crc = body.dropFirst(8).prefix(4).withUnsafeBytes { $0.loadUnaligned(as: UInt32.self) }
            let data = Data(body.dropFirst(12))
            XCTAssertEqual(Int(offset), 1000 + received.count)
            XCTAssertEqual(crc, CRC32.checksum(data))
            received += data
        }
        XCTAssertEqual(received, try Data(contentsOf: pass.url).dropFirst(1000))

        try await send(s, .done, json(["id": "p1", "sha256": pass.offer.sha256]))
        XCTAssertEqual(events.list.first, .authenticated(laptop: "mac"))
        XCTAssertEqual(events.list.last, .delivered(id: "p1"))
    }

    func testUnpairedLaptopIsRejected() async throws {
        let laptop = TestLaptop()
        let events = Events()
        let s = session([try file(100, id: "p1")], laptop: laptop, events: events)
        try await s.start()
        _ = await laptop.take()
        try await send(s, .auth, json(["laptop": "evil", "mac": RFTX.authMAC(key: "other", nonce: Data(0..<16))]))
        let msgs = await laptop.take()
        let body = try JSONSerialization.jsonObject(with: msgs[0].1) as! [String: Any]
        XCTAssertEqual(body["error"] as? String, "unpaired")
        XCTAssertEqual(events.list, [.rejected])
        // nothing is served without authentication
        try await send(s, .get, json(["id": "p1", "offset": 0]))
        let after = await laptop.take()
        XCTAssertEqual(after.map(\.0), [.error])
    }

    func testOutboxAndStatus() throws {
        let docs = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let outbox = Outbox(documents: docs)
        let a = try file(10, id: "a")
        let b = try file(20, id: "b")
        try outbox.add(a.offer, phone: "iPhone")
        try outbox.add(b.offer, phone: "iPhone")
        try outbox.add(a.offer, phone: "iPhone")  // no duplicates
        XCTAssertEqual(outbox.entries().map(\.id), ["b", "a"])
        XCTAssertEqual(outbox.entries()[0].file, "Projects/x/b.tscan")
        try outbox.remove(id: "b", phone: "iPhone")
        XCTAssertEqual(outbox.entries().map(\.id), ["a"])
        XCTAssertEqual(outbox.status(), [:])
        let status = #"{"v": 1, "laptop": "mac", "passes": {"a": {"received": 10, "size": 10, "done": true}}}"#
        try Data(status.utf8).write(to: docs.appendingPathComponent("TrackScoutTransfer/status.json"))
        XCTAssertEqual(outbox.status()["a"], Outbox.Status(received: 10, size: 10, done: true))
    }
}

/// Spec 0007 AC7: a throttled backend → the choice with ETAs; switching back resumes without resending parts.
final class RoutingTests: XCTestCase {
    final class Ticker: @unchecked Sendable {
        var t = 0.0
        let step: Double
        init(step: Double) { self.step = step }
        func now() -> Double {
            t += step
            return t
        }
    }

    func file(_ bytes: Int) throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".tscan")
        try Data((0..<bytes).map { UInt8($0 % 251) }).write(to: url)
        return url
    }

    func testFastBackendUploadsDirectly() async throws {
        let fake = FakeBackend()
        let client = BackendClient(server: URL(string: "https://x")!, token: "rft_test", transport: fake)
        let ticker = Ticker(step: 0.0001)  // 1000-byte parts in 0.1 ms: 10 MB/s
        let gate = ThroughputGate(thresholds: RoutingThresholds(), clock: { ticker.now() })
        try await client.uploadPass(file: try file(5000), projectName: "A", workspaceId: "ws1") {
            try gate.check(done: $0, total: $1)
        }
        XCTAssertEqual(fake.partPuts, 5)
    }

    func testSlowBackendOffersTheChoiceAndResumes() async throws {
        let fake = FakeBackend()
        let client = BackendClient(server: URL(string: "https://x")!, token: "rft_test", transport: fake)
        let ticker = Ticker(step: 2)  // 1000 bytes per 2 s: 500 B/s
        let gate = ThroughputGate(thresholds: RoutingThresholds(), clock: { ticker.now() })
        let f = try file(5000)
        do {
            try await client.uploadPass(file: f, projectName: "A", workspaceId: "ws1") {
                try gate.check(done: $0, total: $1)
            }
            XCTFail("expected SlowBackend")
        } catch let slow as SlowBackend {
            XCTAssertEqual(slow.done, 1000)
            XCTAssertEqual(slow.rate, 500, accuracy: 1)
            XCTAssertEqual(slow.eta, 8, accuracy: 0.1)
            let kinds = slow.options.map(\.kind)
            XCTAssertEqual(kinds, [.backend, .cable, .bluetooth])
            XCTAssertEqual(slow.options[1].eta, 5000 / Routing.cableRate, accuracy: 1e-9)
            XCTAssertTrue(slow.options[2].recommended)  // tiny pass: Bluetooth is fine
        }
        XCTAssertEqual(fake.partPuts, 1)
        // the user picks "keep uploading to the backend": no gate, and part 0 is not sent again
        try await client.uploadPass(file: f, projectName: "A", workspaceId: "ws1")
        XCTAssertEqual(fake.partPuts, 5)
    }

    func testLongEtaAloneIsSlow() throws {
        let ticker = Ticker(step: 1)
        let gate = ThroughputGate(thresholds: RoutingThresholds(maxETA: 600, minRate: 0), clock: { ticker.now() })
        try gate.check(done: 0, total: 10_000_000_000)
        XCTAssertThrowsError(try gate.check(done: 5_000_000, total: 10_000_000_000))  // 5 MB/s but ~33 min left
    }

    func testBluetoothNotRecommendedForLargePasses() {
        let options = Routing.options(remaining: 1_000_000_000, total: 2_000_000_000, backendRate: 100_000)
        XCTAssertFalse(options[2].recommended)
        XCTAssertEqual(options[0].eta, 10_000, accuracy: 0.1)
    }
}
