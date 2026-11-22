import Foundation

/// What a status screen offers. The first action is the primary one and is the one that fixes the problem.
enum StatusAction: Hashable {
    case retry, chooseFolder, openLibrary, quit
}

enum StatusKind: Equatable {
    /// Work in progress: a spinner, no actions.
    case progress
    /// Nothing is open yet and the researcher picks what to open.
    case choose
    /// Something stopped the workbench. Explain the cause and offer the fix.
    case problem
}

/// One description of every native status screen (#286): the symbol for its kind of problem and the actions
/// that lead out of it. Every screen uses the same component, so they look like parts of the same product.
struct StatusPresentation: Equatable {
    let kind: StatusKind
    let symbol: String?
    let actions: [StatusAction]

    static let progressKeys: Set<String> = ["loading", "storage.moving", "upgrade.running"]

    static func of(_ key: String, canRetry: Bool) -> StatusPresentation {
        if progressKeys.contains(key) { return StatusPresentation(kind: .progress, symbol: nil, actions: []) }
        switch key {
        case "workspace":
            return StatusPresentation(kind: .choose, symbol: "folder", actions: [.chooseFolder, .openLibrary])
        case "install":
            // The app bundle is incomplete. Nothing inside it can fix that.
            return StatusPresentation(kind: .problem, symbol: "exclamationmark.triangle", actions: [.quit])
        case "library", "disconnected", "create-postgres":
            return StatusPresentation(kind: .problem, symbol: symbol(for: key), actions: [.retry])
        case "create-path", "create-failed", "workspace-occupied":
            // Only reached after the researcher chose a folder, so choosing another one is the fix.
            return StatusPresentation(kind: .problem, symbol: symbol(for: key), actions: [.chooseFolder, .openLibrary])
        default:
            if canRetry { return StatusPresentation(kind: .problem, symbol: symbol(for: key), actions: [.retry]) }
            // Opening arbitrary folders is an advanced tool. Ordinary problems lead back to the app's own library.
            return StatusPresentation(kind: .problem, symbol: symbol(for: key), actions: [.openLibrary])
        }
    }

    static func symbol(for key: String) -> String {
        switch key {
        case "postgres", "timeout", "create-postgres", "unbound", "login-missing", "login-invalid", "schema":
            return "cylinder.split.1x2"
        case "project", "create-path", "workspace-occupied", "workspace":
            return "folder.badge.questionmark"
        case "library":
            return "books.vertical"
        case "disconnected":
            return "arrow.triangle.2.circlepath"
        default:
            return "exclamationmark.triangle"
        }
    }
}
