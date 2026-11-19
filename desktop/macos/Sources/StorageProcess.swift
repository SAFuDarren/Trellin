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
    case preview(URL)
    case move(StoragePlan)

    var timeout: TimeInterval {
        switch self {
        case .information: return 15
        case .preview: return 300
        case .move: return 1_800
        }
    }

    var arguments: [String] {
        let options: [String]
        switch self {
        case .information: options = ["--storage-info"]
        case let .preview(destination): options = ["--storage-preview", destination.path]
        case let .move(plan): options = ["--storage-move", plan.destination.path, plan.confirmation]
        }
        return WorkbenchLaunch.prefix + options
    }
}

struct StorageProject: Decodable {
    let name: String
    let path: String

    var workspace: URL? { localDirectoryURL(path) }
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
    let projects: [StorageProject]?

    var isReady: Bool { version == 1 && status == "ready" }
    var rootURL: URL? { isReady ? localDirectoryURL(root) : nil }
    var backupURL: URL? { isReady ? localDirectoryURL(backup) : nil }

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
