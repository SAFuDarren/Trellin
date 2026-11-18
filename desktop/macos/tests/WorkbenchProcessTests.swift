import Foundation

@main
struct WorkbenchProcessTests {
    static func main() throws {
        try readiness()
        try readLiveService()
        try startFailures()
        try position()
        environment()
        print("5 Trellin native boundary checks passed.")
    }

    private static func startFailures() throws {
        func response(_ code: String, status: String = "error") throws -> WorkbenchStartFailure {
            let data = try JSONSerialization.data(withJSONObject: ["version": 1, "status": status, "code": code])
            return try JSONDecoder().decode(WorkbenchReadiness.self, from: data).failure
        }
        let failures = try [response("login-missing"), response("login-invalid"), response("unbound"),
                            response("postgres"), response("unknown"), response("login-missing", status: "ready")]
        precondition(failures == [.loginMissing, .loginInvalid, .unbound, .postgres, .startup, .startup])
        precondition(!WorkbenchStartFailure.loginMissing.canRetry && WorkbenchStartFailure.postgres.canRetry)
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
