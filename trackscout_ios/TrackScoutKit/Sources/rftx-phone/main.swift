import Foundation
import TrackScoutKit

// The real phone-side transfer session over stdin/stdout, so pytest can run the Python laptop code against the
// Swift phone code (spec 0007 AC9). Each GATT value is framed as u16 big-endian length + bytes.
// Usage: rftx-phone <pairing-key> <max-payload> <pass.tscan>...   (pass id = file name without extension)
let args = CommandLine.arguments
guard args.count >= 4, let maxPayload = Int(args[2]) else {
    FileHandle.standardError.write(Data("usage: rftx-phone <key> <max-payload> <pass.tscan>...\n".utf8))
    exit(2)
}
let key = args[1]
let passes = try args[3...].map { path -> (offer: OfferedPass, url: URL) in
    let url = URL(fileURLWithPath: path)
    let id = url.deletingPathExtension().lastPathComponent
    let offer = try OfferedPass(
        id: id, file: url, relativePath: url.lastPathComponent, project: "Corridor", passType: "walkthrough",
        createdAt: Date(timeIntervalSince1970: 0), key: key)
    return (offer, url)
}

let out = FileHandle.standardOutput
let session = PhoneTransferSession(
    key: key, passes: passes, maxPayload: maxPayload,
    send: { fragment in
        var len = UInt16(fragment.count).bigEndian
        out.write(Data(bytes: &len, count: 2) + fragment)
    },
    events: { event in
        if case .delivered(let id) = event { FileHandle.standardError.write(Data("delivered \(id)\n".utf8)) }
        if case .rejected = event { FileHandle.standardError.write(Data("rejected\n".utf8)) }
    })

func readExactly(_ n: Int) -> Data? {
    var data = Data()
    while data.count < n {
        let piece = FileHandle.standardInput.readData(ofLength: n - data.count)
        if piece.isEmpty { return nil }
        data += piece
    }
    return data
}

try await session.start()
while let head = readExactly(2) {
    let n = Int(head[head.startIndex]) << 8 | Int(head[head.startIndex + 1])
    guard let fragment = readExactly(n) else { break }
    try await session.receive(fragment)
}
