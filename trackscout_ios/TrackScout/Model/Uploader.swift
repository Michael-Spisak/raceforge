import Foundation
import Network
import TrackScoutKit

/// Uploads finished passes to the team backend (spec 0007 scope 8): automatic on Wi-Fi, cellular only after
/// the user agreed for that pass. Interrupted uploads resume from the parts the backend already has.
@MainActor
final class Uploader: ObservableObject {
    @Published private(set) var pairing: Pairing? = PairingStore.load()
    @Published private(set) var onWiFi = true
    @Published private(set) var online = true
    /// Set when a pass waits for permission to use mobile data; the UI shows a confirmation.
    @Published var cellularRequest: (project: String, pass: String)?
    @Published private(set) var workspaces: [BackendClient.Workspace] = []
    @Published var lastError: String?

    private let monitor = NWPathMonitor()
    private weak var store: Store?
    private var running: Set<String> = []

    init() {
        monitor.pathUpdateHandler = { [weak self] path in
            Task { @MainActor in
                self?.online = path.status == .satisfied
                self?.onWiFi = path.status == .satisfied && !path.isExpensive
                self?.uploadPending()
            }
        }
        monitor.start(queue: DispatchQueue(label: "org.raceforge.trackscout.path"))
    }

    func attach(_ store: Store) {
        self.store = store
        uploadPending()
    }

    func pair(_ p: Pairing) async {
        PairingStore.save(p)
        pairing = p
        if store?.workspaceID == nil { store?.workspaceID = p.workspaceId }
        await refreshWorkspaces()
        uploadPending()
    }

    func unpair() {
        PairingStore.save(nil)
        pairing = nil
        workspaces = []
    }

    private func client(allowCellular: Bool) -> BackendClient? {
        guard let p = pairing else { return nil }
        return BackendClient(server: p.server, token: p.token, allowCellular: allowCellular)
    }

    func refreshWorkspaces() async {
        guard let c = client(allowCellular: true) else { return }
        do {
            workspaces = try await c.workspaces()
            lastError = nil
        } catch {
            lastError = describe(error)
        }
    }

    /// Starts every pass that is not uploaded yet, if allowed (paired, workspace chosen, Wi-Fi, auto upload).
    func uploadPending() {
        guard let store, store.autoUpload, onWiFi, pairing != nil, store.workspaceID != nil else { return }
        for project in store.projects {
            for pass in project.passes where pass.upload == .local || isFailed(pass.upload) {
                Task { await upload(project: project.id, pass: pass.id, allowCellular: false) }
            }
        }
    }

    private func isFailed(_ s: UploadState) -> Bool {
        if case .failed = s { return true }
        return false
    }

    /// Manual "Upload now": asks before using mobile data.
    func requestUpload(project: String, pass: String) {
        if onWiFi || !online {
            Task { await upload(project: project, pass: pass, allowCellular: false) }
        } else {
            cellularRequest = (project, pass)
        }
    }

    func upload(project projectID: String, pass passID: String, allowCellular: Bool) async {
        guard let store, let workspace = store.workspaceID, let c = client(allowCellular: allowCellular),
            let project = store.projects.first(where: { $0.id == projectID }),
            let pass = project.passes.first(where: { $0.id == passID }), !running.contains(passID)
        else { return }
        running.insert(passID)
        defer { running.remove(passID) }
        store.setUpload(project: projectID, pass: passID, .uploading(progress: 0))
        let file = Store.documents.appendingPathComponent(pass.file)
        do {
            let version = try await c.uploadPass(file: file, projectName: project.name, workspaceId: workspace) {
                sent, total in
                let fraction = total > 0 ? Double(sent) / Double(total) : 1
                Task { @MainActor in store.setUpload(project: projectID, pass: passID, .uploading(progress: fraction)) }
            }
            store.setUpload(project: projectID, pass: passID, .uploaded(version: version.semver))
        } catch {
            store.setUpload(project: projectID, pass: passID, .failed(message: describe(error)))
        }
    }

    private func describe(_ error: Error) -> String {
        if case UploadError.http(let status, let detail) = error { return "\(status): \(detail)" }
        return error.localizedDescription
    }
}
