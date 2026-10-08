import Compression
import CryptoKit
import Foundation

/// Little-endian byte buffer for the `.tscan` binary streams.
public struct LEWriter {
    public private(set) var data = Data()
    public init() {}

    public mutating func u8(_ v: UInt8) { data.append(v) }
    public mutating func u16(_ v: UInt16) { withUnsafeBytes(of: v.littleEndian) { data.append(contentsOf: $0) } }
    public mutating func u32(_ v: UInt32) { withUnsafeBytes(of: v.littleEndian) { data.append(contentsOf: $0) } }
    public mutating func u64(_ v: UInt64) { withUnsafeBytes(of: v.littleEndian) { data.append(contentsOf: $0) } }
    public mutating func f32(_ v: Float) { u32(v.bitPattern) }
    public mutating func f64(_ v: Double) { u64(v.bitPattern) }
    public mutating func bytes(_ d: Data) { data.append(d) }
}

/// One camera frame of a pass. Pose: camera → world, column-major 4×4 (ARKit `simd_float4x4` order).
public struct FrameRecord: Equatable, Sendable {
    public var t: Double
    public var segment: UInt16
    public var tracking: TrackingState
    public var hasDepth: Bool
    public var pose: [Float]  // 16, column-major
    public var intrinsics: [Float]  // 9, column-major
    public var exposureS: Float

    public static let byteSize = 8 + 2 + 1 + 1 + 64 + 36 + 4  // 116

    public init(
        t: Double, segment: UInt16, tracking: TrackingState, hasDepth: Bool, pose: [Float],
        intrinsics: [Float], exposureS: Float
    ) {
        precondition(pose.count == 16 && intrinsics.count == 9)
        self.t = t
        self.segment = segment
        self.tracking = tracking
        self.hasDepth = hasDepth
        self.pose = pose
        self.intrinsics = intrinsics
        self.exposureS = exposureS
    }

    public func encode(into w: inout LEWriter) {
        w.f64(t)
        w.u16(segment)
        w.u8(tracking.rawValue)
        w.u8(hasDepth ? 1 : 0)
        pose.forEach { w.f32($0) }
        intrinsics.forEach { w.f32($0) }
        w.f32(exposureS)
    }
}

public enum TrackingState: UInt8, Sendable {
    case notAvailable = 0, limited = 1, normal = 2
}

/// IMU sample: gravity, user acceleration (g), rotation rate (rad/s).
public struct ImuRecord: Sendable {
    public var t: Double
    public var gravity: SIMD3<Float>
    public var acceleration: SIMD3<Float>
    public var rotationRate: SIMD3<Float>
    public static let byteSize = 8 + 36

    public init(t: Double, gravity: SIMD3<Float>, acceleration: SIMD3<Float>, rotationRate: SIMD3<Float>) {
        self.t = t
        self.gravity = gravity
        self.acceleration = acceleration
        self.rotationRate = rotationRate
    }

    public func encode(into w: inout LEWriter) {
        w.f64(t)
        for v in [gravity, acceleration, rotationRate] {
            w.f32(v.x)
            w.f32(v.y)
            w.f32(v.z)
        }
    }
}

/// IEEE 754 half precision bits (portable: `Float16` is not available on Intel Macs).
public func float16Bits(_ value: Float) -> UInt16 {
    let bits = value.bitPattern
    let sign = UInt16((bits >> 16) & 0x8000)
    let exp = Int((bits >> 23) & 0xFF) - 127 + 15
    var mant = bits & 0x7F_FFFF
    if value.isNaN { return sign | 0x7E00 }
    if exp >= 31 { return sign | 0x7C00 }  // overflow → inf
    if exp <= 0 {  // subnormal or zero
        if exp < -10 { return sign }
        mant |= 0x80_0000
        let shift = UInt32(14 - exp)
        let half = UInt16(mant >> shift)
        let round = (mant >> (shift - 1)) & 1
        return sign | (half + UInt16(round))
    }
    let half = sign | UInt16(exp << 10) | UInt16(mant >> 13)
    return (mant & 0x1000) != 0 ? half + 1 : half  // round half up
}

public enum DepthCodec {
    /// One depth record: u32 length + raw-deflate(float16 depth ‖ uint8 confidence).
    public static func encode(depth: [UInt16], confidence: [UInt8]) -> Data {
        precondition(depth.count == confidence.count)
        var raw = Data(capacity: depth.count * 3)
        depth.withUnsafeBufferPointer { buf in
            for v in buf { withUnsafeBytes(of: v.littleEndian) { raw.append(contentsOf: $0) } }
        }
        raw.append(contentsOf: confidence)
        let packed = deflate(raw)
        var w = LEWriter()
        w.u32(UInt32(packed.count))
        w.bytes(packed)
        return w.data
    }

