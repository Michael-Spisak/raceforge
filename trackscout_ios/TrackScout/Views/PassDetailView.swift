import SceneKit
import SwiftUI
import TrackScoutKit

/// On-phone review before upload: mesh preview, facts, upload / share / delete (spec 0007 scope 10).
struct PassDetailView: View {
    @EnvironmentObject var store: Store
    @EnvironmentObject var uploader: Uploader
    @Environment(\.dismiss) private var dismiss
    let projectID: String
    let passID: String
    @State private var confirmDelete = false

    var pass: PassRecord? { store.projects.first { $0.id == projectID }?.passes.first { $0.id == passID } }

    var body: some View {
        if let pass {
            List {
                Section {
                    MeshPreview(url: store.previewURL(project: projectID, pass: passID))
                        .frame(height: 260)
                        .listRowInsets(EdgeInsets())
                }
                Section {
                    LabeledContent("Type") { Text(LocalizedStringKey(pass.type.title)) }
                    LabeledContent("Recorded") { Text(verbatim: formatDuration(pass.durationS)) }
                    LabeledContent("Kept") { Text(verbatim: formatDuration(pass.keptS)) }
                    LabeledContent("Segments") { Text(verbatim: "\(pass.segments)") }
                    LabeledContent("Size") {
                        Text(verbatim: ByteCountFormatter.string(fromByteCount: pass.sizeBytes, countStyle: .file))
                    }
                    LabeledContent("Aligned with first pass") { Text(pass.aligned ? "Yes" : "No") }
                }
                Section("Upload") {
                    switch pass.upload {
                    case .uploaded(let v): Label("Uploaded as version \(v)", systemImage: "checkmark.icloud")
                    case .uploading(let p): ProgressView(value: p) { Text("Uploading…") }
                    case .failed(let msg):
                        Label { Text(verbatim: msg) } icon: { Image(systemName: "exclamationmark.icloud") }
                            .foregroundStyle(.red)
                    case .local: Text("Only on this phone").foregroundStyle(.secondary)
                    case .slow(let rate, let eta):
                        Label("Backend slow: \(mbps(rate)) MB/s, \(etaText(eta))", systemImage: "tortoise")
                            .foregroundStyle(.orange)
                    case .toLaptop(let via, let p):
                        ProgressView(value: p) { Text(via == "cable" ? "Waiting for the laptop (cable)…" : "Sending by Bluetooth…") }
                        if via == "cable" {
                            Text("Connect the phone to the paired laptop and click “Receive by cable” on the Team tab.")
                                .font(.caption).foregroundStyle(.secondary)
                        } else {
                            Text("Keep TrackScout open and click “Receive by Bluetooth” on the laptop's Team tab.")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    case .onLaptop: Label("On the laptop — it uploads the pass", systemImage: "laptopcomputer")
                    }
                    if let note = uploader.bluetoothNote {
                        Text(verbatim: note).font(.caption).foregroundStyle(.orange)
                    }
                    if uploader.pairing == nil {
                        Text("Pair with RaceForge in the settings to upload.").font(.caption)
                    } else if !isUploaded(pass.upload) {
                        Button("Upload now") { uploader.requestUpload(project: projectID, pass: passID) }
                        Button("Send to the laptop by cable") {
                            uploader.route(.cable, project: projectID, pass: passID)
                        }
                        Button("Send to the laptop by Bluetooth") {
                            uploader.route(.bluetooth, project: projectID, pass: passID)
                        }
                    }
                    ShareLink(item: Store.documents.appendingPathComponent(pass.file)) {
                        Label("Share file (AirDrop, Files)", systemImage: "square.and.arrow.up")
                    }
                }
                Section {
                    Button("Delete pass", role: .destructive) { confirmDelete = true }
                }
            }
            .navigationTitle(Text(LocalizedStringKey(pass.type.title)))
            .confirmationDialog("Delete this pass from the phone?", isPresented: $confirmDelete, titleVisibility: .visible) {
                Button("Delete", role: .destructive) {
                    store.deletePass(project: projectID, pass: passID)
                    dismiss()
                }
            }
        }
    }

    private func isUploaded(_ s: UploadState) -> Bool {
        switch s {
        case .uploaded, .uploading, .toLaptop, .onLaptop: return true
        default: return false
        }
    }
}

/// SceneKit view of the pass mesh, coloured by ARKit class (floor, wall, door, …).
struct MeshPreview: UIViewRepresentable {
    let url: URL

    static let colors: [UInt8: UIColor] = [
        1: .systemBlue, 2: .systemGray, 3: .systemTeal, 4: .systemOrange, 5: .systemPink, 6: .systemCyan, 7: .systemGreen,
    ]

    func makeUIView(context: Context) -> SCNView {
        let view = SCNView()
        view.allowsCameraControl = true
        view.autoenablesDefaultLighting = true
        view.backgroundColor = .secondarySystemBackground
        view.scene = SCNScene()
        if let data = try? Data(contentsOf: url), let mesh = PLY.decode(data) {
            view.scene?.rootNode.addChildNode(Self.node(mesh))
        }
        return view
    }

    func updateUIView(_ uiView: SCNView, context: Context) {}

    static func node(_ mesh: PLY.Mesh) -> SCNNode {
        let root = SCNNode()
        let source = SCNGeometrySource(vertices: mesh.vertices.map { SCNVector3($0.x, $0.y, $0.z) })
        var byClass: [UInt8: [UInt32]] = [:]
        for (f, c) in zip(mesh.faces, mesh.classification) { byClass[c, default: []] += [f.x, f.y, f.z] }
        for (cls, idx) in byClass {
            let element = SCNGeometryElement(indices: idx, primitiveType: .triangles)
            let geo = SCNGeometry(sources: [source], elements: [element])
            let m = SCNMaterial()
            m.diffuse.contents = colors[cls] ?? UIColor.systemYellow
            m.isDoubleSided = true
            geo.materials = [m]
            root.addChildNode(SCNNode(geometry: geo))
        }
        return root
    }
}
