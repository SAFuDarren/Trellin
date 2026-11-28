import Foundation
import PDFKit

private struct PreviewPage: Encodable {
    let page: Int
    let text: String
}

private struct Preview: Encodable {
    let status: String
    var page_count: Int?
    var pages_included_through: Int?
    var pages: [PreviewPage]?
}

@main
private enum PDFPreview {
    static let inputLimit = 16 * 1024 * 1024

    // stdin is the verified byte stream from the source reader. Read at most
    // one extra byte to distinguish an exact-limit document from an oversized one.
    static func readInput() throws -> Data? {
        var input = Data()
        while input.count <= inputLimit {
            let count = min(64 * 1024, inputLimit + 1 - input.count)
            guard let chunk = try FileHandle.standardInput.read(upToCount: count), !chunk.isEmpty else {
                return input
            }
            input.append(chunk)
        }
        return nil
    }

    static func preview() throws -> Preview {
        let arguments = CommandLine.arguments
        guard arguments.count == 2, arguments[1].utf8.allSatisfy({ (48...57).contains($0) }),
              let budget = Int(arguments[1]), (1...18_000).contains(budget),
              let input = try readInput(), !input.isEmpty,
              let document = PDFDocument(data: input) else {
            return Preview(status: "pdf_invalid")
        }
        guard !document.isLocked else { return Preview(status: "pdf_locked") }
        let count = document.pageCount
        guard count > 0 else { return Preview(status: "pdf_invalid") }
        guard count <= 128 else { return Preview(status: "pdf_page_limit") }

        var remaining = budget
        var pages: [PreviewPage] = []
        var through = count
        let content = CharacterSet.whitespacesAndNewlines.inverted
        for index in 0..<count {
            guard let text = document.page(at: index)?.string,
                  text.rangeOfCharacter(from: content) != nil, !text.utf8.contains(0) else { continue }
            let bytes = text.utf8.count
            if bytes > remaining {
                guard !pages.isEmpty else { return Preview(status: "pdf_text_limit") }
                through = index
                break
            }
            remaining -= bytes
            pages.append(PreviewPage(page: index + 1, text: text))
        }
        guard !pages.isEmpty else { return Preview(status: "pdf_no_text") }
        return Preview(status: "available", page_count: count, pages_included_through: through, pages: pages)
    }

    static func main() {
        let fallback = Data(#"{"status":"pdf_invalid"}"#.utf8)
        let output = (try? JSONEncoder().encode(preview())) ?? fallback
        FileHandle.standardOutput.write(output)
    }
}
