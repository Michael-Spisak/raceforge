import Foundation

/// HTTP seam so the upload logic can be tested without a network (AC1).
public protocol HTTPTransport: Sendable {
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse)
}

public struct URLSessionTransport: HTTPTransport {
    let session: URLSession
    public init(session: URLSession = .shared) { self.session = session }

    public func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw UploadError.badResponse }
        return (data, http)
    }
}

public enum UploadError: Error, Equatable {
    case http(status: Int, detail: String)
    case badResponse
    case cellularNotAllowed
}

/// Talks to the team backend (spec 0006 API) with a TrackScout API token.
public final class BackendClient: Sendable {
    public let server: URL
    let token: String
    let transport: HTTPTransport
    /// Cellular data is only used after the user agreed (spec 0007 scope 8).
    public let allowCellular: Bool

    public init(server: URL, token: String, transport: HTTPTransport = URLSessionTransport(), allowCellular: Bool = false) {
        self.server = server
        self.token = token
        self.transport = transport
        self.allowCellular = allowCellular
    }

    public struct Workspace: Codable, Equatable, Sendable {
        public let id: String
        public let name: String
    }

    struct ObjectInfo: Codable {
        let id: String
        let latest: VersionInfo?
    }

    public struct VersionInfo: Codable, Equatable, Sendable {
        public let id: String
        public let semver: String
    }

    struct UploadInfo: Codable {
        let id: String?
        let partSize: Int
        let parts: Int
        let received: [Int]
        let status: String
        enum CodingKeys: String, CodingKey {
            case id, parts, received, status
            case partSize = "part_size"
        }
    }

    struct FileEntry: Codable, Equatable {
        let path: String
        let sha256: String
        let size: Int64
    }

    struct FileSet: Codable {
        var schema = "fileset"
        var schemaVersion = 1
        var files: [FileEntry]
        var entry: String?
        enum CodingKeys: String, CodingKey {
            case schema, files, entry
            case schemaVersion = "schema_version"
        }
    }

    struct VersionContent: Codable { let content: FileSet }

    func request(
        _ method: String, _ path: String, query: [URLQueryItem] = [], json: Data? = nil, body: Data? = nil,
        headers: [String: String] = [:]
    ) async throws -> Data {
        var comps = URLComponents(
            url: server.appendingPathComponent("api/v1").appendingPathComponent(path), resolvingAgainstBaseURL: false)!
        if !query.isEmpty { comps.queryItems = query }
        var req = URLRequest(url: comps.url!)
        req.httpMethod = method
        req.allowsCellularAccess = allowCellular
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        if let json {
            req.httpBody = json
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        } else if let body {
            req.httpBody = body
            req.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
        }
        headers.forEach { req.setValue($1, forHTTPHeaderField: $0) }
        let (data, resp) = try await transport.send(req)
        guard (200..<300).contains(resp.statusCode) else {
            let detail = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["detail"] as? String
            throw UploadError.http(status: resp.statusCode, detail: detail ?? String(decoding: data, as: UTF8.self))
        }
        return data
    }

    static func encode<T: Encodable>(_ v: T) throws -> Data { try JSONEncoder().encode(v) }

    public func workspaces() async throws -> [Workspace] {
        try JSONDecoder().decode([Workspace].self, from: try await request("GET", "workspaces"))
    }

    /// Uploads one `.tscan` pass and records it as a new version of the project's `capture` object:
    /// the version lists every pass uploaded so far (unchanged passes are deduplicated blobs).
    @discardableResult
    public func uploadPass(
        file: URL, projectName: String, workspaceId: String,
        progress: @Sendable (_ sent: Int64, _ total: Int64) throws -> Void = { _, _ in }
    ) async throws -> VersionInfo {
        let (sha, size) = try Checksum.sha256(file: file)
        let slug = captureSlug(projectName: projectName)
        var object = try await ensureObject(workspaceId: workspaceId, slug: slug)
        try await uploadBlob(file: file, sha256: sha, size: size, progress: progress)
        let name = file.lastPathComponent
        let mine = FileEntry(path: name, sha256: sha, size: size)
        struct Body: Encodable {
            let content: FileSet
            let message: String
        }
        // Read-modify-write of the pass list: another device may post a version at the same time.
        // After posting, check that the latest version still lists this pass, else merge again.
        var posted: VersionInfo?
        for _ in 0..<3 {
            if let posted, object.latest?.id == posted.id { return posted }
            var files = try await latestFiles(object)
            if let posted, files.contains(mine) { return posted }
            files.removeAll { $0.path == name }
            files.append(mine)
            let body = Body(content: FileSet(files: files, entry: nil), message: "TrackScout pass \(name)")
            let data = try await request("POST", "objects/\(object.id)/versions", json: try Self.encode(body))
            posted = try JSONDecoder().decode(VersionInfo.self, from: data)
            object = try await ensureObject(workspaceId: workspaceId, slug: slug)
        }
        guard let posted else { throw UploadError.badResponse }
        return posted
    }

    func latestFiles(_ object: ObjectInfo) async throws -> [FileEntry] {
        guard let latest = object.latest else { return [] }
        let v = try JSONDecoder().decode(VersionContent.self, from: try await request("GET", "versions/\(latest.id)"))
        return v.content.files
    }

    func ensureObject(workspaceId: String, slug: String) async throws -> ObjectInfo {
        let path = "workspaces/\(workspaceId)/objects"
        let found = try JSONDecoder().decode(
            [ObjectInfo].self, from: try await request("GET", path, query: [URLQueryItem(name: "slug", value: slug)]))
        if let obj = found.first { return obj }
        let body = try JSONSerialization.data(withJSONObject: ["kind": "capture", "slug": slug, "tags": ["trackscout"]])
        return try JSONDecoder().decode(ObjectInfo.self, from: try await request("POST", path, json: body))
    }

    /// Chunked, resumable upload (spec 0006): parts the server already has are skipped. `progress` may throw
    /// (e.g. `ThroughputGate` on a slow backend) to stop; finished parts stay on the server for the resume.
    public func uploadBlob(
        file: URL, sha256: String, size: Int64, progress: @Sendable (Int64, Int64) throws -> Void = { _, _ in }
    ) async throws {
        let created = try await request(
            "POST", "uploads", json: try JSONSerialization.data(withJSONObject: ["sha256": sha256, "size": size]))
        let info = try JSONDecoder().decode(UploadInfo.self, from: created)
        if info.status == "exists" {
            try progress(size, size)
            return
        }
        guard let id = info.id else { throw UploadError.badResponse }
        let handle = try FileHandle(forReadingFrom: file)
        defer { try? handle.close() }
        var sent = Int64(info.received.count) * Int64(info.partSize)
        try progress(min(sent, size), size)
        for n in 0..<info.parts where !info.received.contains(n) {
            try handle.seek(toOffset: UInt64(n) * UInt64(info.partSize))
            let part = try handle.read(upToCount: info.partSize) ?? Data()
            _ = try await request(
                "PUT", "uploads/\(id)/parts/\(n)", body: part, headers: ["X-Part-SHA256": Checksum.sha256(part)])
            sent += Int64(part.count)
            try progress(min(sent, size), size)
        }
        _ = try await request("POST", "uploads/\(id)/complete")
    }
}
