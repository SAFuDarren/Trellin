import Foundation

@main
struct WorkbenchProcessTests {
    static func main() throws {
        try readiness()
        try readLiveService()
        try startFailures()
        try launchDestination()
        try storageProtocol()
        try storageDestinationAliases()
        try storageReplyBounds()
        try position()
        environment()
        releaseNotesLinks()
        print("10 Trellin native boundary checks passed.")
    }

    private static func startFailures() throws {
        func response(_ code: String, status: String = "error") throws -> WorkbenchStartFailure {
            let data = try JSONSerialization.data(withJSONObject: ["version": 1, "status": status, "code": code])
            return try JSONDecoder().decode(WorkbenchReadiness.self, from: data).failure
        }
        let failures = try [response("login-missing"), response("login-invalid"), response("unbound"),
                            response("workspace-occupied"),
                            response("create-path"), response("create-postgres"), response("create-failed"),
                            response("postgres"), response("storage-unavailable"), response("storage-project"),
                            response("unknown"), response("login-missing", status: "ready")]
        precondition(failures == [.loginMissing, .loginInvalid, .unbound, .workspaceOccupied, .createPath, .createPostgres,
                                  .createFailed, .postgres, .library, .project, .startup, .startup])
        precondition(!WorkbenchStartFailure.loginMissing.canRetry && WorkbenchStartFailure.postgres.canRetry)
    }

    private static func launchDestination() throws {
        let parent = URL(fileURLWithPath: "/Studies/量子图 Proofs", isDirectory: true)
        precondition(WorkbenchLaunch.restore(parent).arguments.suffix(1) == [parent.path])
        precondition(WorkbenchLaunch.select(parent).arguments.suffix(2) == ["--select", parent.path])
        precondition(WorkbenchLaunch.library.arguments.suffix(1) == ["--library"])
        precondition(WorkbenchLaunch.newManaged("量子图 Proofs").arguments.suffix(2) == ["--new-managed", "量子图 Proofs"])
        func ready(_ path: String) throws -> WorkbenchReadiness {
            let data = try JSONSerialization.data(withJSONObject: ["version": 1, "status": "ready", "workspace": path])
            return try JSONDecoder().decode(WorkbenchReadiness.self, from: data)
        }
        let actual = try ready("/Studies/量子图 Proofs/Trellin")
        let invalid = try ready("Studies/量子图 Proofs/Trellin")
        precondition(actual.workspaceURL?.path == "/Studies/量子图 Proofs/Trellin")
        precondition(invalid.workspaceURL == nil)
    }

    private static func storageProtocol() throws {
        func reply(_ values: [String: Any]) throws -> StorageReply {
            let data = try JSONSerialization.data(withJSONObject: values)
            return try JSONDecoder().decode(StorageReply.self, from: data)
        }
        let valid: [String: Any] = [
            "version": 1,
            "status": "ready",
            "root": "/Studies/Trellin Library",
            "destination": "/Archive/Trellin Library",
            "confirmation": String(repeating: "a", count: 64),
            "project_count": 2,
            "files": 14,
            "bytes": Int64(8_512),
        ]
        let result = try reply(valid)
        let plan = result.plan!
        precondition(plan.projectCount == 2 && plan.files == 14 && plan.bytes == 8_512)
        precondition(plan.root.path == "/Studies/Trellin Library")
        precondition(StorageCommand.move(plan).arguments.suffix(3) ==
                     ["--storage-move", "/Archive/Trellin Library", String(repeating: "a", count: 64)])
        precondition(StorageCommand.preview(plan.destination).arguments.suffix(2) ==
                     ["--storage-preview", "/Archive/Trellin Library"])
        precondition(StorageCommand.information.arguments.suffix(1) == ["--storage-info"])
        for override: [String: Any] in [["version": 2], ["status": "error"], ["root": "Studies"],
                                         ["destination": "/Studies/Trellin Library"],
                                         ["confirmation": "stale"], ["project_count": -1], ["bytes": -1]] {
            let values = valid.merging(override) { _, later in later }
            let invalid = try reply(values)
            precondition(invalid.plan == nil)
        }
    }

    private static func storageDestinationAliases() throws {
        let name = "trellin-native-storage-\(UUID())"
        let parent = URL(fileURLWithPath: "/private/tmp/\(name)", isDirectory: true)
        let alias = URL(fileURLWithPath: "/tmp/\(name)", isDirectory: true)
        let files = FileManager.default
        try files.createDirectory(at: parent, withIntermediateDirectories: false)
        defer { try? files.removeItem(at: parent) }
        let selected = alias.appendingPathComponent("Trellin Library", isDirectory: true)
        let reported = parent.appendingPathComponent("Trellin Library", isDirectory: true)
        let other = parent.appendingPathComponent("Different Library", isDirectory: true)
        precondition(!files.fileExists(atPath: reported.path))
        precondition(sameStorageDestination(selected, reported))
        precondition(!sameStorageDestination(selected, other))
        try files.createDirectory(at: reported, withIntermediateDirectories: false)
        precondition(sameStorageDestination(selected, reported))
    }

