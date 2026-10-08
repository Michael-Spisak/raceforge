import Foundation

/// `.tscan` v1 manifest (spec 0007, human-owned contract). JSON keys are snake_case.
public struct Manifest: Codable, Equatable, Sendable {
    public var schema = "tscan"
    public var schemaVersion = 1
    public var app: AppInfo
    public var device: DeviceInfo
    public var project: ProjectInfo
    public var pass: PassInfo
    public var quality: Quality
    public var createdAt: Date
    public var coordinateFrame = "arkit"
    public var worldMap: WorldMapInfo
    public var depth: DepthInfo
    public var segments: [Segment]
    public var roomplan: Bool
    /// sha256 + size of every other file in the archive, by archive path.
    public var files: [String: FileInfo]

    enum CodingKeys: String, CodingKey {
        case schema, app, device, project, pass, quality, segments, roomplan, files, depth
        case schemaVersion = "schema_version", createdAt = "created_at"
        case coordinateFrame = "coordinate_frame", worldMap = "world_map"
    }

    public struct AppInfo: Codable, Equatable, Sendable {
        public var name = "TrackScout"
        public var version: String
        public init(version: String) { self.version = version }
    }

    public struct DeviceInfo: Codable, Equatable, Sendable {
        public var model: String
        public var system: String
        public init(model: String, system: String) {
            self.model = model
            self.system = system
        }
    }

    public struct ProjectInfo: Codable, Equatable, Sendable {
        public var id: String
        public var name: String
        public init(id: String, name: String) {
            self.id = id
            self.name = name
        }
    }

    public struct PassInfo: Codable, Equatable, Sendable {
        public var id: String
        public var type: PassType
        public var conditions: Conditions
        public init(id: String, type: PassType, conditions: Conditions) {
            self.id = id
            self.type = type
            self.conditions = conditions
        }
    }

    public struct Conditions: Codable, Equatable, Sendable {
        public var lights: String  // on | off | mixed
        public var doors: String  // open | closed | mixed
        public var note: String
        public init(lights: String = "on", doors: String = "closed", note: String = "") {
            self.lights = lights
            self.doors = doors
            self.note = note
        }
    }

    public struct WorldMapInfo: Codable, Equatable, Sendable {
        public var id: String?
        public var included: Bool
        public var aligned: Bool
        public var alignedAtS: Double?
        enum CodingKeys: String, CodingKey {
            case id, included, aligned
            case alignedAtS = "aligned_at_s"
        }
        public init(id: String?, included: Bool, aligned: Bool, alignedAtS: Double?) {
            self.id = id
            self.included = included
            self.aligned = aligned
            self.alignedAtS = alignedAtS
        }
    }

    public struct DepthInfo: Codable, Equatable, Sendable {
        public var width: Int
        public var height: Int
        public var everyNthFrame: Int
        enum CodingKeys: String, CodingKey {
            case width, height
            case everyNthFrame = "every_nth_frame"
        }
        public init(width: Int, height: Int, everyNthFrame: Int) {
            self.width = width
            self.height = height
            self.everyNthFrame = everyNthFrame
        }
    }

    public struct Segment: Codable, Equatable, Sendable {
        public var index: Int
        public var startS: Double
        public var endS: Double
        public var frames: Int
        public var video: String?
        public var depth: String
        public var mesh: String?
        public var discarded: [[Double]]
        enum CodingKeys: String, CodingKey {
            case index, frames, video, depth, mesh, discarded
            case startS = "start_s", endS = "end_s"
        }
    }

    public struct FileInfo: Codable, Equatable, Sendable {
        public var sha256: String
        public var size: Int64
    }
}

public enum PassType: String, Codable, CaseIterable, Sendable {
    case walkthrough, high, low, detail
    case gapFill = "gap_fill"
}

public enum Quality: String, Codable, CaseIterable, Sendable {
    case maximum, high, economy

    /// Depth is stored for every n-th camera frame.
    public var depthEveryNthFrame: Int { self == .economy ? 2 : 1 }

    /// Planned camera frame rate.
    public var frameRate: Int { self == .economy ? 15 : 30 }

    /// Rough bytes per minute (RGB video + depth + poses + mesh), used for the storage estimate.
    public var bytesPerMinute: Int64 {
        switch self {
        case .maximum: return 3_500_000_000
        case .high: return 1_250_000_000
        case .economy: return 300_000_000
        }
    }

    /// Minutes of recording that fit into `freeBytes`, keeping 1 GB free for the system.
    public func minutesRemaining(freeBytes: Int64) -> Int {
        let usable = max(0, freeBytes - 1_000_000_000)
        return Int(usable / bytesPerMinute)
    }
}

/// JSON coding for manifests: explicit snake_case keys (no key strategy, which would also rewrite the
/// file paths used as dictionary keys), ISO-8601 dates, sorted keys.
public enum ManifestCoding {
    public static let encoder: JSONEncoder = {
        let e = JSONEncoder()
        e.dateEncodingStrategy = .iso8601
        e.outputFormatting = [.sortedKeys, .prettyPrinted]
        return e
    }()

    public static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.dateDecodingStrategy = .iso8601
        return d
    }()
}
