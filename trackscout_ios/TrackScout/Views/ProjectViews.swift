import SwiftUI
import TrackScoutKit

struct ProjectsView: View {
    @EnvironmentObject var store: Store
    @EnvironmentObject var uploader: Uploader
    @State private var newName = ""
    @State private var showNew = false
    @State private var showSettings = false

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
        }
    }
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
