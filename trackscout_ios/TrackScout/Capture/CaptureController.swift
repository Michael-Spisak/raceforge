import ARKit
import AVFoundation
import CoreMotion
import Foundation
import TrackScoutKit
import UIKit

enum CaptureWarning: String, CaseIterable {
    case tooFast, lowLight, trackingLost, tooFar, relocalizing
}

/// Result of a finished pass, ready for the store and the upload.
struct FinishedPass {
    let archive: URL
    let manifest: Manifest
    let previewPLY: Data?
    let worldMap: Data?
}

/// Drives ARKit and feeds `PassRecorder` (spec 0007 scope 3–5). All recording work happens on `queue`
/// (also the ARSession delegate queue); published UI state is updated on the main thread.
final class CaptureController: NSObject, ObservableObject, ARSessionDelegate, @unchecked Sendable {
    @Published private(set) var state: RecorderState = .idle
    @Published private(set) var warnings: Set<CaptureWarning> = []
    @Published private(set) var aligned = false
    @Published private(set) var usesWorldMap = false
    @Published private(set) var elapsedS: Double = 0
    @Published private(set) var keptS: Double = 0
    @Published private(set) var frames = 0

    let session = ARSession()
    private let queue = DispatchQueue(label: "org.raceforge.trackscout.capture", qos: .userInitiated)
    private let motion = CMMotionManager()
    private var recorder: PassRecorder?
    private var video: VideoWriter?
    private var quality: Quality = .high
    private var lastFrameT: Double = -1
    private var lastT: Double = 0
    private var lastWarningUpdate: Double = 0
    private var alignedAtS: Double?
    private var mapLoaded = false
    private var closingVideos: [Task<Void, Never>] = []
    private var roomplanJSON: Data?
    private var roomplanUSDZ: URL?

