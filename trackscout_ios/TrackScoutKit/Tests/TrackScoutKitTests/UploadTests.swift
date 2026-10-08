import Foundation
import XCTest

@testable import TrackScoutKit

/// In-memory stand-in for the spec 0006 backend endpoints TrackScout uses.
final class FakeBackend: HTTPTransport, @unchecked Sendable {
    let lock = NSLock()
    var objects: [String: (id: String, latest: String?)] = [:]  // slug → object
    var versions: [String: [String: Any]] = [:]
    var blobs: Set<String> = []
    var uploads: [String: (sha: String, size: Int, parts: Set<Int>)] = [:]
    var partPuts = 0
    var failPartOnce: Int? = nil
    var requests: [URLRequest] = []
    let partSize = 1000

    func json(_ obj: Any, _ status: Int = 200) -> (Data, HTTPURLResponse) {
        (try! JSONSerialization.data(withJSONObject: obj),
         HTTPURLResponse(url: URL(string: "http://x")!, statusCode: status, httpVersion: nil, headerFields: nil)!)
    }

    func send(_ r: URLRequest) async throws -> (Data, HTTPURLResponse) {
        lock.lock()
        defer { lock.unlock() }
        requests.append(r)
        XCTAssertEqual(r.value(forHTTPHeaderField: "Authorization"), "Bearer rft_test")
        let path = r.url!.path.replacingOccurrences(of: "/api/v1/", with: "")
        let parts = path.split(separator: "/").map(String.init)
        let body = r.httpBody.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] } ?? [:]
        switch (r.httpMethod!, parts.first!) {
        case ("GET", "workspaces") where parts.count == 1:
            return json([["id": "ws1", "name": "Season 1", "created_at": "2026-10-08T00:00:00Z"]])
        case ("GET", "workspaces"):
            let slug = URLComponents(url: r.url!, resolvingAgainstBaseURL: false)!.queryItems!.first!.value!
            guard let o = objects[slug] else { return json([]) }
            return json([["id": o.id, "latest": o.latest.map { ["id": $0, "semver": "1.0.\(versions.count - 1)"] } ?? NSNull()]])
        case ("POST", "workspaces"):
            let slug = body["slug"] as! String
            XCTAssertEqual(body["kind"] as? String, "capture")
            objects[slug] = (id: "obj-\(slug)", latest: nil)
            return json(["id": "obj-\(slug)", "latest": NSNull()], 201)
        case ("POST", "uploads") where parts.count == 1:
            let sha = body["sha256"] as! String
            let size = body["size"] as! Int
            if blobs.contains(sha) {
                return json(["id": NSNull(), "part_size": partSize, "parts": 1, "received": [], "status": "exists"], 201)
            }
            let id = uploads.first { $0.value.sha == sha }?.key ?? "up\(uploads.count)"
            let have = uploads[id]?.parts ?? []
            uploads[id] = (sha, size, have)
            let n = max(1, (size + partSize - 1) / partSize)
            return json(["id": id, "part_size": partSize, "parts": n, "received": have.sorted(), "status": "open"], 201)
        case ("PUT", "uploads"):
            let n = Int(parts[3])!
            if failPartOnce == n {
                failPartOnce = nil
                throw URLError(.networkConnectionLost)
            }
            XCTAssertEqual(r.value(forHTTPHeaderField: "X-Part-SHA256"), Checksum.sha256(r.httpBody!))
            uploads[parts[1]]!.parts.insert(n)
            partPuts += 1
            return json([:])
        case ("POST", "uploads"):
            let up = uploads[parts[1]]!
            blobs.insert(up.sha)
            return json([:])
        case ("POST", "objects"):
            let id = "v\(versions.count)"
            versions[id] = body
            let slug = objects.first { $0.value.id == parts[1] }!.key
            objects[slug]!.latest = id
            return json(["id": id, "semver": "1.0.\(versions.count - 1)"], 201)
        case ("GET", "versions"):
            return json(versions[parts[1]]!)
        default:
            return json(["detail": "unexpected \(r.httpMethod!) \(path)"], 404)
        }
    }
}

