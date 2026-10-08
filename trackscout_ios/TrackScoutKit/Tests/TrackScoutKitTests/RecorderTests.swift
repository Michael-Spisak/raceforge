import Foundation
import XCTest

@testable import TrackScoutKit

final class RecorderTests: XCTestCase {
    var tmp: URL!

    override func setUpWithError() throws {
        tmp = FileManager.default.temporaryDirectory.appendingPathComponent("tsk-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: tmp) }

    func recorder(_ quality: Quality = .high) throws -> PassRecorder {
        try PassRecorder(
            dir: tmp.appendingPathComponent("pass"), project: .init(id: "p", name: "P"),
            pass: .init(id: "x", type: .detail, conditions: .init()), quality: quality, depthSize: (2, 2),
            app: .init(version: "0"), device: .init(model: "m", system: "s"))
    }

    func frame(_ r: PassRecorder, _ t: Double) throws -> Bool {
        try r.append(
            t: t, pose: Synthetic.pose(0), intrinsics: Synthetic.intrinsics, exposureS: 0, tracking: .normal,
            depth: [1, 2, 3, 4], confidence: [2, 2, 2, 2])
    }

    /// Regression: "Maximum" (4K video) made ARKit deliver another depth resolution than assumed → crash.
    func testDepthResolutionComesFromTheFrames() throws {
        let r = try recorder(.maximum)
        try r.start(at: 0)
        let big = (width: 3, height: 2)
        XCTAssertTrue(
            try r.append(
                t: 0.1, pose: Synthetic.pose(0), intrinsics: Synthetic.intrinsics, exposureS: 0, tracking: .normal,
                depth: [1, 2, 3, 4, 5, 6], confidence: [2, 2, 2, 2, 2, 2], depthSize: big))
        XCTAssertEqual(r.manifest.depth.width, 3)
        XCTAssertEqual(r.manifest.depth.height, 2)
        // a frame with another size mid-pass keeps its pose but loses its depth — no crash
        XCTAssertFalse(try frame(r, 0.2))
        XCTAssertEqual(r.depthFramesSkipped, 1)
        XCTAssertEqual(r.depthFramesWritten, 1)
        XCTAssertEqual(r.framesWritten, 2)
    }

    func testStateMachine() throws {
        let r = try recorder()
        XCTAssertThrowsError(try r.pause(at: 0))
        XCTAssertThrowsError(try frame(r, 0))
        try r.start(at: 0)
        XCTAssertEqual(r.state, .recording(segment: 0))
        XCTAssertThrowsError(try r.start(at: 0))
        _ = try frame(r, 0.1)
        try r.pause(at: 0.5)
        XCTAssertEqual(r.state, .paused)
        XCTAssertThrowsError(try frame(r, 0.6))  // nothing is recorded while paused
        try r.resume(at: 1.0)
        XCTAssertEqual(r.state, .recording(segment: 1))
        _ = try frame(r, 1.1)
        let m = try r.finish(at: 1.2)
        XCTAssertEqual(r.state, .finished)
        XCTAssertEqual(m.segments.map(\.frames), [1, 1])
        XCTAssertEqual(m.segments.map(\.startS), [0, 1.0])
        XCTAssertEqual(m.segments[0].endS, 0.5)
        XCTAssertThrowsError(try r.resume(at: 2))
    }

    func testDiscardSpansSegments() throws {
        let r = try recorder()
        try r.start(at: 0)
        try r.pause(at: 10)
        try r.resume(at: 20)
        try r.discardLast(seconds: 15, now: 25)  // reaches back into segment 0
        XCTAssertEqual(r.manifest.segments[0].discarded, [])  // segment 0 ended before the range
        XCTAssertEqual(r.manifest.segments[1].discarded, [[20, 25]])
        try r.pause(at: 30)
        try r.discardLast(seconds: 22, now: 99)  // paused: counts back from the end of the last segment
        XCTAssertEqual(r.manifest.segments[0].discarded, [[8, 10]])
        XCTAssertEqual(r.manifest.segments[1].discarded, [[20, 25], [20, 30]])
    }

    func testDepthEveryNthFrame() throws {
        let r = try recorder(.economy)
        try r.start(at: 0)
        let stored = try (0..<5).map { try frame(r, Double($0)) }
        XCTAssertEqual(stored, [true, false, true, false, true])
    }

    func testStorageEstimate() {
        XCTAssertEqual(Quality.high.minutesRemaining(freeBytes: 500_000_000), 0)
        XCTAssertEqual(Quality.high.minutesRemaining(freeBytes: 11_000_000_000), 8)
        XCTAssertGreaterThan(
            Quality.economy.minutesRemaining(freeBytes: 50_000_000_000),
            Quality.maximum.minutesRemaining(freeBytes: 50_000_000_000))
    }

    func testSyntheticArchive() throws {
        let archive = tmp.appendingPathComponent("s.tscan")
        let m = try Synthetic.makePass(in: tmp.appendingPathComponent("w"), archive: archive)
        XCTAssertEqual(m.segments.count, 2)
        let d = try XCTUnwrap(m.segments[1].discarded.first)
        XCTAssertEqual(d[0], 2.6, accuracy: 1e-9)
        XCTAssertEqual(d[1], 2.9, accuracy: 1e-9)
        XCTAssertNotNil(m.files["frames.bin"])
        XCTAssertEqual(m.files["frames.bin"]?.size, Int64(20 * FrameRecord.byteSize))
        let bytes = try Data(contentsOf: archive)
        XCTAssertEqual(bytes.prefix(4), Data([0x50, 0x4B, 0x03, 0x04]))
        // The manifest round-trips through JSON.
        let decoded = try ManifestCoding.decoder.decode(Manifest.self, from: try ManifestCoding.encoder.encode(m))
        XCTAssertEqual(decoded, m)
    }
}

final class FormatTests: XCTestCase {
    func testFloat16() {
        XCTAssertEqual(float16Bits(1.0), 0x3C00)
        XCTAssertEqual(float16Bits(-2.0), 0xC000)
        XCTAssertEqual(float16Bits(0), 0)
        XCTAssertEqual(float16Bits(65504), 0x7BFF)
        XCTAssertEqual(float16Bits(1e6), 0x7C00)
        XCTAssertEqual(float16Bits(1.5), 0x3E00)
    }

    func testDepthCodecRoundTrip() throws {
        let depth: [UInt16] = (0..<192).map { UInt16($0 * 3) }
        let conf: [UInt8] = (0..<192).map { UInt8($0 % 3) }
        let record = DepthCodec.encode(depth: depth, confidence: conf)
        let n = record.prefix(4).withUnsafeBytes { $0.loadUnaligned(as: UInt32.self) }
        XCTAssertEqual(Int(n), record.count - 4)
        let raw = try XCTUnwrap(DepthCodec.inflate(record.dropFirst(4), expectedSize: 192 * 3))
        XCTAssertEqual(raw[raw.startIndex + 2], 3)
        XCTAssertEqual(raw.suffix(192), Data(conf))
    }

    func testPLYRoundTrip() {
        let mesh = PLY.Mesh(vertices: [[0, 0, 0], [1, 0, 0], [0, 1, 2.5]], faces: [[0, 1, 2]], classification: [7])
        XCTAssertEqual(PLY.decode(PLY.encode(vertices: mesh.vertices, faces: mesh.faces, classification: mesh.classification)), mesh)
        XCTAssertNil(PLY.decode(Data("nope".utf8)))
    }

    func testCRC32() {
        var c = CRC32()
        c.update(Data("123456789".utf8))
        XCTAssertEqual(c.checksum, 0xCBF4_3926)
    }

    func testFrameRecordSize() {
        var w = LEWriter()
        FrameRecord(t: 1, segment: 2, tracking: .limited, hasDepth: true, pose: Synthetic.pose(1),
                    intrinsics: Synthetic.intrinsics, exposureS: 0.5).encode(into: &w)
        XCTAssertEqual(w.data.count, FrameRecord.byteSize)
    }

    func testPairing() throws {
        let p = Pairing(server: URL(string: "https://rf.example.org")!, token: "rft_abc", workspaceId: "ws",
                        laptopName: "MacBook", laptopKey: "k")
        XCTAssertEqual(try Pairing.parse(try p.url()), p)
        XCTAssertThrowsError(try Pairing.parse("https://example.org"))
        XCTAssertThrowsError(try Pairing.parse("raceforge://pair?v=2&d=x"))
        XCTAssertThrowsError(try Pairing.parse("raceforge://pair?v=1&d=bm9wZQ"))
    }

    func testSlug() {
        XCTAssertEqual(captureSlug(projectName: "Gang 2. Stock – Süd"), "scan-gang-2-stock-sud")
        XCTAssertEqual(captureSlug(projectName: "!!!"), "scan-track")
        XCTAssertLessThanOrEqual(captureSlug(projectName: String(repeating: "a", count: 100)).count, 63)
    }
}
