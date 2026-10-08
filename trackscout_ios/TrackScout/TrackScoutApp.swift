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

struct LiDARRequiredView: View {
    var body: some View {
        ContentUnavailableView(
            "LiDAR required", systemImage: "sensor.tag.radiowaves.forward",
            description: Text("TrackScout needs an iPhone or iPad with a LiDAR scanner (iPhone 12 Pro or newer Pro models, iPad Pro)."))
    }
}
