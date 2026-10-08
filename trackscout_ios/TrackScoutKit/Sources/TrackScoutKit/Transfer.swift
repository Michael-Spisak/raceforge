import CryptoKit
import Foundation

/// Phone → laptop transfer v1 ("rftx1", spec 0007): the phone side.
///
/// Bluetooth: messages (`u8 type` + body) are split into GATT values (`u8 flags` + payload, bit 0 = last
/// fragment). The laptop must answer the phone's random challenge with an HMAC of the pairing key before the
/// phone offers anything. Cable: the phone lists passes in `TrackScoutTransfer/outbox.json` (see `Outbox`).
public enum RFTX {
    public static let serviceUUID = "7E0F0001-5A1D-4C55-9B7E-52464F524745"
    public static let rxUUID = "7E0F0002-5A1D-4C55-9B7E-52464F524745"  // laptop → phone (write)
    public static let txUUID = "7E0F0003-5A1D-4C55-9B7E-52464F524745"  // phone → laptop (notify)
    public static let maxChunk = 64 * 1024

    public enum Msg: UInt8, Sendable {
        case challenge = 1, auth, offer, get, chunk, done, error
    }

    public static func hmacHex(_ key: String, _ message: Data) -> String {
        let mac = HMAC<SHA256>.authenticationCode(for: message, using: SymmetricKey(data: Data(key.utf8)))
        return mac.map { String(format: "%02x", $0) }.joined()
    }

    /// Pairing tag of an offered pass: only the paired laptop can verify it.
    public static func tag(key: String, id: String, sha256: String) -> String {
        hmacHex(key, Data("rftx1|\(id)|\(sha256)".utf8))
    }

    public static func authMAC(key: String, nonce: Data) -> String {
        hmacHex(key, Data("rftx1-auth|".utf8) + nonce)
    }

    public static func fragments(_ kind: Msg, _ body: Data, maxPayload: Int) -> [Data] {
        let data = Data([kind.rawValue]) + body
        let size = max(1, maxPayload)
        var out: [Data] = []
        var i = data.startIndex
        while i < data.endIndex {
            let end = min(i + size, data.endIndex)
            out.append(Data([end == data.endIndex ? 1 : 0]) + data[i..<end])
            i = end
        }
        return out
    }

    public static func chunkBody(offset: UInt64, data: Data) -> Data {
        var head = Data(count: 12)
        head.withUnsafeMutableBytes { raw in
            raw.storeBytes(of: offset.littleEndian, toByteOffset: 0, as: UInt64.self)
            raw.storeBytes(of: CRC32.checksum(data).littleEndian, toByteOffset: 8, as: UInt32.self)
        }
        return head + data
    }
}

extension CRC32 {
    /// CRC-32 of one buffer (as zlib.crc32) for the chunk checks.
    public static func checksum(_ data: Data) -> UInt32 {
        var c = CRC32()
        c.update(data)
        return c.checksum
    }
}

/// Reassembles GATT fragments into messages.
public struct Reassembler: Sendable {
    var buffer = Data()
    public init() {}

    /// Returns a complete message when `fragment` was the last one.
    public mutating func push(_ fragment: Data) -> (RFTX.Msg, Data)? {
        guard let flags = fragment.first else { return nil }
        buffer += fragment.dropFirst()
        guard flags & 1 == 1 else { return nil }
        defer { buffer = Data() }
        guard let first = buffer.first, let kind = RFTX.Msg(rawValue: first) else { return nil }
        return (kind, Data(buffer.dropFirst()))
    }
}

/// One pass the phone offers (outbox.json entry / Bluetooth OFFER), plus where the file is on the phone.
public struct OfferedPass: Codable, Equatable, Sendable {
    public let id: String
    public let size: Int64
    public let sha256: String
    public let project: String
    public let passType: String
    public let createdAt: String
    public let tag: String
    public var file: String?

    enum CodingKeys: String, CodingKey {
        case id, size, sha256, project, tag, file
        case passType = "pass_type"
        case createdAt = "created_at"
    }

    public init(id: String, file: URL, relativePath: String, project: String, passType: String, createdAt: Date, key: String)
        throws
    {
        let (sha, size) = try Checksum.sha256(file: file)
        self.id = id
        self.size = size
        self.sha256 = sha
        self.project = project
        self.passType = passType
        self.createdAt = ISO8601DateFormatter().string(from: createdAt)
        self.tag = RFTX.tag(key: key, id: id, sha256: sha)
        self.file = relativePath
    }
}

/// The phone side of one Bluetooth connection. Feed every value the laptop writes into `receive`; the session
/// answers through `send` (one GATT notification per call; it should wait while the notify queue is full).
public final class PhoneTransferSession: @unchecked Sendable {
    public enum Event: Equatable, Sendable {
        case authenticated(laptop: String)
        case rejected
        case progress(id: String, sent: Int64, total: Int64)
        case delivered(id: String)
    }

    let key: String
    let passes: [String: (offer: OfferedPass, url: URL)]
    let order: [String]
    let maxPayload: Int
    let chunkSize: Int
    let nonce: Data
    let send: @Sendable (Data) async throws -> Void
    let events: @Sendable (Event) -> Void
    var reassembler = Reassembler()
    public private(set) var authenticated = false

