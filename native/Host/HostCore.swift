import Foundation
import CryptoKit
import Darwin

enum HostFailure: Error { case invalidConfiguration, invalidResponse }

struct HostConfiguration: Codable, Equatable {
    let version: Int
    let projectRoot: String
    let dataRoot: String
    let backupsRoot: String
    let port: Int

    init(version: Int = 1, projectRoot: String, dataRoot: String, backupsRoot: String, port: Int) throws {
        guard version == 1, (1024...65535).contains(port), [projectRoot, dataRoot, backupsRoot].allSatisfy({$0.hasPrefix("/") && !$0.contains("\0") && !$0.contains("\n")}) else { throw HostFailure.invalidConfiguration }
        self.version = version
        self.projectRoot = Self.canonical(projectRoot)
        self.dataRoot = Self.canonical(dataRoot)
        self.backupsRoot = Self.canonical(backupsRoot)
        self.port = port
    }
    static func canonical(_ path: String) -> String {
        // Foundation can rewrite /private/tmp as /tmp only after a directory exists.
        // Match Python Path.resolve using POSIX realpath, including missing final components.
        var prefix = URL(fileURLWithPath: path).standardizedFileURL.path
        var suffix: [String] = []
        while true {
            if let resolved = realpath(prefix, nil) {
                let base = String(cString: resolved); free(resolved)
                return suffix.reversed().reduce(base) { ($0 as NSString).appendingPathComponent($1) }
            }
            guard prefix != "/" else { return path }
            suffix.append((prefix as NSString).lastPathComponent)
            prefix = (prefix as NSString).deletingLastPathComponent
        }
    }
    static func fingerprint(_ path: String) -> String { SHA256.hash(data: Data(canonical(path).utf8)).map { String(format: "%02x", $0) }.joined() }
    var workspaceURL: URL { URL(string: "http://127.0.0.1:\(port)/")! }
    var identityURL: URL { workspaceURL.appendingPathComponent("api/runtime/host") }
    var portableRuntime: URL? {
        let code = URL(fileURLWithPath: projectRoot)
        let runtime = code.deletingLastPathComponent()
        guard code.lastPathComponent == "code", runtime.lastPathComponent == "PortableRuntime" else { return nil }
        return runtime
    }
    var workspaceBinding: String {
        Self.dataFingerprint(Data(("v1:" + Self.fingerprint(projectRoot) + ":" + Self.fingerprint(dataRoot)).utf8))
    }
    func workspaceMAC(_ message: String) -> String? {
        guard let preferences = try? HostPreferences(directory: URL(fileURLWithPath: dataRoot).deletingLastPathComponent()),
              let key = try? preferences.workspaceKey() else { return nil }
        return HMAC<SHA256>.authenticationCode(for: Data(message.utf8), using: SymmetricKey(data: key))
            .map { String(format: "%02x", $0) }.joined()
    }
    var workspaceAuthorization: String? { workspaceMAC("request:" + workspaceBinding) }
    var pythonURL: URL { URL(fileURLWithPath: projectRoot).appendingPathComponent(".venv/bin/python") }
    var managerURL: URL { URL(fileURLWithPath: projectRoot).appendingPathComponent("scripts/manage_service.py") }

    static func dataFingerprint(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }
    static func load(_ url: URL) throws -> HostConfiguration { try decode(Data(contentsOf: url)) }
    static func decode(_ data: Data) throws -> HostConfiguration {
        let decoded = try JSONDecoder().decode(HostConfiguration.self, from: data)
        // Synthesized Decodable does not invoke the validating initializer.
        return try HostConfiguration(version: decoded.version, projectRoot: decoded.projectRoot, dataRoot: decoded.dataRoot, backupsRoot: decoded.backupsRoot, port: decoded.port)
    }
}

struct ServiceIdentity: Codable {
    let product: String
    let protocolVersion: Int
    let projectFingerprint: String
    let dataFingerprint: String
    let pid: Int
    let instanceId: String?
    let serviceMode: String
    let workspacePath: String
    let captureOwnership: [String:String]
    let restartCaptureReattach: Bool
    enum CodingKeys: String, CodingKey {
        case product, pid
        case protocolVersion = "protocol_version", projectFingerprint = "project_fingerprint", dataFingerprint = "data_fingerprint"
        case instanceId = "instance_id", serviceMode = "service_mode", workspacePath = "workspace_path"
        case captureOwnership = "capture_ownership", restartCaptureReattach = "restart_capture_reattach"
    }
}

enum ConnectionState: Equatable {
    case checking, unavailable, incompatible, differentWorkspace, connected(pid: Int, mode: String)
    var canOpen: Bool { if case .connected = self { return true }; return false }
    var canStart: Bool { self == .unavailable }
    var title: String {
        switch self {
        case .checking: return "正在核对本机服务"
        case .unavailable: return "尚未连接本机服务"
        case .incompatible: return "服务不兼容，尚未连接"
        case .differentWorkspace: return "服务属于其他工作空间"
        case .connected: return "已连接 · 服务独立运行"
        }
    }
}

func validateIdentity(_ data: Data, statusCode: Int, configuration: HostConfiguration) -> ConnectionState {
    guard statusCode == 200, data.count <= 16_384, let value = try? JSONDecoder().decode(ServiceIdentity.self, from: data),
          value.product == "cn.seeknow.tingji", value.protocolVersion == 1, value.pid > 1,
          ["foreground", "detached", "launchd"].contains(value.serviceMode), value.workspacePath == "/",
          value.captureOwnership == ["browser":"recording_page", "native":"api_service", "host":"none"], !value.restartCaptureReattach else { return .incompatible }
    guard value.projectFingerprint == HostConfiguration.fingerprint(configuration.projectRoot),
          value.dataFingerprint == HostConfiguration.fingerprint(configuration.dataRoot) else { return .differentWorkspace }
    return .connected(pid: value.pid, mode: value.serviceMode)
}

// Request generation is local to one host window. Late probes cannot reopen stale state.
struct ProbeGeneration {
    private(set) var value = 0
    mutating func next() -> Int { value += 1; return value }
    func accepts(_ candidate: Int) -> Bool { candidate == value }
}
