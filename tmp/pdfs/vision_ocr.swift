import AppKit
import Foundation
import Vision

for path in CommandLine.arguments.dropFirst() {
    guard let image = NSImage(contentsOfFile: path),
          let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        fputs("Could not load \(path)\n", stderr)
        continue
    }

    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["ru-RU", "en-US"]
    request.usesLanguageCorrection = true

    do {
        try VNImageRequestHandler(cgImage: cgImage, options: [:]).perform([request])
        let observations = (request.results ?? []).sorted {
            if abs($0.boundingBox.maxY - $1.boundingBox.maxY) > 0.015 {
                return $0.boundingBox.maxY > $1.boundingBox.maxY
            }
            return $0.boundingBox.minX < $1.boundingBox.minX
        }
        print("=== \(URL(fileURLWithPath: path).lastPathComponent) ===")
        for observation in observations {
            guard let candidate = observation.topCandidates(1).first else { continue }
            let b = observation.boundingBox
            print(String(format: "%.4f\t%.4f\t%.4f\t%.4f\t%@", b.minX, b.minY, b.width, b.height, candidate.string))
        }
    } catch {
        fputs("OCR failed for \(path): \(error)\n", stderr)
    }
}
