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

let companionID = String(repeating: "a", count: 32)
let observed = Date(timeIntervalSince1970: 1_000)
func companionPayload(_ identity: Data, phase: String = "recording") -> Data {
    let host = try! JSONSerialization.jsonObject(with: identity)
    return try! JSONSerialization.data(withJSONObject: ["host": host, "snapshot": [
        "observed_at": observed.timeIntervalSince1970, "recording_id": companionID,
        "phase": phase, "source": "native", "lines": [] as [String]
    ]])
}
let recordingSnapshot = CompanionSnapshot.verified(companionPayload(payload()), code: 200, configuration: configuration, recording: companionID)
check(recordingSnapshot?.recording_id == companionID, "companion verifies identity and bound recording")
check(CompanionSnapshot.verified(companionPayload(payload(["data_fingerprint":"other"])), code: 200, configuration: configuration, recording: companionID) == nil, "companion refuses other workspace content")
check(CompanionSnapshot.verified(companionPayload(payload()), code: 200, configuration: configuration, recording: String(repeating: "b", count: 32)) == nil, "companion refuses other recording content")
var observation = CompanionObservation()
observation.receive(recordingSnapshot)
check(observation.target(at: observed) == companionID, "only current bound recording is a return target")
check(observation.target(at: observed.addingTimeInterval(9)) == nil, "expired status cannot route")
observation.receive(nil)
check(observation.latest?.observed_at == observed.timeIntervalSince1970 && observation.target(at: observed) == nil, "disconnect retains timestamp, not navigation")
observation.receive(recordingSnapshot)
check(observation.target(at: observed) == companionID, "reconnection restores the same navigation target")

