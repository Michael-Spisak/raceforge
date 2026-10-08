import SwiftUI
import TrackScoutKit
import VisionKit

struct SettingsView: View {
    @EnvironmentObject var store: Store
    @EnvironmentObject var uploader: Uploader
    @Environment(\.dismiss) private var dismiss
    @State private var scanning = false
    @State private var pasted = ""
    @State private var pairError: String?

    var body: some View {
        NavigationStack {
            Form {
                Section("RaceForge backend") {
                    if let p = uploader.pairing {
                        LabeledContent("Server") { Text(verbatim: p.server.host() ?? p.server.absoluteString) }
                        LabeledContent("Laptop") { Text(verbatim: p.laptopName) }
                        Picker("Workspace", selection: Binding(get: { store.workspaceID ?? "" }, set: { store.workspaceID = $0 })) {
                            ForEach(uploader.workspaces, id: \.id) { Text(verbatim: $0.name).tag($0.id) }
                        }
                        .task { await uploader.refreshWorkspaces() }
                        Button("Unpair", role: .destructive) { uploader.unpair() }
                    } else {
                        Text("In the RaceForge app, open Team → Pair TrackScout and scan the QR code.")
                            .font(.caption)
                        if DataScannerViewController.isSupported {
                            Button {
                                scanning = true
                            } label: {
                                Label("Scan QR code", systemImage: "qrcode.viewfinder")
                            }
                        }
                        TextField("…or paste the pairing link", text: $pasted)
                            .textInputAutocapitalization(.never).autocorrectionDisabled()
                            .onSubmit { pair(pasted) }
                    }
                    if let e = pairError ?? uploader.lastError {
                        Text(verbatim: e).font(.caption).foregroundStyle(.red)
                    }
                }
                Section("Recording") {
                    Picker("Default quality", selection: $store.quality) {
                        ForEach(Quality.allCases, id: \.self) { Text(LocalizedStringKey($0.title)).tag($0) }
                    }
                    Toggle("Upload automatically on Wi-Fi", isOn: $store.autoUpload)
                }
                Section {
                    Stepper(value: $store.maxUploadMinutes, in: 1...120, step: 1) {
                        Text("Ask when an upload needs more than \(Int(store.maxUploadMinutes)) min")
                    }
                    Stepper(value: $store.minUploadMBps, in: 0.1...20, step: 0.1) {
                        Text("…or runs slower than \(String(format: "%.1f", store.minUploadMBps)) MB/s")
                    }
                } header: {
                    Text("Slow connection")
                } footer: {
                    Text("Then TrackScout offers to send the pass to the paired laptop by cable or Bluetooth instead.")
                }
                Section {
                    Text("TrackScout is free and open source (GPL-3.0). It only talks to your own RaceForge backend.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Settings")
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            .sheet(isPresented: $scanning) {
                QRScanner { text in
                    scanning = false
                    pair(text)
                }
                .ignoresSafeArea()
            }
        }
    }

    private func pair(_ text: String) {
        do {
            let p = try Pairing.parse(text)
            pairError = nil
            Task { await uploader.pair(p) }
        } catch {
            pairError = String(localized: "This is not a RaceForge pairing code.")
        }
    }
}

/// QR code scanner (VisionKit, free Apple framework).
struct QRScanner: UIViewControllerRepresentable {
    var prefix = "raceforge://pair"
    let onCode: (String) -> Void

    func makeUIViewController(context: Context) -> DataScannerViewController {
        let vc = DataScannerViewController(
            recognizedDataTypes: [.barcode(symbologies: [.qr])], qualityLevel: .balanced,
            recognizesMultipleItems: false, isHighlightingEnabled: true)
        vc.delegate = context.coordinator
        try? vc.startScanning()
        return vc
    }

    func updateUIViewController(_ vc: DataScannerViewController, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator(prefix: prefix, onCode: onCode) }

    final class Coordinator: NSObject, DataScannerViewControllerDelegate {
        let prefix: String
        let onCode: (String) -> Void
        private var done = false
        init(prefix: String, onCode: @escaping (String) -> Void) {
            self.prefix = prefix
            self.onCode = onCode
        }

        func dataScanner(_ scanner: DataScannerViewController, didAdd items: [RecognizedItem], allItems: [RecognizedItem]) {
            guard !done else { return }
            for case .barcode(let code) in items {
                if let text = code.payloadStringValue, text.hasPrefix(prefix) {
                    done = true
                    scanner.stopScanning()
                    onCode(text)
                    return
                }
            }
        }
    }
}
