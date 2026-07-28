import AppKit
import Foundation
import Vision

guard CommandLine.arguments.count == 2 else {
    fputs("usage: vision_ocr IMAGE\n", stderr)
    exit(2)
}

let path = CommandLine.arguments[1]
guard let image = NSImage(contentsOfFile: path) else {
    fputs("cannot open image: \(path)\n", stderr)
    exit(3)
}
var rect = NSRect(origin: .zero, size: image.size)
guard let cgImage = image.cgImage(
    forProposedRect: &rect,
    context: nil,
    hints: nil
) else {
    fputs("cannot create CGImage: \(path)\n", stderr)
    exit(4)
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = ["zh-Hans", "en-US"]
request.usesLanguageCorrection = true
let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
try handler.perform([request])

let observations = request.results ?? []
let sorted = observations.sorted { left, right in
    let yDifference = abs(left.boundingBox.midY - right.boundingBox.midY)
    if yDifference > 0.012 {
        return left.boundingBox.midY > right.boundingBox.midY
    }
    return left.boundingBox.minX < right.boundingBox.minX
}
for observation in sorted {
    if let candidate = observation.topCandidates(1).first {
        print(candidate.string)
    }
}