final class UploadTests: XCTestCase {
    func file(_ bytes: Int, name: String = "pass-1.tscan") throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString).appendingPathComponent(name)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data((0..<bytes).map { UInt8($0 % 251) }).write(to: url)
        return url
    }

    func testUploadCreatesCaptureVersionsListingAllPasses() async throws {
        let fake = FakeBackend()
        let client = BackendClient(server: URL(string: "https://rf.example.org")!, token: "rft_test", transport: fake)
        let spaces = try await client.workspaces()
        XCTAssertEqual(spaces.map(\.name), ["Season 1"])
        let v1 = try await client.uploadPass(file: try file(2500), projectName: "Gang Süd", workspaceId: "ws1")
        XCTAssertEqual(fake.partPuts, 3)
        let v2 = try await client.uploadPass(file: try file(10, name: "pass-2.tscan"), projectName: "Gang Süd", workspaceId: "ws1")
        XCTAssertEqual(fake.objects.keys.sorted(), ["scan-gang-sud"])
        XCTAssertNotEqual(v1.id, v2.id)
        let files = ((fake.versions[v2.id]!["content"] as! [String: Any])["files"] as! [[String: Any]]).map { $0["path"] as! String }
        XCTAssertEqual(files, ["pass-1.tscan", "pass-2.tscan"])
        XCTAssertFalse(fake.requests.contains { $0.allowsCellularAccess })  // cellular only after asking
    }

    func testDuplicateUploadIsSkipped() async throws {
        let fake = FakeBackend()
        let client = BackendClient(server: URL(string: "https://rf.example.org")!, token: "rft_test", transport: fake)
        let f = try file(1500)
        try await client.uploadPass(file: f, projectName: "A", workspaceId: "ws1")
        let puts = fake.partPuts
        try await client.uploadPass(file: f, projectName: "A", workspaceId: "ws1")
        XCTAssertEqual(fake.partPuts, puts)
    }

    func testResumeAfterConnectionLoss() async throws {
        let fake = FakeBackend()
        fake.failPartOnce = 2
        let client = BackendClient(server: URL(string: "https://rf.example.org")!, token: "rft_test", transport: fake)
        let f = try file(4200)
        do {
            try await client.uploadPass(file: f, projectName: "A", workspaceId: "ws1")
            XCTFail("expected the connection loss")
        } catch {}
        XCTAssertEqual(fake.partPuts, 2)
        try await client.uploadPass(file: f, projectName: "A", workspaceId: "ws1")
        XCTAssertEqual(fake.partPuts, 5)  // parts 0 and 1 were not sent again
    }

    func testCellularFlag() async throws {
        let fake = FakeBackend()
        let client = BackendClient(
            server: URL(string: "https://rf.example.org")!, token: "rft_test", transport: fake, allowCellular: true)
        _ = try await client.workspaces()
        XCTAssertTrue(fake.requests.allSatisfy { $0.allowsCellularAccess })
    }

    func testHttpErrorCarriesDetail() async throws {
        struct Failing: HTTPTransport {
            func send(_ r: URLRequest) async throws -> (Data, HTTPURLResponse) {
                (Data(#"{"detail":"token lacks the 'edit' scope"}"#.utf8),
                 HTTPURLResponse(url: r.url!, statusCode: 403, httpVersion: nil, headerFields: nil)!)
            }
        }
        let client = BackendClient(server: URL(string: "https://x")!, token: "rft_test", transport: Failing())
        do {
            _ = try await client.workspaces()
            XCTFail()
        } catch let e as UploadError {
            XCTAssertEqual(e, .http(status: 403, detail: "token lacks the 'edit' scope"))
        }
    }
}
