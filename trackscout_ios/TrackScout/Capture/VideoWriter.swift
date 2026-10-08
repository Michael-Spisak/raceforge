import AVFoundation
import CoreVideo
import Foundation
import TrackScoutKit

/// HEVC video of one segment; presentation times are the ARFrame timestamps (same clock as frames.bin).
final class VideoWriter: @unchecked Sendable {
    private let url: URL
    private let quality: Quality
    private var writer: AVAssetWriter?
    private var input: AVAssetWriterInput?
    private var adaptor: AVAssetWriterInputPixelBufferAdaptor?

    init(url: URL, quality: Quality) {
        self.url = url
        self.quality = quality
    }

    private func setUp(width: Int, height: Int, at t: Double) {
        try? FileManager.default.removeItem(at: url)
        guard let w = try? AVAssetWriter(outputURL: url, fileType: .mov) else { return }
        let bitrate = quality == .maximum ? 40_000_000 : quality == .high ? 12_000_000 : 4_000_000
        let settings: [String: Any] = [
            AVVideoCodecKey: AVVideoCodecType.hevc, AVVideoWidthKey: width, AVVideoHeightKey: height,
            AVVideoCompressionPropertiesKey: [AVVideoAverageBitRateKey: bitrate],
        ]
        let input = AVAssetWriterInput(mediaType: .video, outputSettings: settings)
        input.expectsMediaDataInRealTime = true
        let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: nil)
        guard w.canAdd(input) else { return }
        w.add(input)
        w.startWriting()
        w.startSession(atSourceTime: CMTime(seconds: t, preferredTimescale: 600))
        writer = w
        self.input = input
        self.adaptor = adaptor
    }

    func append(_ buffer: CVPixelBuffer, at t: Double) {
        if writer == nil { setUp(width: CVPixelBufferGetWidth(buffer), height: CVPixelBufferGetHeight(buffer), at: t) }
        guard let input, let adaptor, input.isReadyForMoreMediaData else { return }  // drop rather than block
        adaptor.append(buffer, withPresentationTime: CMTime(seconds: t, preferredTimescale: 600))
    }

    func finish() async {
        guard let writer, writer.status == .writing else { return }
        input?.markAsFinished()
        await writer.finishWriting()
    }
}
