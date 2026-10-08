import Foundation

public enum RecorderState: Equatable, Sendable {
    case idle
    case recording(segment: Int)
    case paused
    case finished
}

public enum RecorderError: Error, Equatable {
    case invalidTransition(from: RecorderState, action: String)
    case missingFile(String)
}

/// Records one pass into a working directory and packs it into a `.tscan` (spec 0007).
/// The camera/ARKit side (iOS app) feeds frames; this class owns segments, files and the manifest.
public final class PassRecorder {
    public let dir: URL
    public private(set) var state: RecorderState = .idle
    public private(set) var manifest: Manifest
    public private(set) var framesWritten = 0
    /// Depth frames stored, and frames whose depth was dropped because its resolution changed mid-pass.
    public private(set) var depthFramesWritten = 0
    public private(set) var depthFramesSkipped = 0
    private var frames: FileHandle?
    private var imu: FileHandle?
    private var depth: FileHandle?
    private var sinceDepth = 0
    private var lastT: Double = 0

    public init(
        dir: URL, project: Manifest.ProjectInfo, pass: Manifest.PassInfo, quality: Quality,
        depthSize: (width: Int, height: Int), app: Manifest.AppInfo, device: Manifest.DeviceInfo,
        createdAt: Date = Date()
    ) throws {
        self.dir = dir
        manifest = Manifest(
            app: app, device: device, project: project, pass: pass, quality: quality,
            createdAt: createdAt,
            worldMap: .init(id: nil, included: false, aligned: false, alignedAtS: nil),
            depth: .init(width: depthSize.width, height: depthSize.height, everyNthFrame: quality.depthEveryNthFrame),
            segments: [], roomplan: false, files: [:])
        let fm = FileManager.default
        for sub in ["depth", "video", "mesh"] {
            try fm.createDirectory(at: dir.appendingPathComponent(sub), withIntermediateDirectories: true)
        }
        frames = try Self.create(dir.appendingPathComponent("frames.bin"))
        imu = try Self.create(dir.appendingPathComponent("imu.bin"))
    }

    private static func create(_ url: URL) throws -> FileHandle {
        FileManager.default.createFile(atPath: url.path, contents: nil)
        return try FileHandle(forWritingTo: url)
    }

    /// Where the iOS app writes the RGB video of a segment (relative path in the archive).
    public static func videoPath(segment: Int) -> String { "video/\(segment).mov" }

    public func url(_ relative: String) -> URL { dir.appendingPathComponent(relative) }

    // MARK: - state machine

    public func start(at t: Double) throws {
        guard state == .idle else { throw RecorderError.invalidTransition(from: state, action: "start") }
        try openSegment(at: t)
    }

    public func pause(at t: Double) throws {
        guard case .recording = state else { throw RecorderError.invalidTransition(from: state, action: "pause") }
        try closeSegment(at: t)
        state = .paused
    }

    /// Continue after a pause: a new segment of the same pass (tracking kept running meanwhile).
    public func resume(at t: Double) throws {
        guard state == .paused else { throw RecorderError.invalidTransition(from: state, action: "resume") }
        try openSegment(at: t)
    }

    private func openSegment(at t: Double) throws {
        let index = manifest.segments.count
        let depthPath = "depth/\(index).bin"
        depth = try Self.create(url(depthPath))
        manifest.segments.append(
            .init(index: index, startS: t, endS: t, frames: 0, video: nil, depth: depthPath, mesh: nil, discarded: []))
        sinceDepth = 0
        state = .recording(segment: index)
    }

    private func closeSegment(at t: Double) throws {
        guard var seg = manifest.segments.last else { return }
        seg.endS = max(seg.endS, t)
        manifest.segments[manifest.segments.count - 1] = seg
        try depth?.close()
        depth = nil
    }

    /// Marks the last `seconds` of recording as discarded (the importer skips them). Works while recording
    /// or paused; the range may reach back into earlier segments.
    public func discardLast(seconds: Double, now t: Double) throws {
        guard state != .idle, state != .finished else {
            throw RecorderError.invalidTransition(from: state, action: "discard")
        }
        var recording = false
        if case .recording = state { recording = true }
        let end = recording ? t : (manifest.segments.last?.endS ?? t)
        let start = end - seconds
        let last = manifest.segments.count - 1
        for i in manifest.segments.indices {
            let seg = manifest.segments[i]
            let segEnd = (recording && i == last) ? end : seg.endS
            let lo = max(start, seg.startS)
            let hi = min(end, segEnd)
            if hi > lo { manifest.segments[i].discarded.append([lo, hi]) }
        }
    }

    /// Discarded intervals of all segments (for the UI: "recorded minutes kept").
    public var keptSeconds: Double {
        manifest.segments.reduce(0) { sum, s in
            sum + max(0, (s.endS - s.startS) - s.discarded.reduce(0) { $0 + max(0, min($1[1], s.endS) - $1[0]) })
        }
    }

    // MARK: - data

