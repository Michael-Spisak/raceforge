import Foundation

/// CRC-32 (IEEE 802.3), as used by ZIP.
public struct CRC32 {
    private static let table: [UInt32] = (0..<256).map { i in
        var c = UInt32(i)
        for _ in 0..<8 { c = (c & 1) != 0 ? 0xEDB8_8320 ^ (c >> 1) : c >> 1 }
        return c
    }
    public private(set) var value: UInt32 = 0xFFFF_FFFF
    public init() {}

    public mutating func update(_ data: Data) {
        var c = value
        data.withUnsafeBytes { buf in
            for b in buf { c = Self.table[Int((c ^ UInt32(b)) & 0xFF)] ^ (c >> 8) }
        }
        value = c
    }

    public var checksum: UInt32 { value ^ 0xFFFF_FFFF }
}

/// Writes an uncompressed ("stored") ZIP64 archive by streaming files from disk.
/// Passes can exceed 4 GB, so every entry carries ZIP64 sizes and offsets.
public final class ZipWriter {
    private struct Entry {
        let name: Data
        let crc: UInt32
        let size: UInt64
        let offset: UInt64
    }

    private let handle: FileHandle
    private var entries: [Entry] = []
    private var offset: UInt64 = 0

    public init(url: URL) throws {
        FileManager.default.createFile(atPath: url.path, contents: nil)
        handle = try FileHandle(forWritingTo: url)
    }

    private func write(_ d: Data) throws {
        try handle.write(contentsOf: d)
        offset += UInt64(d.count)
    }

    private func localHeader(name: Data, crc: UInt32, size: UInt64) -> Data {
        var w = LEWriter()
        w.u32(0x0403_4B50)
        w.u16(45)  // version needed: ZIP64
        w.u16(0x0800)  // UTF-8 names
        w.u16(0)  // stored
        w.u16(0)
        w.u16(0x21)  // 1980-01-01
        w.u32(crc)
        w.u32(0xFFFF_FFFF)
        w.u32(0xFFFF_FFFF)
        w.u16(UInt16(name.count))
        w.u16(20)
        w.bytes(name)
        w.u16(0x0001)  // ZIP64 extra: original + compressed size
        w.u16(16)
        w.u64(size)
        w.u64(size)
        return w.data
    }

    /// Adds a file; the header's CRC is patched after streaming the data.
    public func add(path: String, from url: URL) throws {
        let name = Data(path.utf8)
        let start = offset
        let size = UInt64((try FileManager.default.attributesOfItem(atPath: url.path)[.size] as? NSNumber)?.int64Value ?? 0)
        try write(localHeader(name: name, crc: 0, size: size))
        var crc = CRC32()
        let input = try FileHandle(forReadingFrom: url)
        defer { try? input.close() }
        var copied: UInt64 = 0
        while let chunk = try input.read(upToCount: 1 << 20), !chunk.isEmpty {
            crc.update(chunk)
            try write(chunk)
            copied += UInt64(chunk.count)
        }
        precondition(copied == size, "\(path) changed while zipping")
        try handle.seek(toOffset: start + 14)
        var w = LEWriter()
        w.u32(crc.checksum)
        try handle.write(contentsOf: w.data)
        try handle.seek(toOffset: offset)
        entries.append(Entry(name: name, crc: crc.checksum, size: size, offset: start))
    }

    public func add(path: String, data: Data) throws {
        let name = Data(path.utf8)
        var crc = CRC32()
        crc.update(data)
        let start = offset
        try write(localHeader(name: name, crc: crc.checksum, size: UInt64(data.count)))
        try write(data)
        entries.append(Entry(name: name, crc: crc.checksum, size: UInt64(data.count), offset: start))
    }

    public func finish() throws {
        let cdStart = offset
        for e in entries {
            var w = LEWriter()
            w.u32(0x0201_4B50)
            w.u16(45)
            w.u16(45)
            w.u16(0x0800)
            w.u16(0)
            w.u16(0)
            w.u16(0x21)
            w.u32(e.crc)
            w.u32(0xFFFF_FFFF)
            w.u32(0xFFFF_FFFF)
            w.u16(UInt16(e.name.count))
            w.u16(28)
            w.u16(0)
            w.u16(0)
            w.u16(0)
            w.u32(0)
            w.u32(0xFFFF_FFFF)
            w.bytes(e.name)
            w.u16(0x0001)
            w.u16(24)
            w.u64(e.size)
            w.u64(e.size)
            w.u64(e.offset)
            try write(w.data)
        }
        let cdSize = offset - cdStart
        let eocd64 = offset
        var w = LEWriter()
        w.u32(0x0606_4B50)  // ZIP64 end of central directory
        w.u64(44)
        w.u16(45)
        w.u16(45)
        w.u32(0)
        w.u32(0)
        w.u64(UInt64(entries.count))
        w.u64(UInt64(entries.count))
        w.u64(cdSize)
        w.u64(cdStart)
        w.u32(0x0706_4B50)  // locator
        w.u32(0)
        w.u64(eocd64)
        w.u32(1)
        w.u32(0x0605_4B50)  // classic end record, all fields "see ZIP64"
        w.u16(0xFFFF)
        w.u16(0xFFFF)
        w.u16(0xFFFF)
        w.u16(0xFFFF)
        w.u32(0xFFFF_FFFF)
        w.u32(0xFFFF_FFFF)
        w.u16(0)
        try write(w.data)
        try handle.close()
    }
}
