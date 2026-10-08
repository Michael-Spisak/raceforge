import Foundation

/// A small, fully known pass for tests and the cross-language contract check (spec 0007 AC3):
/// two segments (pause between), the last 0.3 s discarded, depth 16×12, a floor + wall mesh, a world map.
public enum Synthetic {
    public static let depthSize = (width: 16, height: 12)

    /// Camera i sits at ARKit (0.1·i, 1.4, −0.05·i), looking along −z (identity rotation).
    public static func pose(_ i: Int) -> [Float] {
        [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0.1 * Float(i), 1.4, -0.05 * Float(i), 1]
    }

    public static let intrinsics: [Float] = [210, 0, 0, 0, 210, 0, 8, 6, 1]

    public static func depthValue(_ i: Int) -> Float { 1.5 + Float(i) * 0.25 }

    @discardableResult
    public static func makePass(in workDir: URL, archive: URL) throws -> Manifest {
        let rec = try PassRecorder(
            dir: workDir, project: .init(id: "proj-1", name: "Corridor Test"),
            pass: .init(id: "pass-1", type: .walkthrough, conditions: .init(lights: "on", doors: "closed", note: "synthetic")),
            quality: .high, depthSize: depthSize, app: .init(version: "0.1.0"),
            device: .init(model: "synthetic", system: "test"),
            createdAt: Date(timeIntervalSince1970: 1_791_000_000))
        let n = depthSize.width * depthSize.height
        var i = 0
        func frame(_ t: Double) throws {
            try rec.append(
                t: t, pose: pose(i), intrinsics: intrinsics, exposureS: 0.01, tracking: .normal,
                depth: Array(repeating: float16Bits(depthValue(i)), count: n), confidence: Array(repeating: 2, count: n))
            try rec.appendImu(.init(t: t, gravity: [0, -1, 0], acceleration: [0, 0, 0], rotationRate: [0, 0, 0.1]))
            i += 1
        }
        try rec.start(at: 0)
        for k in 0..<10 { try frame(Double(k) * 0.1) }
        try rec.pause(at: 1.0)
        try rec.resume(at: 2.0)
        for k in 0..<10 { try frame(2.0 + Double(k) * 0.1) }
        try rec.discardLast(seconds: 0.3, now: 2.9)
        let floor = PLY.encode(
            vertices: [[-1, 0, 1], [3, 0, 1], [3, 0, -2], [-1, 0, -2], [-1, 2, -2]],
            faces: [[0, 1, 2], [0, 2, 3], [3, 2, 4]], classification: [2, 2, 1])
        try rec.setMesh(segment: 0, ply: floor)
        try rec.setWorldMap(Data("synthetic world map".utf8), id: "map-1", aligned: true, alignedAtS: 0.4)
        let manifest = try rec.finish(at: 2.9)
        try rec.export(to: archive)
        return manifest
    }
}