    /// Raw DEFLATE (RFC 1951; Python: `zlib.decompress(data, -15)`).
    public static func deflate(_ input: Data) -> Data {
        let capacity = input.count + input.count / 10 + 1024
        var out = Data(count: capacity)
        let n = out.withUnsafeMutableBytes { dst in
            input.withUnsafeBytes { src in
                compression_encode_buffer(
                    dst.bindMemory(to: UInt8.self).baseAddress!, capacity,
                    src.bindMemory(to: UInt8.self).baseAddress!, input.count, nil, COMPRESSION_ZLIB)
            }
        }
        precondition(n > 0 || input.isEmpty, "deflate failed")
        return out.prefix(n)
    }

    public static func inflate(_ input: Data, expectedSize: Int) -> Data? {
        var out = Data(count: expectedSize)
        let n = out.withUnsafeMutableBytes { dst in
            input.withUnsafeBytes { src in
                compression_decode_buffer(
                    dst.bindMemory(to: UInt8.self).baseAddress!, expectedSize,
                    src.bindMemory(to: UInt8.self).baseAddress!, input.count, nil, COMPRESSION_ZLIB)
            }
        }
        return n == expectedSize ? out : nil
    }
}

public enum PLY {
    /// Binary little-endian PLY with per-face ARKit classification (`ARMeshClassification` raw values).
    public static func encode(vertices: [SIMD3<Float>], faces: [SIMD3<UInt32>], classification: [UInt8]) -> Data {
        precondition(faces.count == classification.count)
        let header = """
            ply
            format binary_little_endian 1.0
            comment TrackScout mesh, ARKit world frame (Y-up, metres)
            element vertex \(vertices.count)
            property float x
            property float y
            property float z
            element face \(faces.count)
            property list uchar uint vertex_indices
            property uchar classification
            end_header

            """
        var w = LEWriter()
        w.bytes(Data(header.utf8))
        for v in vertices {
            w.f32(v.x)
            w.f32(v.y)
            w.f32(v.z)
        }
        for (f, c) in zip(faces, classification) {
            w.u8(3)
            w.u32(f.x)
            w.u32(f.y)
            w.u32(f.z)
            w.u8(c)
        }
        return w.data
    }
}

extension PLY {
    public struct Mesh: Equatable, Sendable {
        public var vertices: [SIMD3<Float>]
        public var faces: [SIMD3<UInt32>]
        public var classification: [UInt8]
    }

    /// Reads meshes written by `encode` (for the on-phone preview).
    public static func decode(_ data: Data) -> Mesh? {
        guard let end = data.range(of: Data("end_header\n".utf8)) else { return nil }
        let header = String(decoding: data[..<end.lowerBound], as: UTF8.self).split(separator: "\n")
        var nv = 0
        var nf = 0
        for line in header {
            let p = line.split(separator: " ")
            if p.count == 3, p[0] == "element" {
                if p[1] == "vertex" { nv = Int(p[2]) ?? 0 }
                if p[1] == "face" { nf = Int(p[2]) ?? 0 }
            }
        }
        let body = Data(data[end.upperBound...])
        guard body.count >= nv * 12 + nf * 14 else { return nil }
        func f32(_ o: Int) -> Float { Float(bitPattern: u32(o)) }
        func u32(_ o: Int) -> UInt32 {
            body.withUnsafeBytes { UInt32(littleEndian: $0.loadUnaligned(fromByteOffset: o, as: UInt32.self)) }
        }
        var mesh = Mesh(vertices: [], faces: [], classification: [])
        mesh.vertices.reserveCapacity(nv)
        for i in 0..<nv { mesh.vertices.append([f32(i * 12), f32(i * 12 + 4), f32(i * 12 + 8)]) }
        for i in 0..<nf {
            let o = nv * 12 + i * 14
            guard body[o] == 3 else { return nil }
            mesh.faces.append([u32(o + 1), u32(o + 5), u32(o + 9)])
            mesh.classification.append(body[o + 13])
        }
        return mesh
    }
}

public enum Checksum {
    public static func sha256(file url: URL) throws -> (hex: String, size: Int64) {
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        var hasher = SHA256()
        var size: Int64 = 0
        while let chunk = try handle.read(upToCount: 1 << 20), !chunk.isEmpty {
            hasher.update(data: chunk)
            size += Int64(chunk.count)
        }
        return (hasher.finalize().map { String(format: "%02x", $0) }.joined(), size)
    }

    public static func sha256(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }
}