    static var isSupported: Bool {
        ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth)
            && ARWorldTrackingConfiguration.supportsSceneReconstruction(.meshWithClassification)
    }

    override init() {
        super.init()
        session.delegate = self
        session.delegateQueue = queue
    }

    /// Starts tracking (not recording yet). With a world map, ARKit relocalises into the project's frame.
    func prepare(quality: Quality, worldMap: ARWorldMap?) {
        self.quality = quality
        let config = ARWorldTrackingConfiguration()
        config.sceneReconstruction = .meshWithClassification
        config.frameSemantics = [.sceneDepth]
        config.environmentTexturing = .none
        if quality == .maximum, let format = ARWorldTrackingConfiguration.recommendedVideoFormatFor4KResolution {
            config.videoFormat = format
        }
        config.initialWorldMap = worldMap
        session.run(config, options: [.resetTracking, .removeExistingAnchors])
        DispatchQueue.main.async {
            self.usesWorldMap = worldMap != nil
            self.aligned = worldMap == nil
        }
        queue.async {
            self.alignedAtS = nil
            self.mapLoaded = worldMap != nil
        }
    }

    func stop() {
        session.pause()
        motion.stopDeviceMotionUpdates()
    }

    // MARK: - recording controls (called from the UI)

    func start(project: Project, pass: Manifest.PassInfo) throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("pass-\(pass.id)")
        try? FileManager.default.removeItem(at: dir)
        let rec = try PassRecorder(
            dir: dir, project: .init(id: project.id, name: project.name), pass: pass, quality: quality,
            depthSize: (256, 192),
            app: .init(version: Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0"),
            device: .init(model: Self.deviceModel(), system: "iOS \(UIDevice.current.systemVersion)"))
        queue.sync {
            recorder = rec
            roomplanJSON = nil
            roomplanUSDZ = nil
            try? rec.start(at: lastT)
            openVideo(segment: 0)
            lastFrameT = -1
        }
        startMotion()
        publish()
    }

    func pause() {
        queue.sync {
            try? recorder?.pause(at: lastT)
            closeVideo()
        }
        motion.stopDeviceMotionUpdates()
        publish()
    }

    func resume() {
        queue.sync {
            try? recorder?.resume(at: lastT)
            if case .recording(let seg) = recorder?.state { openVideo(segment: seg) }
        }
        startMotion()
        publish()
    }

    func discard(seconds: Double) {
        queue.sync { try? recorder?.discardLast(seconds: seconds, now: lastT) }
        publish()
    }

    func attachRoomPlan(json: Data, usdz: URL?) {
        queue.sync {
            roomplanJSON = json
            roomplanUSDZ = usdz
        }
    }

    /// Ends the pass: closes video, stores the mesh and (for a project's first pass) the world map,
    /// writes the `.tscan` into `archive`.
    func finish(to archive: URL, includeWorldMap: Bool) async throws -> FinishedPass {
        motion.stopDeviceMotionUpdates()
        let worldMap: Data? = includeWorldMap ? await currentWorldMap() : nil
        let (finishVideo, closing): (VideoWriter?, [Task<Void, Never>]) = queue.sync {
            let v = video
            video = nil
            let c = closingVideos
            closingVideos = []
            return (v, c)
        }
        await finishVideo?.finish()
        for task in closing { await task.value }  // videos of earlier segments must be complete
        return try queue.sync {
            guard let rec = recorder else { throw RecorderError.invalidTransition(from: .idle, action: "finish") }
            if case .recording = rec.state { try rec.pause(at: lastT) }
            let ply = meshPLY()
            if let ply, let last = rec.manifest.segments.indices.last { try rec.setMesh(segment: last, ply: ply) }
            try rec.setWorldMap(
                worldMap, id: rec.manifest.project.id, aligned: alignedAtS != nil || worldMap != nil,
                alignedAtS: alignedAtS)
            if let json = roomplanJSON { try rec.setRoomPlan(json: json, usdz: roomplanUSDZ) }
            let manifest = try rec.finish(at: lastT)
            try FileManager.default.createDirectory(
                at: archive.deletingLastPathComponent(), withIntermediateDirectories: true)
            try rec.export(to: archive)
            try? FileManager.default.removeItem(at: rec.dir)
            recorder = nil
            DispatchQueue.main.async { self.state = .finished }
            return FinishedPass(archive: archive, manifest: manifest, previewPLY: ply, worldMap: worldMap)
        }
    }

    // MARK: - ARSessionDelegate (on `queue`)

    func session(_ session: ARSession, didUpdate frame: ARFrame) {
        let t = frame.timestamp
        lastT = t
        updateAlignment(frame)
        if t - lastWarningUpdate > 0.5 {
            lastWarningUpdate = t
            let w = warnings(for: frame)
            DispatchQueue.main.async { self.warnings = w }
        }
        guard let rec = recorder, case .recording = rec.state else { return }
        // Keep the preset's frame rate (ARKit delivers 60 fps).
        guard lastFrameT < 0 || t - lastFrameT >= 1.0 / Double(quality.frameRate) - 0.004 else { return }
        lastFrameT = t
        var depth: [UInt16]?
        var confidence: [UInt8]?
        if let d = frame.sceneDepth {
            depth = Self.float16Depth(d.depthMap)
            confidence = d.confidenceMap.map(Self.bytes)
        }
        do {
            try rec.append(
                t: t, pose: Self.columns(frame.camera.transform), intrinsics: Self.columns(frame.camera.intrinsics),
                exposureS: Float(frame.camera.exposureDuration), tracking: Self.tracking(frame.camera.trackingState),
                depth: depth, confidence: confidence)
        } catch {
            return
        }
        video?.append(frame.capturedImage, at: t)
        if rec.framesWritten % 10 == 0 { publish() }
    }

    private func updateAlignment(_ frame: ARFrame) {
        guard alignedAtS == nil else { return }
        // With a world map, tracking only becomes "normal" after ARKit relocalised into it.
        if mapLoaded, case .normal = frame.camera.trackingState {
            alignedAtS = frame.timestamp
            DispatchQueue.main.async { self.aligned = true }
        }
    }

    private func warnings(for frame: ARFrame) -> Set<CaptureWarning> {
        var w: Set<CaptureWarning> = []
        switch frame.camera.trackingState {
        case .notAvailable: w.insert(.trackingLost)
        case .limited(let reason):
            switch reason {
            case .excessiveMotion: w.insert(.tooFast)
            case .insufficientFeatures: w.insert(.lowLight)
            case .relocalizing: w.insert(.relocalizing)
            default: w.insert(.trackingLost)
            }
        case .normal: break
        }
        if let light = frame.lightEstimate, light.ambientIntensity < 250 { w.insert(.lowLight) }
        if let d = frame.sceneDepth, Self.medianDepth(d.depthMap) > 4 { w.insert(.tooFar) }
        return w
    }

    private func publish() {
        let s = recorder?.state ?? .idle
        let n = recorder?.framesWritten ?? 0
        let kept = recorder?.keptSeconds ?? 0
        let elapsed = recorder?.manifest.segments.reduce(0) { $0 + ($1.endS - $1.startS) } ?? 0
        DispatchQueue.main.async {
            self.state = s
            self.frames = n
            self.keptS = kept
            self.elapsedS = elapsed
        }
    }

    // MARK: - video, motion, mesh, world map

    private func openVideo(segment: Int) {
        guard let rec = recorder else { return }
        video = VideoWriter(url: rec.url(PassRecorder.videoPath(segment: segment)), quality: quality)
    }

    private func closeVideo() {
        guard let v = video else { return }
        video = nil
        closingVideos.append(Task { await v.finish() })
    }

    private func startMotion() {
        guard motion.isDeviceMotionAvailable else { return }
        motion.deviceMotionUpdateInterval = 1.0 / 100
        let q = OperationQueue()
        q.underlyingQueue = queue
        motion.startDeviceMotionUpdates(to: q) { [weak self] m, _ in
            guard let self, let m, let rec = self.recorder, case .recording = rec.state else { return }
            try? rec.appendImu(
                .init(
                    t: m.timestamp,
                    gravity: [Float(m.gravity.x), Float(m.gravity.y), Float(m.gravity.z)],
                    acceleration: [Float(m.userAcceleration.x), Float(m.userAcceleration.y), Float(m.userAcceleration.z)],
                    rotationRate: [Float(m.rotationRate.x), Float(m.rotationRate.y), Float(m.rotationRate.z)]))
        }
    }

    private func currentWorldMap() async -> Data? {
        await withCheckedContinuation { cont in
            session.getCurrentWorldMap { map, _ in
                cont.resume(returning: map.flatMap { try? NSKeyedArchiver.archivedData(withRootObject: $0, requiringSecureCoding: true) })
            }
        }
    }

    /// All mesh anchors, in world coordinates, as one PLY with per-face classification.
    private func meshPLY() -> Data? {
        let anchors = session.currentFrame?.anchors.compactMap { $0 as? ARMeshAnchor } ?? []
        guard !anchors.isEmpty else { return nil }
        var vertices: [SIMD3<Float>] = []
        var faces: [SIMD3<UInt32>] = []
        var classes: [UInt8] = []
        for anchor in anchors {
            let g = anchor.geometry
            let base = UInt32(vertices.count)
            let vbuf = g.vertices.buffer.contents()
            for i in 0..<g.vertices.count {
                let p = vbuf.advanced(by: g.vertices.offset + g.vertices.stride * i).assumingMemoryBound(to: Float.self)
                let world = anchor.transform * SIMD4<Float>(p[0], p[1], p[2], 1)
                vertices.append([world.x, world.y, world.z])
            }
            let fbuf = g.faces.buffer.contents()
            let cls = g.classification
            for f in 0..<g.faces.count {
                var idx: [UInt32] = []
                for k in 0..<3 {
                    let off = (f * 3 + k) * g.faces.bytesPerIndex
                    let v: UInt32 =
                        g.faces.bytesPerIndex == 4
                        ? fbuf.load(fromByteOffset: off, as: UInt32.self)
                        : UInt32(fbuf.load(fromByteOffset: off, as: UInt16.self))
                    idx.append(base + v)
                }
                faces.append([idx[0], idx[1], idx[2]])
                if let cls {
                    let c = cls.buffer.contents().load(fromByteOffset: cls.offset + cls.stride * f, as: UInt8.self)
                    classes.append(c)
                } else {
                    classes.append(0)
                }
            }
        }
        return PLY.encode(vertices: vertices, faces: faces, classification: classes)
    }

    // MARK: - conversions

    static func columns(_ m: simd_float4x4) -> [Float] {
        [m.columns.0, m.columns.1, m.columns.2, m.columns.3].flatMap { [$0.x, $0.y, $0.z, $0.w] }
    }

    static func columns(_ m: simd_float3x3) -> [Float] {
        [m.columns.0, m.columns.1, m.columns.2].flatMap { [$0.x, $0.y, $0.z] }
    }

    static func tracking(_ s: ARCamera.TrackingState) -> TrackingState {
        switch s {
        case .notAvailable: return .notAvailable
        case .limited: return .limited
        case .normal: return .normal
        }
    }

    static func float16Depth(_ buf: CVPixelBuffer) -> [UInt16] {
        CVPixelBufferLockBaseAddress(buf, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(buf, .readOnly) }
        let w = CVPixelBufferGetWidth(buf)
        let h = CVPixelBufferGetHeight(buf)
        let row = CVPixelBufferGetBytesPerRow(buf)
        guard let base = CVPixelBufferGetBaseAddress(buf) else { return [] }
        var out = [UInt16](repeating: 0, count: w * h)
        for y in 0..<h {
            let p = base.advanced(by: y * row).assumingMemoryBound(to: Float32.self)
            for x in 0..<w { out[y * w + x] = Float16(p[x]).bitPattern }
        }
        return out
    }

    static func bytes(_ buf: CVPixelBuffer) -> [UInt8] {
        CVPixelBufferLockBaseAddress(buf, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(buf, .readOnly) }
        let w = CVPixelBufferGetWidth(buf)
        let h = CVPixelBufferGetHeight(buf)
        let row = CVPixelBufferGetBytesPerRow(buf)
        guard let base = CVPixelBufferGetBaseAddress(buf) else { return [] }
        var out = [UInt8](repeating: 0, count: w * h)
        for y in 0..<h {
            let p = base.advanced(by: y * row).assumingMemoryBound(to: UInt8.self)
            for x in 0..<w { out[y * w + x] = p[x] }
        }
        return out
    }

    static func medianDepth(_ buf: CVPixelBuffer) -> Float {
        CVPixelBufferLockBaseAddress(buf, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(buf, .readOnly) }
        let w = CVPixelBufferGetWidth(buf)
        let h = CVPixelBufferGetHeight(buf)
        let row = CVPixelBufferGetBytesPerRow(buf)
        guard let base = CVPixelBufferGetBaseAddress(buf) else { return 0 }
        var samples: [Float] = []
        for y in stride(from: 0, to: h, by: 16) {
            let p = base.advanced(by: y * row).assumingMemoryBound(to: Float32.self)
            for x in stride(from: 0, to: w, by: 16) { samples.append(p[x]) }
        }
        samples.sort()
        return samples.isEmpty ? 0 : samples[samples.count / 2]
    }

    static func deviceModel() -> String {
        var info = utsname()
        uname(&info)
        return withUnsafeBytes(of: &info.machine) { String(decoding: $0.prefix { $0 != 0 }, as: UTF8.self) }
    }
}