    public init(
        key: String, passes: [(offer: OfferedPass, url: URL)], maxPayload: Int, chunkSize: Int = RFTX.maxChunk,
        nonce: Data? = nil, send: @escaping @Sendable (Data) async throws -> Void,
        events: @escaping @Sendable (Event) -> Void = { _ in }
    ) {
        self.key = key
        self.passes = Dictionary(uniqueKeysWithValues: passes.map { ($0.offer.id, $0) })
        self.order = passes.map(\.offer.id)
        self.maxPayload = maxPayload
        self.chunkSize = chunkSize
        self.nonce = nonce ?? Data((0..<16).map { _ in UInt8.random(in: 0...255) })
        self.send = send
        self.events = events
    }

    func message(_ kind: RFTX.Msg, _ body: Data) async throws {
        for f in RFTX.fragments(kind, body, maxPayload: maxPayload) { try await send(f) }
    }

    func json(_ kind: RFTX.Msg, _ obj: [String: Any]) async throws {
        try await message(kind, try JSONSerialization.data(withJSONObject: obj))
    }

    /// Call when the laptop subscribed to the notify characteristic.
    public func start() async throws { try await message(.challenge, nonce) }

    public func receive(_ fragment: Data) async throws {
        guard let (kind, body) = reassembler.push(fragment) else { return }
        let obj = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any] ?? [:]
        switch kind {
        case .auth:
            let mac = obj["mac"] as? String ?? ""
            guard mac == RFTX.authMAC(key: key, nonce: nonce) else {
                events(.rejected)
                try await json(.offer, ["error": "unpaired"])
                return
            }
            authenticated = true
            events(.authenticated(laptop: obj["laptop"] as? String ?? "laptop"))
            var entries: [[String: Any]] = []
            for id in order {
                var offer = passes[id]!.offer
                offer.file = nil
                entries.append(try JSONSerialization.jsonObject(with: JSONEncoder().encode(offer)) as! [String: Any])
            }
            try await json(.offer, ["passes": entries])
        case .get where authenticated:
            guard let id = obj["id"] as? String, let pass = passes[id], let offset = (obj["offset"] as? NSNumber)?.int64Value else {
                try await json(.error, ["error": "unknown pass"])
                return
            }
            try await stream(pass.offer, pass.url, from: offset)
        case .done where authenticated:
            if let id = obj["id"] as? String, passes[id]?.offer.sha256 == obj["sha256"] as? String {
                events(.delivered(id: id))
            }
        default:
            try await json(.error, ["error": "unexpected message \(kind)"])
        }
    }

    func stream(_ offer: OfferedPass, _ url: URL, from start: Int64) async throws {
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        try handle.seek(toOffset: UInt64(start))
        var offset = start
        while offset < offer.size {
            let data = try handle.read(upToCount: chunkSize) ?? Data()
            if data.isEmpty { break }
            try await message(.chunk, RFTX.chunkBody(offset: UInt64(offset), data: data))
            offset += Int64(data.count)
            events(.progress(id: offer.id, sent: offset, total: offer.size))
        }
    }
}

/// Cable transfer: `TrackScoutTransfer/outbox.json` lists what the paired laptop may copy, and the laptop writes
/// its progress to `TrackScoutTransfer/status.json` (both in the app's shared Documents folder).
public struct Outbox: Sendable {
    public struct Status: Equatable, Sendable {
        public let received: Int64
        public let size: Int64
        public let done: Bool
    }

    let folder: URL
    public init(documents: URL) { folder = documents.appendingPathComponent("TrackScoutTransfer") }

    var outboxURL: URL { folder.appendingPathComponent("outbox.json") }
    var statusURL: URL { folder.appendingPathComponent("status.json") }

    struct File: Codable {
        var v = 1
        var phone: String
        var passes: [OfferedPass]
    }

    public func entries() -> [OfferedPass] {
        guard let data = try? Data(contentsOf: outboxURL), let f = try? JSONDecoder().decode(File.self, from: data)
        else { return [] }
        return f.passes
    }

    public func add(_ offer: OfferedPass, phone: String) throws {
        var list = entries().filter { $0.id != offer.id }
        list.append(offer)
        try write(list, phone: phone)
    }

    public func remove(id: String, phone: String) throws {
        try write(entries().filter { $0.id != id }, phone: phone)
    }

    func write(_ list: [OfferedPass], phone: String) throws {
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let enc = JSONEncoder()
        enc.outputFormatting = [.sortedKeys]
        try enc.encode(File(phone: phone, passes: list)).write(to: outboxURL, options: .atomic)
    }

    /// The laptop's progress per pass id (empty while no laptop has connected).
    public func status() -> [String: Status] {
        guard let data = try? Data(contentsOf: statusURL),
            let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let passes = obj["passes"] as? [String: [String: Any]]
        else { return [:] }
        var out: [String: Status] = [:]
        for (id, s) in passes {
            out[id] = Status(
                received: (s["received"] as? NSNumber)?.int64Value ?? 0, size: (s["size"] as? NSNumber)?.int64Value ?? 0,
                done: s["done"] as? Bool ?? false)
        }
        return out
    }
}
