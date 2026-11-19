import Foundation

enum ReleaseNotesLink {
    private static let prefix = "https://github.com/SAFuDarren/Trellin/releases/tag/"

    static func isOfficial(_ url: URL) -> Bool {
        let address = url.absoluteString
        guard address.hasPrefix(prefix) else { return false }
        var tag = address.dropFirst(prefix.count)
        if tag.first == "v" { tag.removeFirst() }
        let parts = tag.split(separator: ".", omittingEmptySubsequences: false)
        return parts.count == 3 && parts.allSatisfy { part in
            (1...9).contains(part.utf8.count) && (part.utf8.count == 1 || part.first != "0")
                && part.utf8.allSatisfy { (48...57).contains($0) }
        }
    }
}
