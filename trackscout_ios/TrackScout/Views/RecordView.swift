import ARKit
import SceneKit
import SwiftUI
import TrackScoutKit

#if canImport(RoomPlan)
    import RoomPlan
#endif

/// Pass setup → live recording with Pause / Continue / Discard → Finish (spec 0007 scope 2–6).
struct RecordView: View {
    @EnvironmentObject var store: Store
    @EnvironmentObject var uploader: Uploader
    @Environment(\.dismiss) private var dismiss
    let project: Project

    @StateObject private var capture = CaptureController()
    @State private var passType: PassType = .walkthrough
    @State private var lights = "on"
    @State private var doors = "closed"
    @State private var note = ""
    @State private var roomplan = false
    @State private var configured = false
    @State private var saving = false
    @State private var error: String?

    var body: some View {
        ZStack {
            if configured {
                CameraView(session: capture.session, roomplan: roomplan, capture: capture).ignoresSafeArea()
                VStack {
                    statusBar
                    Spacer()
                    controls
                }
                .padding()
            } else {
                setupForm
            }
            if saving {
                ProgressView("Saving pass…").padding().background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12))
            }
        }
        .alert("Could not save the pass", isPresented: Binding(get: { error != nil }, set: { if !$0 { error = nil } })) {
            Button("OK") { error = nil }
        } message: {
            Text(verbatim: error ?? "")
        }
        .onDisappear { capture.stop() }
    }

    // MARK: setup

    private var setupForm: some View {
        NavigationStack {
            Form {
                Section("Pass") {
                    Picker("Type", selection: $passType) {
                        ForEach(PassType.allCases, id: \.self) { Text(LocalizedStringKey($0.title)).tag($0) }
                    }
                    Picker("Quality", selection: $store.quality) {
                        ForEach(Quality.allCases, id: \.self) { Text(LocalizedStringKey($0.title)).tag($0) }
                    }
                    Text("About \(store.quality.minutesRemaining(freeBytes: Store.freeBytes())) minutes of free storage at this quality.")
                        .font(.caption).foregroundStyle(.secondary)
                    #if canImport(RoomPlan)
                        if RoomCaptureSession.isSupported {
                            Toggle("Also capture walls and doors (RoomPlan)", isOn: $roomplan)
                        }
                    #endif
                }
                Section("Conditions") {
                    Picker("Lights", selection: $lights) {
                        Text("On").tag("on")
                        Text("Off").tag("off")
                        Text("Mixed").tag("mixed")
                    }
                    Picker("Doors", selection: $doors) {
                        Text("Closed").tag("closed")
                        Text("Open").tag("open")
                        Text("Mixed").tag("mixed")
                    }
                    TextField("Note", text: $note)
                }
            }
            .navigationTitle("New pass")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Continue") {
                        capture.prepare(quality: store.quality, worldMap: loadWorldMap())
                        configured = true
                    }
                }
            }
        }
    }

    private func loadWorldMap() -> ARWorldMap? {
        guard project.worldMapID != nil,
            let data = try? Data(contentsOf: store.worldMapURL(project: project.id))
        else { return nil }
        return try? NSKeyedUnarchiver.unarchivedObject(ofClass: ARWorldMap.self, from: data)
    }

    // MARK: live

    private var statusBar: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(verbatim: formatDuration(capture.keptS)).font(.title2.monospacedDigit().bold())
                if case .recording = capture.state {
                    Circle().fill(.red).frame(width: 10, height: 10)
                } else if capture.state == .paused {
                    Text("Paused").font(.caption.bold())
                }
                Spacer()
                if capture.usesWorldMap {
                    Label(capture.aligned ? "Aligned" : "Finding first pass…",
                          systemImage: capture.aligned ? "scope" : "location.magnifyingglass")
                        .font(.caption)
                }
            }
            ForEach(Array(capture.warnings).sorted { $0.rawValue < $1.rawValue }, id: \.self) { w in
                Label(LocalizedStringKey(w.message), systemImage: "exclamationmark.triangle.fill")
                    .font(.caption).foregroundStyle(.yellow)
            }
        }
        .padding(10)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))
    }

    private var controls: some View {
        HStack(spacing: 16) {
            switch capture.state {
            case .idle:
                Button("Cancel") { dismiss() }.buttonStyle(.bordered)
                Button {
                    try? capture.start(
                        project: project,
                        pass: .init(id: UUID().uuidString, type: passType,
                                    conditions: .init(lights: lights, doors: doors, note: note)))
                } label: {
                    Label("Record", systemImage: "record.circle").frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent).tint(.red)
            case .recording:
                discardMenu
                Button { capture.pause() } label: {
                    Label("Pause", systemImage: "pause.fill").frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
            case .paused:
                discardMenu
                Button { capture.resume() } label: {
                    Label("Continue", systemImage: "record.circle")
                }
                .buttonStyle(.borderedProminent).tint(.red)
                Button { finish() } label: { Label("Finish", systemImage: "checkmark") }
                    .buttonStyle(.bordered)
            case .finished:
                EmptyView()
            }
        }
        .controlSize(.large)
        .padding(10)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 16))
    }

    private var discardMenu: some View {
        Menu {
            ForEach([5.0, 10, 30], id: \.self) { s in
                Button("Discard last \(Int(s)) seconds") { capture.discard(seconds: s) }
            }
        } label: {
            Image(systemName: "gobackward").padding(6)
        }
        .accessibilityLabel(Text("Discard"))
    }

    private func finish() {
        saving = true
        let first = project.worldMapID == nil
        let folder = Store.documents.appendingPathComponent(folderName(project))
        let stamp = Date().formatted(.iso8601.year().month().day().time(includingFractionalSeconds: false))
            .replacingOccurrences(of: ":", with: "")
        let archive = folder.appendingPathComponent("\(passType.rawValue)-\(stamp).tscan")
        Task {
            do {
                let result = try await capture.finish(to: archive, includeWorldMap: first)
                var p = store.projects.first { $0.id == project.id } ?? project
                if first, let map = result.worldMap {
                    let url = store.worldMapURL(project: project.id)
                    try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
                    try map.write(to: url)
                    p.worldMapID = result.manifest.worldMap.id ?? project.id
                }
                let m = result.manifest
                if let ply = result.previewPLY {
                    let url = store.previewURL(project: project.id, pass: m.pass.id)
                    try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
                    try? ply.write(to: url)
                }
                let size = (try? FileManager.default.attributesOfItem(atPath: archive.path)[.size] as? NSNumber)?.int64Value ?? 0
                p.passes.insert(
                    PassRecord(
                        id: m.pass.id, type: m.pass.type, createdAt: m.createdAt,
                        file: "\(folderName(project))/\(archive.lastPathComponent)",
                        durationS: m.segments.reduce(0) { $0 + ($1.endS - $1.startS) },
                        keptS: m.segments.reduce(0) { sum, s in
                            sum + (s.endS - s.startS) - s.discarded.reduce(0) { $0 + ($1[1] - $1[0]) }
                        },
                        segments: m.segments.count, sizeBytes: size, aligned: m.worldMap.aligned),
                    at: 0)
                store.update(p)
                uploader.uploadPending()
                saving = false
                dismiss()
            } catch {
                saving = false
                self.error = error.localizedDescription
            }
        }
    }

    private func folderName(_ p: Project) -> String {
        let slug = captureSlug(projectName: p.name).replacingOccurrences(of: "scan-", with: "")
        return "\(slug)-\(p.id.prefix(8))"
    }
}