check(observation.active(at: observed)?.source == "native", "fresh active native recording routes")
check(observation.active(at: observed.addingTimeInterval(9)) == nil, "stale menu recording never routes")
observation.receive(CompanionSnapshot.verified(companionPayload(payload(), phase: "multiple"), code: 200, configuration: configuration, recording: nil))
check(observation.active(at: observed) == nil, "ambiguous menu recording never routes")
let reportData = try JSONSerialization.data(withJSONObject: [
    "host": try JSONSerialization.jsonObject(with: payload()),
    "running_backend": "old", "disk_backend": "new", "frontend": ["verified": true, "entry": "/assets/new.js"],
    "capture": ["verified": true, "version": "4"], "busy": true
])
let report = try JSONDecoder().decode(HostDiagnostics.self, from: reportData)
let reportText = report.summary(boundBackend: "new", cleanBuild: true, loadedEntry: "/assets/old.js")
check(reportText.contains("安全重启") && reportText.contains("重新载入") && reportText.contains("请完成后再更新"), "diagnostics distinguishes stale service, page, and maintenance guard")
check(report.summary(boundBackend: "new", cleanBuild: false, loadedEntry: nil).contains("未提交改动"), "dirty host manifest has specific diagnosis")

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
// E2 persistence and host leases use an isolated private folder only.
let stateDirectory = temp.appendingPathComponent("host-state")
let preferences = try HostPreferences(directory: stateDirectory)
let initial = preferences.load(defaults: configuration)
check(!initial.saved && !initial.problem && initial.token == "missing", "missing preference uses bundled default")
check(!FileManager.default.fileExists(atPath: stateDirectory.path), "read-only load does not create state")
let changed = try HostConfiguration(projectRoot: config.projectRoot, dataRoot: config.dataRoot, backupsRoot: config.backupsRoot, port: 19876)
let saved = try preferences.save(changed, expectedToken: initial.token)
check(saved.saved && !saved.problem && saved.configuration == changed, "save returns exact canonical configuration")
let reloaded = preferences.load(defaults: configuration)
check(reloaded.saved && reloaded.configuration == changed, "configuration survives new preference object and load")
let folderAttributes = try FileManager.default.attributesOfItem(atPath: stateDirectory.path)
let fileAttributes = try FileManager.default.attributesOfItem(atPath: preferences.file.path)
check((folderAttributes[.posixPermissions] as? NSNumber)?.intValue == 0o700 && (fileAttributes[.posixPermissions] as? NSNumber)?.intValue == 0o600, "private folder and file modes")
let stableData = try Data(contentsOf: preferences.file)
let unchecked = try JSONDecoder().decode(HostConfiguration.self, from: Data("{\"version\":1,\"projectRoot\":\"relative\",\"dataRoot\":\"/tmp/b\",\"backupsRoot\":\"/tmp/c\",\"port\":8765}".utf8))
check((try? preferences.save(unchecked, expectedToken: saved.token)) == nil, "save revalidates values even if a caller bypasses initializer")
check((try? preferences.save(configuration, expectedToken: "old-token")) == nil, "stale setting write rejected")
let afterConflict = try Data(contentsOf: preferences.file)
check(afterConflict == stableData, "conflict preserves saved document")
check(!FileManager.default.fileExists(atPath: changed.dataRoot), "saving does not create meeting data folder")
let badJSON = Data("{broken configuration".utf8); try badJSON.write(to: preferences.file)
let corrupted = preferences.load(defaults: configuration)
check(corrupted.problem && !corrupted.saved && corrupted.token != nil, "corrupt saved document is explicit, not silently active default")
_ = try preferences.save(changed, expectedToken: corrupted.token, preservePrevious: true)
let backups = try FileManager.default.contentsOfDirectory(at: stateDirectory, includingPropertiesForKeys: nil).filter { $0.lastPathComponent.hasPrefix("connection-invalid-") }
let preservedBadJSON = backups.isEmpty ? Data() : try Data(contentsOf: backups[0])
check(backups.count == 1 && preservedBadJSON == badJSON, "repair retains corrupt content privately")
let outside = temp.appendingPathComponent("outside-config.json"); try stableData.write(to: outside)
try FileManager.default.removeItem(at: preferences.file); try FileManager.default.createSymbolicLink(at: preferences.file, withDestinationURL: outside)
let redirected = preferences.load(defaults: configuration)
check(redirected.problem && redirected.token == nil, "symbolic link preferences are not followed")
check((try? preferences.save(configuration, expectedToken: redirected.token)) == nil, "unsafe configuration cannot be overwritten")
let outsideAfter = try Data(contentsOf: outside)
check(outsideAfter == stableData, "outside file unchanged")
try FileManager.default.removeItem(at: preferences.file)
_ = try preferences.save(changed, expectedToken: "missing")
try Data(repeating: 32, count: 65_537).write(to: preferences.file)
check(preferences.load(defaults: configuration).problem, "oversized settings rejected")
try stableData.write(to: preferences.file); try FileManager.default.setAttributes([.posixPermissions:0o644], ofItemAtPath: preferences.file.path)
check(preferences.load(defaults: configuration).problem, "public settings permissions rejected")
try FileManager.default.setAttributes([.posixPermissions:0o600], ofItemAtPath: preferences.file.path)
var firstLease: HostInstanceLock? = try HostInstanceLock(preferences: preferences)
check(firstLease != nil && HostInstanceLock.isRunning(preferences: preferences), "host lease is observable")
let secondLease = try HostInstanceLock(preferences: preferences)
check(secondLease == nil, "second host cannot acquire same profile")
firstLease = nil
check(!HostInstanceLock.isRunning(preferences: preferences), "lease releases without deleting lock file")
let afterRelease = try HostInstanceLock(preferences: preferences)
check(afterRelease != nil, "stale lock file does not prevent restart")
let otherPreferences = try HostPreferences(directory: temp.appendingPathComponent("another-state"))
let otherLease = try HostInstanceLock(preferences: otherPreferences)
check(otherLease != nil, "test profiles have independent ownership")
check(HostInstanceLock.activationName(preferences: preferences) != HostInstanceLock.activationName(preferences: otherPreferences), "activation scope is profile-specific")
let policy = WorkspacePolicy(configuration: configuration)
check(policy.allows(configuration.workspaceURL), "exact loopback origin allowed")
for text in ["https://127.0.0.1:18765/", "http://localhost:18765/", "http://127.0.0.1:18766/", "http://127.0.0.1:18765.evil.test/", "file:///tmp/a", "data:text/html,test", "javascript:alert(1)", "http://user@127.0.0.1:18765/"] {
    check(!policy.allows(URL(string:text)), "foreign and privileged URLs rejected")
}
check(policy.preview(URL(string:"http://127.0.0.1:18765/api/meetings/" + String(repeating:"a",count:32) + "/minutes/export/pdf?inline=true")), "specific same-origin PDF preview allowed")
check(!policy.preview(configuration.workspaceURL), "new workspaces cannot silently replace draft view")
check(policy.localBlob(URL(string:"blob:http://127.0.0.1:18765/" + UUID().uuidString)), "local export blob recognized")
check(!policy.localBlob(URL(string:"blob:https://remote.example/" + UUID().uuidString)), "foreign blob rejected")
check(policy.storeIdentifier == WorkspacePolicy(configuration:configuration).storeIdentifier && policy.storeIdentifier != WorkspacePolicy(configuration:changed).storeIdentifier, "storage isolated by workspace and port")
check(WorkspacePolicy.filename("../../unsafe\\meeting.json") == "meeting.json", "server filename cannot escape save directory")
check(WorkspacePolicy.filename("..") == "会议导出.download", "empty or traversal name gets safe default")
let downloadsDirectory = temp.appendingPathComponent("exports");try FileManager.default.createDirectory(at: downloadsDirectory, withIntermediateDirectories:true)
let destination = downloadsDirectory.appendingPathComponent("meeting.txt")
let stage = try DownloadStaging(destination: destination)
try Data("synthetic export".utf8).write(to: stage.temporary)
try stage.publish();check(FileManager.default.fileExists(atPath: destination.path), "completed export published")
stage.cleanup()
let overwrite = try DownloadStaging(destination: destination)
try Data("updated export".utf8).write(to: overwrite.temporary)
try overwrite.publish();let overwritten = try Data(contentsOf: destination)
check(overwritten == Data("updated export".utf8), "explicit overwrite publishes only completed bytes")
overwrite.cleanup()
let changedTarget = try DownloadStaging(destination: destination)
try Data("pending export".utf8).write(to: changedTarget.temporary)
try Data("external edit".utf8).write(to: destination)
check((try? changedTarget.publish()) == nil, "concurrent destination edit prevents overwrite")
let kept = try Data(contentsOf: destination);check(kept == Data("external edit".utf8), "concurrent original preserved")
changedTarget.cleanup()
let newTarget = downloadsDirectory.appendingPathComponent("new.txt");let racing = try DownloadStaging(destination:newTarget)
try Data("export".utf8).write(to:racing.temporary);try Data("other file".utf8).write(to:newTarget)
check((try? racing.publish()) == nil, "appeared destination is never clobbered")
racing.cleanup()
let linkTarget = downloadsDirectory.appendingPathComponent("link.txt");try FileManager.default.createSymbolicLink(at:linkTarget,withDestinationURL:destination)
check((try? DownloadStaging(destination:linkTarget)) == nil, "symlink destinations rejected")
let cancelled = try DownloadStaging(destination: destination);cancelled.cleanup()
let afterCancel = try Data(contentsOf: destination);check(afterCancel == kept, "cancel cleanup never removes original")
check(!FileManager.default.fileExists(atPath:cancelled.folder.path), "cancel removes only its private staging folder")
print("{\"suite\":\"native-host-e3\",\"passed\":\(passed),\"network\":false,\"capture\":false}")
