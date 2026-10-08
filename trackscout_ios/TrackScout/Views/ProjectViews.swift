import SwiftUI
import TrackScoutKit

struct ProjectsView: View {
    @EnvironmentObject var store: Store
    @EnvironmentObject var uploader: Uploader
    @State private var newName = ""
    @State private var showNew = false
    @State private var showSettings = false
    @State private var showDrive = false

    var body: some View {
        NavigationStack {
            List {
                if uploader.pairing == nil {
                    Section {
                        Button {
                            showSettings = true
                        } label: {
                            Label("Pair with RaceForge to upload", systemImage: "qrcode.viewfinder")
                        }
                    }
                }
                Section("Tracks") {
                    if store.projects.isEmpty {
                        Text("No tracks yet. Create one per corridor or course.").foregroundStyle(.secondary)
                    }
                    ForEach(store.projects) { p in
                        NavigationLink(value: p.id) {
                            VStack(alignment: .leading) {
                                Text(verbatim: p.name).font(.headline)
                                Text("\(p.passes.count) passes").font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                    .onDelete { idx in idx.map { store.projects[$0].id }.forEach(store.deleteProject) }
                }
            }
            .navigationTitle("TrackScout")
            .navigationDestination(for: String.self) { id in ProjectView(projectID: id) }
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button { showSettings = true } label: { Image(systemName: "gearshape") }
                        .accessibilityLabel(Text("Settings"))
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showDrive = true } label: { Image(systemName: "steeringwheel") }
                        .accessibilityLabel(Text("Drive the car"))
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showNew = true } label: { Image(systemName: "plus") }
                        .accessibilityLabel(Text("New track"))
                }
            }
            .alert("New track", isPresented: $showNew) {
                TextField("Name, e.g. Corridor 2nd floor", text: $newName)
                Button("Create") {
                    if !newName.trimmingCharacters(in: .whitespaces).isEmpty { _ = store.addProject(name: newName) }
                    newName = ""
                }
                Button("Cancel", role: .cancel) { newName = "" }
            }
            .sheet(isPresented: $showSettings) { SettingsView() }
            .fullScreenCover(isPresented: $showDrive) { DriveView() }
            .alert(
                "Use mobile data?",
                isPresented: Binding(get: { uploader.cellularRequest != nil }, set: { if !$0 { uploader.cellularRequest = nil } })
            ) {
                Button("Upload over mobile data") {
                    if let r = uploader.cellularRequest {
                        Task { await uploader.upload(project: r.project, pass: r.pass, allowCellular: true) }
                    }
                    uploader.cellularRequest = nil
                }
                Button("Wait for Wi-Fi", role: .cancel) { uploader.cellularRequest = nil }
            } message: {
                Text("Recordings are large. Uploading over mobile data can use up your data plan.")
            }
            .sheet(item: $uploader.routeRequest) { request in
                RouteSheet(request: request).presentationDetents([.medium])
            }
        }
    }
}

/// "The backend is slow" (spec 0007 scope 8): keep uploading, or send the pass to the paired laptop.
struct RouteSheet: View {
    @EnvironmentObject var uploader: Uploader
    @Environment(\.dismiss) private var dismiss
    let request: Uploader.RouteRequest

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Text("Uploading to the backend runs at \(mbps(request.slow.rate)) MB/s.")
                } footer: {
                    Text("Parts already uploaded are kept. The laptop uploads the pass later, when its connection is good.")
                }
                ForEach(request.slow.options, id: \.kind) { option in
                    Button {
                        uploader.route(option.kind, project: request.project, pass: request.pass)
                        dismiss()
                    } label: {
                        HStack {
                            Label(LocalizedStringKey(title(option.kind)), systemImage: icon(option.kind))
                            Spacer()
                            VStack(alignment: .trailing) {
                                Text(verbatim: etaText(option.eta)).monospacedDigit()
                                if option.recommended { Text("Recommended").font(.caption2).foregroundStyle(.green) }
                            }
                        }
                    }
                }
            }
            .navigationTitle("Slow connection")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Later") { dismiss() } } }
        }
    }

    private func title(_ k: RouteOption.Kind) -> String {
        switch k {
        case .backend: return "Keep uploading to the backend"
        case .cable: return "Send to the laptop by cable"
        case .bluetooth: return "Send to the laptop by Bluetooth"
        }
    }

    private func icon(_ k: RouteOption.Kind) -> String {
        switch k {
        case .backend: return "icloud.and.arrow.up"
        case .cable: return "cable.connector"
        case .bluetooth: return "dot.radiowaves.left.and.right"
        }
    }
}

