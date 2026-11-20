import Foundation

func sameStorageDestination(_ first: URL, _ second: URL) -> Bool {
    func normalizedPath(_ url: URL) -> String? {
        let leaf = url.lastPathComponent
        guard url.isFileURL, !leaf.isEmpty, leaf != ".", leaf != "..", !leaf.contains("/"),
              let parent = realpath(url.deletingLastPathComponent().path, nil) else { return nil }
        defer { free(parent) }
        // Resolve the existing parent so aliases compare even before the move creates the leaf.
        return URL(fileURLWithPath: String(cString: parent), isDirectory: true)
            .appendingPathComponent(leaf, isDirectory: true).path
    }
    guard let path = normalizedPath(first) else { return false }
    return path == normalizedPath(second)
}

enum StorageCommand {
    case information
    case resolve(UUID)
    case preview(URL)
    case move(StoragePlan)
    case upgrade(URL)

    var timeout: TimeInterval {
        switch self {
        case .information, .resolve: return 15
        case .preview: return 300
        case .move, .upgrade: return 1_800
        }
    }

    var arguments: [String] {
        let options: [String]
        switch self {
        case .information: options = ["--storage-info"]
        case let .resolve(id): options = ["--storage-resolve", id.uuidString.lowercased()]
        case let .preview(destination): options = ["--storage-preview", destination.path]
        case let .move(plan): options = ["--storage-move", plan.destination.path, plan.confirmation]
        case let .upgrade(workspace): options = ["--upgrade-project", workspace.path]
        }
        return WorkbenchLaunch.prefix + options
    }
}

enum DesktopProjectsRequest: Equatable {
    case list
    case open(UUID)
    case newProject
    case folder

    init?(message: Any) {
        guard let fields = message as? [String: String] else { return nil }
        switch fields["action"] {
        case "list" where fields.count == 1: self = .list
        case "new" where fields.count == 1: self = .newProject
        case "folder" where fields.count == 1: self = .folder
        case "open" where fields.count == 2:
            guard let value = fields["id"], let id = UUID(uuidString: value) else { return nil }
            self = .open(id)
        default: return nil
        }
    }
}

struct StorageProject: Decodable {
    let id: UUID
    let name: String
    let path: String

    var workspace: URL? { localDirectoryURL(path) }

    var sidebarEntry: [String: Any] {
        var directory: ObjCBool = false
        let available = workspace.map {
            FileManager.default.fileExists(atPath: $0.path, isDirectory: &directory) && directory.boolValue
        } ?? false
        return ["id": id.uuidString.lowercased(), "name": name, "available": available]
    }
}

struct StoragePlan {
    let root: URL
    let destination: URL
    let confirmation: String
    let projectCount: Int
    let files: Int
    let bytes: Int64
}

struct StorageReply: Decodable {
    let version: Int
    let status: String
    let code: String?
    let root: String?
    let destination: String?
    let confirmation: String?
    let project_count: Int?
    let files: Int?
    let bytes: Int64?
    let backup: String?
    let workspace: String?
    let projects: [StorageProject]?

    var isReady: Bool { version == 1 && status == "ready" }
    var rootURL: URL? { isReady ? localDirectoryURL(root) : nil }
    var backupURL: URL? { isReady ? localDirectoryURL(backup) : nil }
    var workspaceURL: URL? { isReady ? localDirectoryURL(workspace) : nil }

    var plan: StoragePlan? {
        guard let root = rootURL, let destination = localDirectoryURL(destination), root != destination,
              let confirmation, confirmation.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil,
              let projectCount = project_count, projectCount >= 0,
              let files, files >= 0, let bytes, bytes >= 0 else { return nil }
        return StoragePlan(root: root, destination: destination, confirmation: confirmation,
                           projectCount: projectCount, files: files, bytes: bytes)
    }
}

enum StorageProcess {
    static func run(_ command: StorageCommand, runtime: URL, completion: @escaping (StorageReply?) -> Void) {
        DispatchQueue.global(qos: .userInitiated).async {
            let reply = execute(command, runtime: runtime)
            DispatchQueue.main.async { completion(reply) }
        }
    }

    private static func execute(_ command: StorageCommand, runtime: URL) -> StorageReply? {
        let process = Process()
        let response = Pipe()
        process.executableURL = runtime
        process.arguments = command.arguments
        process.environment = WorkbenchProcess.environment(ProcessInfo.processInfo.environment, home: NSHomeDirectory())
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = response
        process.standardError = FileHandle.nullDevice
        do {
            try process.run()
        } catch {
            return nil
        }
        try? response.fileHandleForWriting.close()
        let timeout = DispatchWorkItem { if process.isRunning { process.terminate() } }
        DispatchQueue.global().asyncAfter(deadline: .now() + command.timeout, execute: timeout)
        defer { timeout.cancel() }
        let reader = response.fileHandleForReading
        let data = readReply(from: reader)
        try? reader.close()
        process.waitUntilExit()
        guard let data, let reply = try? JSONDecoder().decode(StorageReply.self, from: data),
              reply.version == 1 else { return nil }
        if process.terminationStatus == 0 { return reply.isReady ? reply : nil }
        return reply.status == "error" ? reply : nil
    }

    static func readReply(from reader: FileHandle, limit: Int = 2 * 1024 * 1024) -> Data? {
        var result = Data()
        while result.count <= limit {
            guard let chunk = try? reader.read(upToCount: 65_536), !chunk.isEmpty else { return nil }
            if let newline = chunk.firstIndex(of: 10) {
                result.append(chunk.prefix(upTo: newline))
                return result.count <= limit ? result : nil
            }
            result.append(chunk)
        }
        return nil
    }
}
