import Foundation

/// Content of the "Pair TrackScout" QR code shown by the desktop app (spec 0007 scope 8):
/// `raceforge://pair?v=1&d=<base64url(JSON)>`.
public struct Pairing: Codable, Equatable, Sendable {
    public var server: URL
    public var token: String
    public var workspaceId: String?
    public var laptopName: String
    public var laptopKey: String

    enum CodingKeys: String, CodingKey {
        case server, token
        case workspaceId = "workspace_id", laptopName = "laptop_name", laptopKey = "laptop_key"
    }

    public init(server: URL, token: String, workspaceId: String?, laptopName: String, laptopKey: String) {
        self.server = server
        self.token = token
        self.workspaceId = workspaceId
        self.laptopName = laptopName
        self.laptopKey = laptopKey
    }

    public enum ParseError: Error, Equatable { case notAPairingCode, unsupportedVersion, invalidPayload }

    public static func parse(_ text: String) throws -> Pairing {
        guard let comps = URLComponents(string: text.trimmingCharacters(in: .whitespacesAndNewlines)),
            comps.scheme == "raceforge", comps.host == "pair"
        else { throw ParseError.notAPairingCode }
        let items = Dictionary((comps.queryItems ?? []).map { ($0.name, $0.value ?? "") }, uniquingKeysWith: { a, _ in a })
        guard items["v"] == "1" else { throw ParseError.unsupportedVersion }
        guard let d = items["d"], let json = Data(base64URL: d),
            let p = try? JSONDecoder().decode(Pairing.self, from: json),
            ["http", "https"].contains(p.server.scheme ?? ""), p.token.hasPrefix("rft_")
        else { throw ParseError.invalidPayload }
        return p
    }

    public func url() throws -> String {
        let json = try JSONEncoder().encode(self)
        return "raceforge://pair?v=1&d=\(json.base64URL)"
    }
}

extension Data {
    init?(base64URL: String) {
        var s = base64URL.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        s += String(repeating: "=", count: (4 - s.count % 4) % 4)
        self.init(base64Encoded: s)
    }

    var base64URL: String {
        base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}

/// `scan-<project-name>` as a backend slug (spec 0001: `^[a-z0-9][a-z0-9-]{1,62}$`).
public func captureSlug(projectName: String) -> String {
    let folded = projectName.folding(options: [.diacriticInsensitive, .caseInsensitive], locale: .init(identifier: "en"))
        .lowercased()
    var out = ""
    for ch in folded {
        if ch.isASCII, ch.isLetter || ch.isNumber {
            out.append(ch)
        } else if !out.hasSuffix("-") {
            out.append("-")
        }
    }
    let core = out.trimmingCharacters(in: CharacterSet(charactersIn: "-"))
    return String("scan-\(core.isEmpty ? "track" : core)".prefix(63)).trimmingCharacters(in: CharacterSet(charactersIn: "-"))
}
