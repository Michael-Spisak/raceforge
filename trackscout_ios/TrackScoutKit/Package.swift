// swift-tools-version:6.0
// TrackScoutKit: everything in TrackScout that does not need a camera (spec 0007).
// Only Apple frameworks; no third-party packages (AC2).
import PackageDescription

let package = Package(
    name: "TrackScoutKit",
    defaultLocalization: "en",
    platforms: [.iOS(.v18), .macOS(.v15)],
    products: [
        .library(name: "TrackScoutKit", targets: ["TrackScoutKit"]),
        .executable(name: "tscan-synth", targets: ["tscan-synth"]),
        .executable(name: "rftx-phone", targets: ["rftx-phone"]),
    ],
    targets: [
        .target(name: "TrackScoutKit"),
        .executableTarget(name: "tscan-synth", dependencies: ["TrackScoutKit"]),
        .executableTarget(name: "rftx-phone", dependencies: ["TrackScoutKit"]),
        .testTarget(name: "TrackScoutKitTests", dependencies: ["TrackScoutKit"]),
    ],
    swiftLanguageModes: [.v5]
)
