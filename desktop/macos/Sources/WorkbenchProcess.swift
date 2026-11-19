import Foundation

struct WorkbenchPosition: Codable, Equatable {
    let claimId: UUID
    let nodeId: String

    init?(message: Any) {
        guard let fields = message as? [String: String], fields.count == 2,
              let claimId = fields["claimId"].flatMap(UUID.init(uuidString:)),
              let nodeId = fields["nodeId"], nodeId.utf8.count <= 100,
              nodeId.range(of: "^[a-z-]+:[0-9a-f:-]+$", options: .regularExpression) != nil
        else { return nil }
        self.claimId = claimId
        self.nodeId = nodeId
    }

    var script: String {
        let position = ["claimId": claimId.uuidString.lowercased(), "nodeId": nodeId]
        guard let data = try? JSONEncoder().encode(position) else { return "" }
        return "window.trellinPosition = \(String(decoding: data, as: UTF8.self));"
    }
}

struct WorkbenchReadiness: Decodable {
    let version: Int
    let status: String
    let url: URL?
    let warnings: [String]?
    let code: String?
    let workspace: String?
    let name: String?

    var workspaceURL: URL? {
        localDirectoryURL(workspace)
    }

    var failure: WorkbenchStartFailure {
        guard version == 1, status == "error", let code else { return .startup }
        if code == "storage-project" { return .project }
        if code.hasPrefix("storage-") { return .library }
        return WorkbenchStartFailure(rawValue: code) ?? .startup
    }

    var sessionURL: URL? {
        guard version == 1, status == "ready", let url, url.scheme == "http",
              url.host == "127.0.0.1", let port = url.port, (1...65535).contains(port),
              url.user == nil, url.password == nil, url.path == "/", url.query == nil,
              let fragment = url.fragment,
              fragment.range(of: "^session=[A-Za-z0-9_-]{43}$", options: .regularExpression) != nil
        else { return nil }
        return url
    }
}

enum WorkbenchStartFailure: String {
    case workspace, unbound, postgres, schema, install, startup
    case loginMissing = "login-missing"
    case loginInvalid = "login-invalid"
    case workspaceOccupied = "workspace-occupied"
    case createPath = "create-path"
    case createPostgres = "create-postgres"
    case createFailed = "create-failed"
    case library, project

    var canRetry: Bool { self == .postgres || self == .createPostgres || self == .startup || self == .library }
}

func localDirectoryURL(_ path: String?) -> URL? {
    guard let path, path.hasPrefix("/"), !path.contains("\0") else { return nil }
    return URL(fileURLWithPath: path, isDirectory: true).standardizedFileURL.resolvingSymlinksInPath()
}

enum WorkbenchLaunch {
    case restore(URL)
    case select(URL)
    case library
    case newManaged(String)

    static let prefix = ["-I", "-B", "-m", "research_multiplier.workbench.desktop"]

    var arguments: [String] {
        switch self {
        case let .restore(workspace): return Self.prefix + [workspace.path]
        case let .select(folder): return Self.prefix + ["--select", folder.path]
        case .library: return Self.prefix + ["--library"]
        case let .newManaged(name): return Self.prefix + ["--new-managed", name]
        }
    }

    var location: URL? {
        switch self {
        case let .restore(url), let .select(url):
            return url.standardizedFileURL.resolvingSymlinksInPath()
        case .library, .newManaged:
            return nil
        }
    }

    var isRestore: Bool {
        if case .restore = self { return true }
        return false
    }
}

/// One child owns one project's loopback service and its in-memory session.
final class WorkbenchProcess {
    private let process = Process()
    private let lifetime = Pipe()
    private let response = Pipe()

    func start(runtime: URL, launch: WorkbenchLaunch, onReady: @escaping (WorkbenchReadiness?) -> Void,
               onExit: @escaping () -> Void) throws {
        process.executableURL = runtime
        process.arguments = launch.arguments
        process.environment = Self.environment(ProcessInfo.processInfo.environment, home: NSHomeDirectory())
        process.standardInput = lifetime
        process.standardOutput = response
        process.standardError = FileHandle.nullDevice
        process.terminationHandler = { _ in DispatchQueue.main.async(execute: onExit) }
        try process.run()
        let reader = response.fileHandleForReading
        DispatchQueue.global(qos: .userInitiated).async {
            let readiness = Self.readiness(from: reader)
            try? reader.close()
            DispatchQueue.main.async { onReady(readiness) }
        }
    }

    func stop() {
        // EOF stops accepting connections and lets the server drain current requests.
        try? lifetime.fileHandleForWriting.close()
    }

    func waitForExit(_ completion: @escaping () -> Void) {
        DispatchQueue.global(qos: .userInitiated).async { [process] in
            process.waitUntilExit()
            DispatchQueue.main.async(execute: completion)
        }
    }

    func cancelStartup() {
        stop()
        if process.isRunning { process.terminate() }
    }

    static func environment(_ base: [String: String], home: String) -> [String: String] {
        var environment = base.filter { key, _ in
            !(key.hasPrefix("RM_") && key.hasSuffix("_DSN")) && key != "RM_DATA_ROOT"
        }
        // Finder launches without the user's shell startup files.
        let defaults = ["\(home)/.elan/bin", "/opt/homebrew/opt/postgresql@18/bin", "/opt/homebrew/bin",
                        "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
        let inherited = (base["PATH"] ?? "").components(separatedBy: ":").filter { $0.hasPrefix("/") }
        environment["PATH"] = (defaults + inherited.filter { !defaults.contains($0) }).joined(separator: ":")
        return environment
    }

    static func readiness(from file: FileHandle) -> WorkbenchReadiness? {
        var bytes = Data()
        // The child stays alive after sending the line. Read the currently
        // available pipe data instead of waiting to fill a fixed-size buffer.
        while bytes.count <= 4096 {
            let chunk = file.availableData
            if chunk.isEmpty { break }
            if let newline = chunk.firstIndex(of: 10) {
                bytes.append(chunk.prefix(upTo: newline))
                return bytes.count <= 4096 ? (try? JSONDecoder().decode(WorkbenchReadiness.self, from: bytes)) : nil
            }
            bytes.append(chunk)
        }
        return nil
    }
}
