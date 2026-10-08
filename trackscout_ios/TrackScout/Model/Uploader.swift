import Foundation
import Network
import TrackScoutKit
import UIKit

/// Gets finished passes off the phone (spec 0007 scopes 8–9): directly to the team backend (automatic on Wi-Fi,
/// cellular only after asking). If the backend is too slow, the upload pauses and the user picks: keep uploading,
/// send to the paired laptop by cable, or by Bluetooth. Parts that reached the backend are never sent again.
@MainActor
final class Uploader: ObservableObject {
    struct RouteRequest: Identifiable {
        let project: String
        let pass: String
        let slow: SlowBackend
        var id: String { pass }
    }

    @Published private(set) var pairing: Pairing? = PairingStore.load()
    @Published private(set) var onWiFi = true
    @Published private(set) var online = true
    /// Set when a pass waits for permission to use mobile data; the UI shows a confirmation.
    @Published var cellularRequest: (project: String, pass: String)?
    /// Set when the backend is too slow; the UI offers the three routes with ETAs.
    @Published var routeRequest: RouteRequest?
    @Published private(set) var workspaces: [BackendClient.Workspace] = []
    @Published var lastError: String?
    @Published private(set) var bluetoothNote: String?

    private let monitor = NWPathMonitor()
    private weak var store: Store?
    private var running: Set<String> = []
    private let bluetooth = BluetoothSender()
    private var cableTimer: Timer?
    /// Passes currently offered over Bluetooth, by pass id.
    private var bluetoothOffers: [String: (offer: OfferedPass, url: URL)] = [:]
    private var outbox: Outbox { Outbox(documents: Store.documents) }
    private var phoneName: String { UIDevice.current.name }

    init() {
        monitor.pathUpdateHandler = { [weak self] path in
            Task { @MainActor in
                self?.online = path.status == .satisfied
                self?.onWiFi = path.status == .satisfied && !path.isExpensive
                self?.uploadPending()
            }
        }
        monitor.start(queue: DispatchQueue(label: "org.raceforge.trackscout.path"))
        bluetooth.onEvent = { [weak self] event in self?.bluetoothEvent(event) }
        bluetooth.onState = { [weak self] note in self?.bluetoothNote = note }
    }

