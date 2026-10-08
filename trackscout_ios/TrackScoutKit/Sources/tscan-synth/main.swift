import Foundation
import TrackScoutKit

// Writes the synthetic test pass: `tscan-synth <out.tscan>` (spec 0007 AC3, used by CI and pytest).
let args = CommandLine.arguments
guard args.count == 2 else {
    FileHandle.standardError.write(Data("usage: tscan-synth <out.tscan>\n".utf8))
    exit(2)
}
let out = URL(fileURLWithPath: args[1])
let work = FileManager.default.temporaryDirectory.appendingPathComponent("tscan-synth-\(UUID().uuidString)")
try Synthetic.makePass(in: work, archive: out)
try? FileManager.default.removeItem(at: work)
print(out.path)
