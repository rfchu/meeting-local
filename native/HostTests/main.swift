import Foundation

var passed = 0
func check(_ condition: @autoclosure () -> Bool, _ message: String) {
    guard condition() else { fputs("FAILED: \(message)\n", stderr); exit(1) }
    passed += 1
}
let configuration = try HostConfiguration(projectRoot: "/tmp/tingji-host-tests/project", dataRoot: "/tmp/tingji-host-tests/data", backupsRoot: "/tmp/tingji-host-tests/backups", port: 18765)
func payload(_ overrides: [String:Any] = [:]) -> Data {
    var value: [String:Any] = ["product":"cn.seeknow.tingji", "protocol_version":1, "project_fingerprint":HostConfiguration.fingerprint(configuration.projectRoot), "data_fingerprint":HostConfiguration.fingerprint(configuration.dataRoot), "pid":1234, "instance_id":"synthetic", "service_mode":"detached", "workspace_path":"/", "capture_ownership":["browser":"recording_page", "native":"api_service", "host":"none"], "restart_capture_reattach":false]
    value.merge(overrides) { _, new in new }; return try! JSONSerialization.data(withJSONObject: value)
}
check(configuration.workspaceURL.absoluteString == "http://127.0.0.1:18765/", "loopback only workspace")
check(configuration.identityURL.path == "/api/runtime/host", "fixed identity path")
check(validateIdentity(payload(), statusCode: 200, configuration: configuration).canOpen, "verified workspace accepted")
check(!validateIdentity(payload(), statusCode: 302, configuration: configuration).canOpen, "redirect is not ready")
check(validateIdentity(payload(["project_fingerprint":"other"]), statusCode: 200, configuration: configuration) == .differentWorkspace, "other project rejected")
check(validateIdentity(payload(["data_fingerprint":"other"]), statusCode: 200, configuration: configuration) == .differentWorkspace, "other data rejected")
for change: [String:Any] in [["product":"other"], ["protocol_version":2], ["pid":0], ["service_mode":"unknown"], ["workspace_path":"https://remote.example/"], ["restart_capture_reattach":true], ["capture_ownership":["host":"owner"]]] {
    check(validateIdentity(payload(change), statusCode: 200, configuration: configuration) == .incompatible, "incompatible identity rejected")
}
check(validateIdentity(Data("<html>not a service</html>".utf8), statusCode: 200, configuration: configuration) == .incompatible, "non JSON rejected")
check(validateIdentity(Data(repeating: 32, count: 16_385), statusCode: 200, configuration: configuration) == .incompatible, "response size bounded")
for port in [0, 80, 65536] {
    check((try? HostConfiguration(projectRoot: "/tmp/a", dataRoot: "/tmp/b", backupsRoot: "/tmp/c", port: port)) == nil, "port bounds")
}
check((try? HostConfiguration(projectRoot: "relative", dataRoot: "/tmp/b", backupsRoot: "/tmp/c", port: 8765)) == nil, "relative path rejected")
check((try? HostConfiguration(version: 2, projectRoot: "/tmp/a", dataRoot: "/tmp/b", backupsRoot: "/tmp/c", port: 8765)) == nil, "unknown manifest version")
check(!ConnectionState.checking.canStart && !ConnectionState.incompatible.canStart && !ConnectionState.differentWorkspace.canStart, "unknown occupied service cannot start")
check(ConnectionState.unavailable.canStart && !ConnectionState.unavailable.canOpen, "offline state has explicit start only")
var generation = ProbeGeneration(); let old = generation.next(); let current = generation.next()
check(!generation.accepts(old) && generation.accepts(current), "late response ignored")