    func attach(_ store: Store) {
        self.store = store
        uploadPending()
        resumeLaptopTransfers()
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
        bluetooth.stop()
        bluetoothOffers = [:]
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

    /// `force`: keep uploading even when slow (the user chose the backend in the route dialog).
    func upload(project projectID: String, pass passID: String, allowCellular: Bool, force: Bool = false) async {
        guard let store, let workspace = store.workspaceID, let c = client(allowCellular: allowCellular),
            let project = store.projects.first(where: { $0.id == projectID }),
            let pass = project.passes.first(where: { $0.id == passID }), !running.contains(passID)
        else { return }
        running.insert(passID)
        defer { running.remove(passID) }
        store.setUpload(project: projectID, pass: passID, .uploading(progress: 0))
        let file = Store.documents.appendingPathComponent(pass.file)
        let report: @Sendable (Int64, Int64) -> Void = { sent, total in
            let fraction = total > 0 ? Double(sent) / Double(total) : 1
            Task { @MainActor in store.setUpload(project: projectID, pass: passID, .uploading(progress: fraction)) }
        }
        let gate = force ? nil : ThroughputGate(thresholds: store.thresholds, report: report)
        do {
            let version = try await c.uploadPass(file: file, projectName: project.name, workspaceId: workspace) {
                done, total in
                if let gate { try gate.check(done: done, total: total) } else { report(done, total) }
            }
            store.setUpload(project: projectID, pass: passID, .uploaded(version: version.semver))
        } catch let slow as SlowBackend {
            store.setUpload(project: projectID, pass: passID, .slow(rate: slow.rate, eta: slow.eta))
            routeRequest = RouteRequest(project: projectID, pass: passID, slow: slow)
        } catch {
            store.setUpload(project: projectID, pass: passID, .failed(message: describe(error)))
        }
    }

    /// The user's choice in the "backend is slow" dialog (or the pass detail's "send to laptop" buttons).
    func route(_ kind: RouteOption.Kind, project: String, pass: String) {
        routeRequest = nil
        switch kind {
        case .backend: Task { await upload(project: project, pass: pass, allowCellular: !onWiFi, force: true) }
        case .cable, .bluetooth: Task { await sendToLaptop(via: kind, project: project, pass: pass) }
        }
    }

    // MARK: laptop (cable / Bluetooth)

    private func offer(project projectID: String, pass passID: String) async throws -> (OfferedPass, URL)? {
        guard let store, let key = pairing?.laptopKey,
            let project = store.projects.first(where: { $0.id == projectID }),
            let pass = project.passes.first(where: { $0.id == passID })
        else { return nil }
        let url = Store.documents.appendingPathComponent(pass.file)
        let (name, file, type, created) = (project.name, pass.file, pass.type.rawValue, pass.createdAt)
        let offer = try await Task.detached {  // hashing a multi-GB pass takes a while
            try OfferedPass(id: passID, file: url, relativePath: file, project: name, passType: type, createdAt: created, key: key)
        }.value
        return (offer, url)
    }

    func sendToLaptop(via kind: RouteOption.Kind, project: String, pass: String) async {
        do {
            guard let (offer, url) = try await self.offer(project: project, pass: pass) else { return }
            store?.setUpload(project: project, pass: pass, .toLaptop(via: kind.rawValue, progress: 0))
            if kind == .cable {
                try outbox.add(offer, phone: phoneName)
                startCablePolling()
            } else {
                bluetoothOffers[offer.id] = (offer, url)
                bluetooth.offer(bluetoothOffers.values.sorted { $0.offer.id < $1.offer.id }, key: pairing?.laptopKey ?? "")
            }
        } catch {
            store?.setUpload(project: project, pass: pass, .failed(message: describe(error)))
        }
    }

    private func resumeLaptopTransfers() {
        if !outbox.entries().isEmpty { startCablePolling() }
        guard let store else { return }
        for project in store.projects {
            for pass in project.passes {
                if case .toLaptop(let via, _) = pass.upload, via == RouteOption.Kind.bluetooth.rawValue {
                    Task { await sendToLaptop(via: .bluetooth, project: project.id, pass: pass.id) }
                }
            }
        }
    }

    private func startCablePolling() {
        guard cableTimer == nil else { return }
        cableTimer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.pollCable() }
        }
    }

    /// Reads the laptop's `status.json` and updates the passes waiting for the cable.
    private func pollCable() {
        let entries = outbox.entries()
        guard !entries.isEmpty else {
            cableTimer?.invalidate()
            cableTimer = nil
            return
        }
        let status = outbox.status()
        for entry in entries {
            guard let s = status[entry.id], let (projectID, _) = locate(entry.id) else { continue }
            if s.done {
                store?.setUpload(project: projectID, pass: entry.id, .onLaptop(via: "cable"))
                try? outbox.remove(id: entry.id, phone: phoneName)
            } else {
                let p = s.size > 0 ? Double(s.received) / Double(s.size) : 0
                store?.setUpload(project: projectID, pass: entry.id, .toLaptop(via: "cable", progress: p))
            }
        }
    }

    private func bluetoothEvent(_ event: PhoneTransferSession.Event) {
        switch event {
        case .progress(let id, let sent, let total):
            if let (p, _) = locate(id) {
                store?.setUpload(project: p, pass: id, .toLaptop(via: "bluetooth", progress: Double(sent) / Double(max(total, 1))))
            }
        case .delivered(let id):
            if let (p, _) = locate(id) { store?.setUpload(project: p, pass: id, .onLaptop(via: "bluetooth")) }
            bluetoothOffers[id] = nil
            if bluetoothOffers.isEmpty { bluetooth.stop() }
        case .rejected: bluetoothNote = String(localized: "A laptop that is not paired with this phone tried to connect.")
        case .authenticated: bluetoothNote = nil
        }
    }

    private func locate(_ passID: String) -> (String, PassRecord)? {
        for project in store?.projects ?? [] {
            if let pass = project.passes.first(where: { $0.id == passID }) { return (project.id, pass) }
        }
        return nil
    }

    private func describe(_ error: Error) -> String {
        if case UploadError.http(let status, let detail) = error { return "\(status): \(detail)" }
        return error.localizedDescription
    }
}
