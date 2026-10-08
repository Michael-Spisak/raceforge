import SwiftUI

@main
struct TrackScoutApp: App {
    @StateObject private var store = Store()
    @StateObject private var uploader = Uploader()

    var body: some Scene {
        WindowGroup {
            Group {
                if CaptureController.isSupported || ProcessInfo.processInfo.environment["TRACKSCOUT_DEMO"] != nil {
                    ProjectsView()
                } else {
                    LiDARRequiredView()
                }
            }
            .environmentObject(store)
            .environmentObject(uploader)
            .task { uploader.attach(store) }
        }
    }
}

/// Devices without LiDAR (e.g. iPad Air) cannot scan, but they can drive the car (spec 0010 delivery C).
struct LiDARRequiredView: View {
    #if DEBUG
        @State private var showDrive = ProcessInfo.processInfo.environment["RF_OPEN_DRIVE"] != nil
    #else
        @State private var showDrive = false
    #endif

    var body: some View {
        ContentUnavailableView {
            Label("LiDAR required", systemImage: "sensor.tag.radiowaves.forward")
        } description: {
            Text("TrackScout needs an iPhone or iPad with a LiDAR scanner (iPhone 12 Pro or newer Pro models, iPad Pro).")
        } actions: {
            Button("Drive the car", systemImage: "steeringwheel") { showDrive = true }
                .buttonStyle(.borderedProminent)
        }
        .fullScreenCover(isPresented: $showDrive) { DriveView() }
    }
}