// A test-only URLProtocol provides synthetic responses without network, models or devices.
final class SyntheticProtocol: URLProtocol {
    static var body = Data()
    static var failure: Error?
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: "HTTP/1.1", headerFields: ["Content-Type":"application/json"])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Self.body)
        if let error = Self.failure { client?.urlProtocol(self, didFailWithError: error) }
        else { client?.urlProtocolDidFinishLoading(self) }
    }
    override func stopLoading() {}
}
func probe(body: Data, error: Error? = nil) -> ConnectionState {
    SyntheticProtocol.body = body; SyntheticProtocol.failure = error
    let client = ServiceClient(protocolClasses: [SyntheticProtocol.self]); let semaphore = DispatchSemaphore(value: 0)
    var result = ConnectionState.checking
    client.probe(configuration) { value in result = value; semaphore.signal() }
    check(semaphore.wait(timeout: .now()+5) == .success, "probe returns within bound")
    client.close(); return result
}
check(!probe(body: Data(repeating: 32, count: 16_385)).canOpen, "network response buffer bounded")
check(probe(body: payload()).canOpen, "network adapter validates affinity")
check(probe(body: payload(["data_fingerprint":"foreign"])) == .differentWorkspace, "network adapter rejects wrong data")
check(probe(body: payload(), error: URLError(.networkConnectionLost)) == .incompatible, "partial failed transfer cannot appear connected or start another listener")
let client = ServiceClient(protocolClasses: [SyntheticProtocol.self]); var redirect: URLRequest? = URLRequest(url: URL(string: "https://remote.example/")!)
client.urlSession(URLSession.shared, task: URLSession.shared.dataTask(with: configuration.identityURL), willPerformHTTPRedirection: HTTPURLResponse(url: configuration.identityURL, statusCode: 302, httpVersion: nil, headerFields: nil)!, newRequest: redirect!) { redirect = $0 }
check(redirect == nil, "redirect never followed"); client.close()

let temp = FileManager.default.temporaryDirectory.appendingPathComponent("tingji-host-manifest-" + UUID().uuidString)
try FileManager.default.createDirectory(at: temp, withIntermediateDirectories: true)
defer { try? FileManager.default.removeItem(at: temp) }
check(HostConfiguration.canonical("/tmp") == "/private/tmp", "POSIX spelling matches Python rather than Foundation aliases")
let beforeCreation = temp.appendingPathComponent("new-data")
let firstFingerprint = HostConfiguration.fingerprint(beforeCreation.path)
try FileManager.default.createDirectory(at: beforeCreation, withIntermediateDirectories: true)
check(firstFingerprint == HostConfiguration.fingerprint(beforeCreation.path), "creating a data directory cannot change affinity")
let alias = temp.appendingPathComponent("alias")
try FileManager.default.createSymbolicLink(at: alias, withDestinationURL: beforeCreation)
check(HostConfiguration.fingerprint(alias.path) == firstFingerprint, "symlinks share POSIX affinity")
let manifest = temp.appendingPathComponent("config.json")
try Data("{\"version\":1,\"projectRoot\":\"relative\",\"dataRoot\":\"/tmp/b\",\"backupsRoot\":\"/tmp/c\",\"port\":8765}".utf8).write(to: manifest)
check((try? HostConfiguration.load(manifest)) == nil, "decoding cannot bypass validation")
let python = temp.appendingPathComponent(".venv/bin/python"); let manager = temp.appendingPathComponent("scripts/manage_service.py")
try FileManager.default.createDirectory(at: python.deletingLastPathComponent(), withIntermediateDirectories: true)
try FileManager.default.createDirectory(at: manager.deletingLastPathComponent(), withIntermediateDirectories: true)
try Data("synthetic only".utf8).write(to: python); try Data().write(to: manager)
try FileManager.default.setAttributes([.posixPermissions:0o700], ofItemAtPath: python.path)
let config = try HostConfiguration(projectRoot: temp.path, dataRoot: temp.appendingPathComponent("数据 space").path, backupsRoot: temp.appendingPathComponent("backups").path, port: 18765)
let plan = try ServiceStartPlan(config)
check(plan.arguments == [config.managerURL.path, "start", "--detached"], "only existing guarded start command")
check(plan.environment["PORT"] == "18765" && plan.environment["MEETING_DATA"] == config.dataRoot && plan.environment["MEETING_BACKUPS"] == config.backupsRoot, "pin workspace and port")
check(plan.environment["PYTHONPATH"] == nil && !plan.arguments.contains("--force"), "no inherited loader or force stop")
check((try? ServiceStartPlan(configuration)) == nil, "missing environment rejected without launching anything")
let foreignManager = temp.appendingPathComponent("other-manager.py")
try FileManager.default.moveItem(at: manager, to: foreignManager)
try FileManager.default.createSymbolicLink(at: manager, withDestinationURL: foreignManager)
check((try? ServiceStartPlan(config)) == nil, "redirected service manager cannot start another project")
print("{\"suite\":\"native-host-e1\",\"passed\":\(passed),\"network\":false,\"capture\":false}")