extension CaptureWarning {
    var message: String {
        switch self {
        case .tooFast: return "Move more slowly"
        case .lowLight: return "Too dark or too few details"
        case .trackingLost: return "Tracking lost — go back to a known spot"
        case .tooFar: return "Too far from the walls (> 4 m)"
        case .relocalizing: return "Point at an area from the first pass"
        }
    }
}

/// Camera preview with the reconstructed mesh, or RoomPlan's live view for RoomPlan passes.
struct CameraView: UIViewRepresentable {
    let session: ARSession
    let roomplan: Bool
    let capture: CaptureController

    func makeUIView(context: Context) -> UIView {
        #if canImport(RoomPlan)
            if roomplan {
                let view = RoomCaptureView(frame: .zero, arSession: session)
                view.delegate = context.coordinator
                view.captureSession.run(configuration: RoomCaptureSession.Configuration())
                context.coordinator.view = view
                return view
            }
        #endif
        let view = ARSCNView(frame: .zero)
        view.session = session
        view.debugOptions = [.showWorldOrigin]
        view.automaticallyUpdatesLighting = true
        return view
    }

    func updateUIView(_ uiView: UIView, context: Context) {
        #if canImport(RoomPlan)
            if case .paused = capture.state, let v = uiView as? RoomCaptureView, !context.coordinator.stopped {
                context.coordinator.stopped = true
                v.captureSession.stop(pauseARSession: false)
            }
        #endif
    }

    func makeCoordinator() -> Coordinator { Coordinator(capture: capture) }

    @objc(TSCameraViewCoordinator)
    final class Coordinator: NSObject {
        let capture: CaptureController
        var stopped = false
        #if canImport(RoomPlan)
            weak var view: RoomCaptureView?
        #endif
        init(capture: CaptureController) { self.capture = capture }
        #if canImport(RoomPlan)
            // RoomCaptureViewDelegate inherits NSCoding; the coordinator is never archived.
            init?(coder: NSCoder) { nil }
            func encode(with coder: NSCoder) {}
        #endif
    }
}

#if canImport(RoomPlan)
    extension CameraView.Coordinator: RoomCaptureViewDelegate {
        func captureView(shouldPresent roomDataForProcessing: CapturedRoomData, error: Error?) -> Bool { true }

        func captureView(didPresent processedResult: CapturedRoom, error: Error?) {
            guard error == nil, let json = try? JSONEncoder().encode(processedResult) else { return }
            let usdz = FileManager.default.temporaryDirectory.appendingPathComponent("roomplan-\(UUID().uuidString).usdz")
            let exported = (try? processedResult.export(to: usdz)) != nil
            capture.attachRoomPlan(json: json, usdz: exported ? usdz : nil)
        }
    }
#endif
