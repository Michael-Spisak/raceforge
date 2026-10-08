import Foundation
import TrackScoutKit

/// A track: passes that share one coordinate frame (the world map of the first pass).
struct Project: Codable, Identifiable, Equatable {
    var id: String
    var name: String
    var createdAt: Date
    var worldMapID: String?
    var passes: [PassRecord] = []
}

enum UploadState: Codable, Equatable {
    case local
    case uploading(progress: Double)
    case uploaded(version: String)
    case failed(message: String)
}

struct PassRecord: Codable, Identifiable, Equatable {
    var id: String
    var type: PassType
    var createdAt: Date
    var file: String  // relative to Documents
    var durationS: Double
    var keptS: Double
    var segments: Int
    var sizeBytes: Int64
    var aligned: Bool
    var upload: UploadState = .local
}

/// Persistent app state (Application Support/state.json); recordings live in Documents so they are
/// visible over the cable (UIFileSharingEnabled) and in the Files app.
@MainActor
final class Store: ObservableObject {
    @Published var projects: [Project] = []
    @Published var quality: Quality = .high { didSet { save() } }
    @Published var autoUpload = true { didSet { save() } }
    @Published var workspaceID: String? { didSet { save() } }

    static let documents = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
    static let support: URL = {
        let url = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("TrackScout")
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }()

    private struct State: Codable {
        var projects: [Project]
        var quality: Quality
        var autoUpload: Bool
        var workspaceID: String?
    }

    private var loading = false

    init() { load() }

    private var stateURL: URL { Self.support.appendingPathComponent("state.json") }

    func load() {
        loading = true
        defer { loading = false }
        guard let data = try? Data(contentsOf: stateURL),
            let s = try? JSONDecoder().decode(State.self, from: data)
        else { return }
        projects = s.projects
        quality = s.quality
        autoUpload = s.autoUpload
        workspaceID = s.workspaceID
        // An upload that was running when the app quit continues from the backend's received parts.
        for p in projects.indices {
            for i in projects[p].passes.indices {
                if case .uploading = projects[p].passes[i].upload { projects[p].passes[i].upload = .local }
            }
        }
    }

    func save() {
        guard !loading else { return }
        let s = State(projects: projects, quality: quality, autoUpload: autoUpload, workspaceID: workspaceID)
        if let data = try? JSONEncoder().encode(s) { try? data.write(to: stateURL, options: .atomic) }
    }

    func addProject(name: String) -> Project {
        let p = Project(id: UUID().uuidString, name: name, createdAt: Date())
        projects.insert(p, at: 0)
        save()
        return p
    }

    func deleteProject(_ id: String) {
        guard let p = projects.first(where: { $0.id == id }) else { return }
        for pass in p.passes { try? FileManager.default.removeItem(at: Self.documents.appendingPathComponent(pass.file)) }
        try? FileManager.default.removeItem(at: Self.support.appendingPathComponent(id))
        projects.removeAll { $0.id == id }
        save()
    }

    func update(_ project: Project) {
        if let i = projects.firstIndex(where: { $0.id == project.id }) { projects[i] = project }
        save()
    }

    func setUpload(project: String, pass: String, _ state: UploadState) {
        guard let p = projects.firstIndex(where: { $0.id == project }),
            let i = projects[p].passes.firstIndex(where: { $0.id == pass })
        else { return }
        projects[p].passes[i].upload = state
        save()
    }

    func deletePass(project: String, pass: String) {
        guard let p = projects.firstIndex(where: { $0.id == project }) else { return }
        if let rec = projects[p].passes.first(where: { $0.id == pass }) {
            try? FileManager.default.removeItem(at: Self.documents.appendingPathComponent(rec.file))
            try? FileManager.default.removeItem(at: previewURL(project: project, pass: pass))
        }
        projects[p].passes.removeAll { $0.id == pass }
        save()
    }

    func worldMapURL(project: String) -> URL {
        Self.support.appendingPathComponent(project).appendingPathComponent("worldmap.arworldmap")
    }

    func previewURL(project: String, pass: String) -> URL {
        Self.support.appendingPathComponent(project).appendingPathComponent("\(pass).ply")
    }

    /// Free space for new recordings.
    static func freeBytes() -> Int64 {
        let values = try? documents.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey])
        return values?.volumeAvailableCapacityForImportantUsage ?? 0
    }
}