func mbps(_ rate: Double) -> String { String(format: "%.2f", rate / 1_000_000) }

/// "≈ 12 min" style estimate.
func etaText(_ seconds: Double) -> String {
    guard seconds.isFinite else { return "–" }
    let d = Duration.seconds(max(1, seconds.rounded()))
    return "≈ " + d.formatted(.units(allowed: [.hours, .minutes, .seconds], width: .abbreviated, maximumUnitCount: 2))
}

struct ProjectView: View {
    @EnvironmentObject var store: Store
    @EnvironmentObject var uploader: Uploader
    let projectID: String
    @State private var setup = false

    var project: Project? { store.projects.first { $0.id == projectID } }

    var body: some View {
        if let project {
            List {
                Section {
                    Button {
                        setup = true
                    } label: {
                        Label(project.passes.isEmpty ? "Record first pass" : "Record another pass", systemImage: "record.circle")
                    }
                    if project.worldMapID != nil {
                        Label("New passes line up with the first one automatically.", systemImage: "scope")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
                Section("Passes") {
                    ForEach(project.passes) { pass in
                        NavigationLink {
                            PassDetailView(projectID: project.id, passID: pass.id)
                        } label: {
                            PassRow(pass: pass)
                        }
                    }
                    .onDelete { idx in
                        idx.map { project.passes[$0].id }.forEach { store.deletePass(project: project.id, pass: $0) }
                    }
                }
            }
            .navigationTitle(Text(verbatim: project.name))
            .fullScreenCover(isPresented: $setup) { RecordView(project: project) }
        }
    }
}

struct PassRow: View {
    let pass: PassRecord

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack {
                Text(LocalizedStringKey(pass.type.title)).font(.headline)
                Spacer()
                UploadBadge(state: pass.upload)
            }
            Text(verbatim: "\(pass.createdAt.formatted(date: .abbreviated, time: .shortened)) · \(formatDuration(pass.keptS)) · \(ByteCountFormatter.string(fromByteCount: pass.sizeBytes, countStyle: .file))")
                .font(.caption).foregroundStyle(.secondary)
        }
    }
}

struct UploadBadge: View {
    let state: UploadState

    var body: some View {
        switch state {
        case .local: Label("On phone", systemImage: "iphone").labelStyle(.iconOnly).foregroundStyle(.secondary)
        case .uploading(let p): ProgressView(value: p).frame(width: 60)
        case .uploaded: Image(systemName: "checkmark.icloud").foregroundStyle(.green)
        case .failed: Image(systemName: "exclamationmark.icloud").foregroundStyle(.red)
        case .slow: Image(systemName: "tortoise").foregroundStyle(.orange)
        case .toLaptop(_, let p): ProgressView(value: p).frame(width: 60).tint(.purple)
        case .onLaptop: Image(systemName: "laptopcomputer").foregroundStyle(.purple)
        }
    }
}

extension PassType {
    var title: String {
        switch self {
        case .walkthrough: return "Walkthrough"
        case .high: return "High / overview"
        case .low: return "Low / car height"
        case .detail: return "Detail"
        case .gapFill: return "Gap fill"
        }
    }
}

extension Quality {
    var title: String {
        switch self {
        case .maximum: return "Maximum"
        case .high: return "High"
        case .economy: return "Economy"
        }
    }
}

func formatDuration(_ s: Double) -> String {
    let total = Int(s.rounded())
    return String(format: "%d:%02d", total / 60, total % 60)
}