    /// Appends a camera frame; depth is kept for every n-th frame (quality preset). Returns whether depth was stored.
    ///
    /// `depthSize` is the real resolution of `depthValues`. ARKit's depth map size depends on the video format
    /// (e.g. 4K on "Maximum"), so the first stored depth frame defines the pass's resolution in the manifest;
    /// a later frame with another size loses its depth (counted in `depthFramesSkipped`) instead of crashing.
    @discardableResult
    public func append(
        t: Double, pose: [Float], intrinsics: [Float], exposureS: Float, tracking: TrackingState,
        depth depthValues: [UInt16]?, confidence: [UInt8]?, depthSize: (width: Int, height: Int)? = nil
    ) throws -> Bool {
        guard case .recording(let segment) = state else {
            throw RecorderError.invalidTransition(from: state, action: "append")
        }
        var storeDepth = false
        if let d = depthValues, let c = confidence {
            storeDepth = sinceDepth % manifest.depth.everyNthFrame == 0
            sinceDepth += 1
            if storeDepth, depthFramesWritten == 0, let size = depthSize, size.width * size.height == d.count {
                manifest.depth.width = size.width
                manifest.depth.height = size.height
            }
            let expected = manifest.depth.width * manifest.depth.height
            if storeDepth && (d.count != expected || c.count != expected) {
                storeDepth = false
                depthFramesSkipped += 1
            }
            if storeDepth {
                try depth?.write(contentsOf: DepthCodec.encode(depth: d, confidence: c))
                depthFramesWritten += 1
            }
        }
        var w = LEWriter()
        FrameRecord(
            t: t, segment: UInt16(segment), tracking: tracking, hasDepth: storeDepth, pose: pose,
            intrinsics: intrinsics, exposureS: exposureS
        ).encode(into: &w)
        try frames?.write(contentsOf: w.data)
        framesWritten += 1
        lastT = t
        manifest.segments[segment].frames += 1
        manifest.segments[segment].endS = t
        return storeDepth
    }

    public func appendImu(_ r: ImuRecord) throws {
        var w = LEWriter()
        r.encode(into: &w)
        try imu?.write(contentsOf: w.data)
    }

    public func setMesh(segment: Int, ply: Data) throws {
        let path = "mesh/\(segment).ply"
        try ply.write(to: url(path))
        manifest.segments[segment].mesh = path
    }

    public func setWorldMap(_ data: Data?, id: String, aligned: Bool, alignedAtS: Double?) throws {
        if let data {
            try data.write(to: url("worldmap.arworldmap"))
        }
        manifest.worldMap = .init(id: id, included: data != nil, aligned: aligned, alignedAtS: alignedAtS)
    }

    public func setRoomPlan(json: Data, usdz: URL?) throws {
        try json.write(to: url("roomplan.json"))
        if let usdz { try FileManager.default.copyItem(at: usdz, to: url("roomplan.usdz")) }
        manifest.roomplan = true
    }

    // MARK: - finish

    /// Closes the pass (pausing first if needed) and fills in videos and checksums.
    @discardableResult
    public func finish(at t: Double) throws -> Manifest {
        if case .recording = state { try pause(at: t) }
        guard state == .paused || state == .idle else {
            throw RecorderError.invalidTransition(from: state, action: "finish")
        }
        try frames?.close()
        try imu?.close()
        frames = nil
        imu = nil
        for i in manifest.segments.indices {
            let video = Self.videoPath(segment: i)
            if FileManager.default.fileExists(atPath: url(video).path) { manifest.segments[i].video = video }
        }
        var files: [String: Manifest.FileInfo] = [:]
        for path in archivePaths() {
            let (hex, size) = try Checksum.sha256(file: url(path))
            files[path] = .init(sha256: hex, size: size)
        }
        manifest.files = files
        state = .finished
        return manifest
    }

    private func archivePaths() -> [String] {
        var paths = ["frames.bin", "imu.bin"]
        for s in manifest.segments {
            paths.append(s.depth)
            if let v = s.video { paths.append(v) }
            if let m = s.mesh { paths.append(m) }
        }
        if manifest.worldMap.included { paths.append("worldmap.arworldmap") }
        if manifest.roomplan {
            paths.append("roomplan.json")
            if FileManager.default.fileExists(atPath: url("roomplan.usdz").path) { paths.append("roomplan.usdz") }
        }
        return paths
    }

    /// Writes the `.tscan` archive: manifest first, then every file listed in it.
    public func export(to archive: URL) throws {
        guard state == .finished else { throw RecorderError.invalidTransition(from: state, action: "export") }
        let zip = try ZipWriter(url: archive)
        try zip.add(path: "manifest.json", data: try ManifestCoding.encoder.encode(manifest))
        for path in manifest.files.keys.sorted() {
            guard FileManager.default.fileExists(atPath: url(path).path) else { throw RecorderError.missingFile(path) }
            try zip.add(path: path, from: url(path))
        }
        try zip.finish()
    }
}