    private static func storageReplyBounds() throws {
        let bytes = Data("{\"version\":1}\n".utf8)
        for limit in [8, 100] {
            let response = Pipe()
            let reader = response.fileHandleForReading
            let writer = response.fileHandleForWriting
            try writer.write(contentsOf: bytes)
            try writer.close()
            defer { try? reader.close() }
            let result = StorageProcess.readReply(from: reader, limit: limit)
            if limit < bytes.count {
                precondition(result == nil)
            } else {
                precondition(result == bytes.dropLast())
            }
        }
    }

    private static func readLiveService() throws {
        let response = Pipe()
        let writer = response.fileHandleForWriting
        defer { try? writer.close() }
        let seen = DispatchSemaphore(value: 0)
        DispatchQueue.global().async {
            let ready = WorkbenchProcess.readiness(from: response.fileHandleForReading)
            precondition(ready?.code == "workspace")
            seen.signal()
        }
        try writer.write(contentsOf: Data("{\"version\":1,\"status\":\"error\",\"code\":\"workspace\"}\n".utf8))
        precondition(seen.wait(timeout: .now() + 2) == .success, "readiness blocked on a live service pipe")
    }

    private static func readiness() throws {
        let token = String(repeating: "a", count: 43)
        let valid = "http://127.0.0.1:51420/#session=\(token)"
        func response(_ address: String, version: Int = 1) throws -> WorkbenchReadiness {
            let data = try JSONSerialization.data(withJSONObject: ["version": version, "status": "ready", "url": address])
            return try JSONDecoder().decode(WorkbenchReadiness.self, from: data)
        }

        let accepted = try response(valid)
        precondition(accepted.sessionURL?.absoluteString == valid)
        let rejected = try [
            response(valid, version: 2),
            response(valid.replacingOccurrences(of: "127.0.0.1", with: "example.com")),
            response(valid.replacingOccurrences(of: ":51420", with: "")),
            response(valid.replacingOccurrences(of: "http:", with: "file:")),
            response(valid + "&extra=true"),
        ]
        precondition(rejected.allSatisfy { $0.sessionURL == nil })
    }

    private static func position() throws {
        let claimId = "11111111-1111-4111-8111-111111111111"
        let nodeId = "obligation:\(claimId):1"
        let selection = WorkbenchPosition(message: ["claimId": claimId, "nodeId": nodeId])!
        let saved = try JSONEncoder().encode(selection)
        let restored = try JSONDecoder().decode(WorkbenchPosition.self, from: saved)
        precondition(restored == selection)
        precondition(selection.script.contains(nodeId))
        precondition(WorkbenchPosition(message: ["claimId": claimId, "nodeId": "other study"]) == nil)
        precondition(WorkbenchPosition(message: ["claimId": "bad", "nodeId": nodeId]) == nil)
    }

    private static func releaseNotesLinks() {
        let path = "https://github.com/SAFuDarren/Trellin/releases/tag/"
        let allowed = ["v1.2.3", "1.2.3", "v0.0.0", "v999999999.1.2"]
        let rejectedTags = [
            "", "v1.2", "v1.2.3.4", "V1.2.3", "v01.2.3", "1.02.3", "1.2.03", "1..3",
            "v1000000000.1.2", "v1.2.3-rc.1", "v1.2.3+build", "１.2.3", "v%31.2.3",
            "v1.2.3#fragment", "v1.2.3?", "v1.2.3?scope=public", "v1.2.3/", "../tag/v1.2.3",
        ]
        let rejectedAddresses = [
            "http://github.com/SAFuDarren/Trellin/releases/tag/v1.2.3",
            "https://github.com:443/SAFuDarren/Trellin/releases/tag/v1.2.3",
            "https://example.test/SAFuDarren/Trellin/releases/tag/v1.2.3",
            "https://github.com@example.test/SAFuDarren/Trellin/releases/tag/v1.2.3",
            "https://github.com/SAFuDarren/another-project/releases/tag/v1.2.3",
            "https://github.com/SAFuDarren/Trellin/releases/latest",
            "https://github.com/SAFuDarren/Trellin/releases/download/v1.2.3/Trellin.zip",
        ]
        for tag in allowed {
            precondition(ReleaseNotesLink.isOfficial(URL(string: path + tag)!))
        }
        for address in rejectedTags.map({ path + $0 }) + rejectedAddresses {
            precondition(!ReleaseNotesLink.isOfficial(URL(string: address)!), "Accepted disallowed URL: " + address)
        }
    }

    private static func environment() {
        let environment = WorkbenchProcess.environment([
            "PATH": "/usr/bin:./bin:/custom", "RM_HUMAN_DSN": "unused",
            "RM_DATA_ROOT": "unused", "XDG_CONFIG_HOME": "/config",
        ], home: "/home")
        precondition(environment["RM_HUMAN_DSN"] == nil)
        precondition(environment["RM_DATA_ROOT"] == nil)
        precondition(environment["XDG_CONFIG_HOME"] == "/config")
        let binaries = environment["PATH"]!.split(separator: ":")
        precondition(binaries.contains("/home/.elan/bin"))
        precondition(binaries.contains("/opt/homebrew/opt/postgresql@18/bin"))
        precondition(binaries.contains("/custom"))
        precondition(!binaries.contains("./bin"))
    }
}
