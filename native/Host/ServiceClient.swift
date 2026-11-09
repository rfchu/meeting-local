import Foundation

final class ServiceClient: NSObject, URLSessionTaskDelegate, URLSessionDataDelegate, @unchecked Sendable {
    private var session: URLSession!
    private let lock = NSLock()
    private var replies: [Int: (Data, Int, (Data, Int) -> Void)] = [:]
    init(protocolClasses: [AnyClass]? = nil) {
        super.init()
        let options = URLSessionConfiguration.ephemeral
        if let protocolClasses { options.protocolClasses = protocolClasses }
        options.timeoutIntervalForRequest = 3
        options.timeoutIntervalForResource = 30
        options.httpShouldSetCookies = false
        options.httpCookieStorage = nil
        options.urlCredentialStorage = nil
        options.urlCache = nil
        options.connectionProxyDictionary = [:]
        session = URLSession(configuration: options, delegate: self, delegateQueue: nil)
    }
    func close() { session.invalidateAndCancel() }
    func browserWorkspace(_ configuration: HostConfiguration, completion: @escaping (URL?) -> Void) {
        let landing = configuration.workspaceURL.appendingPathComponent("_tingji/browser")
        var request = URLRequest(url: landing)
        request.httpMethod = "POST"
        authorized(request, configuration: configuration) { data, status in
            guard status == 200,
                  let reply = (try? JSONSerialization.jsonObject(with: data)) as? [String: String],
                  let code = reply["code"], code.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil,
                  var url = URLComponents(url: landing, resolvingAgainstBaseURL: false) else {
                completion(nil)
                return
            }
            url.fragment = code
            completion(url.url)
        }
    }
    func probe(_ configuration: HostConfiguration, completion: @escaping (ConnectionState) -> Void) {
        var request = URLRequest(url: configuration.identityURL)
        let challenge = configuration.portableRuntime == nil ? nil : Self.challenge()
        request.setValue(challenge, forHTTPHeaderField: "X-Tingji-Challenge")
        send(request) { data, code in
            let state: ConnectionState = code == 0 ? .unavailable : validateIdentity(data, statusCode: code, configuration: configuration)
            if state.canOpen, let challenge,
               !Self.verifyProof(data, configuration: configuration, purpose: "host", challenge: challenge) {
                completion(.incompatible)
            } else { completion(state) }
        }
    }
    private static func challenge() -> String {
        UUID().uuidString.lowercased().replacingOccurrences(of: "-", with: "")
    }
    private static func verifyProof(_ data: Data, configuration: HostConfiguration, purpose: String, challenge: String) -> Bool {
        guard let reply = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let proof = reply["proof"] as? String,
              let expected = configuration.workspaceMAC(purpose + ":" + challenge) else { return false }
        return proof == expected
    }
    func companion(_ configuration: HostConfiguration, mode: CompanionMode, recording: String?, original: Bool = false, follow: String? = nil, after: Int? = nil, completion: @escaping (CompanionSnapshot?) -> Void) {
        var url = URLComponents(url: configuration.workspaceURL.appendingPathComponent("api/runtime/companion"), resolvingAgainstBaseURL: false)!
        url.queryItems = [URLQueryItem(name: "mode", value: mode == .bilingual && original ? "original" : mode.rawValue)]
        if let recording { url.queryItems?.append(URLQueryItem(name: "rid", value: recording)) }
        if let follow, let after {
            url.queryItems?.append(URLQueryItem(name: "follow", value: follow))
            url.queryItems?.append(URLQueryItem(name: "after", value: String(after)))
        }
        var request = URLRequest(url: url.url!)
        let challenge = configuration.portableRuntime == nil ? nil : Self.challenge()
        request.setValue(challenge, forHTTPHeaderField: "X-Tingji-Challenge")
        authorized(request, configuration: configuration) { data, code in
            if let challenge, !Self.verifyProof(data, configuration: configuration, purpose: "companion", challenge: challenge) {
                completion(nil)
            } else { completion(CompanionSnapshot.verified(data, code: code, configuration: configuration, recording: recording)) }
        }
    }
    func translateSubtitle(_ configuration: HostConfiguration, recording: String, reference: CompanionSnapshot.TranslationRequest,
                           completion: @escaping (Int) -> Void) {
        var url = URLComponents(url: configuration.workspaceURL.appendingPathComponent("api/runtime/companion/translation"), resolvingAgainstBaseURL: false)!
        url.queryItems = [URLQueryItem(name: "rid", value: recording), URLQueryItem(name: "aid", value: reference.aid),
                          URLQueryItem(name: "index", value: String(reference.index)),
                          URLQueryItem(name: "profile_id", value: reference.profile_id)]
        var request = URLRequest(url: url.url!, timeoutInterval: 27)
        request.httpMethod = "POST"
        authorized(request, configuration: configuration, deadline: 30) { _, code in completion(code) }
    }

    func diagnostics(_ configuration: HostConfiguration, completion: @escaping (HostDiagnostics?) -> Void) {
        authorized(URLRequest(url: configuration.workspaceURL.appendingPathComponent("api/runtime/host-diagnostics")), configuration: configuration) { data, code in
            guard code == 200, let report = try? JSONDecoder().decode(HostDiagnostics.self, from: data),
                  let identity = try? JSONEncoder().encode(report.host),
                  validateIdentity(identity, statusCode: code, configuration: configuration).canOpen else {
                completion(nil)
                return
            }
            completion(report)
        }
    }
    func postShorthand(_ configuration: HostConfiguration, recording: String, text: String, synthesize: Bool, clientID: String, completion: @escaping (Bool) -> Void) {
        let url = configuration.workspaceURL.appendingPathComponent("api/recordings/\(recording)/notes/shorthand")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let payload: [String: Any] = [
            "raw_text": text,
            "synthesize": synthesize,
            "client_id": clientID
        ]
        request.httpBody = try? JSONSerialization.data(withJSONObject: payload)
        authorized(request, configuration: configuration) { _, code in completion(code == 201 || code == 200) }
    }
    private func authorized(_ request: URLRequest, configuration: HostConfiguration, deadline: TimeInterval = 4, completion: @escaping (Data, Int) -> Void) {
        guard configuration.portableRuntime != nil else { send(request, deadline: deadline, completion: completion); return }
        probe(configuration) { [weak self] state in
            guard let self, state.canOpen, let token = configuration.workspaceAuthorization else {
                completion(Data(), 0); return
            }
            var authenticated = request
            authenticated.setValue(token, forHTTPHeaderField: "X-Tingji-Authorization")
            self.send(authenticated, deadline: deadline, completion: completion)
        }
    }
    private func send(_ request: URLRequest, deadline: TimeInterval = 4, completion: @escaping (Data, Int) -> Void) {
        var request = request
        request.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let task = session.dataTask(with: request)
        lock.lock(); replies[task.taskIdentifier] = (Data(), 0, completion); lock.unlock()
        task.resume()
        DispatchQueue.global().asyncAfter(deadline: .now() + deadline) { [weak task] in task?.cancel() }
    }
    // A loopback redirect must never turn into a request to another service or a cloud host.
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse, completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        lock.lock()
        if var item = replies[dataTask.taskIdentifier] {
            item.1 = (response as? HTTPURLResponse)?.statusCode ?? 0
            replies[dataTask.taskIdentifier] = item
        }
        lock.unlock()
        completionHandler(response.expectedContentLength > 16_384 ? .cancel : .allow)
    }
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        lock.lock()
        guard var item = replies[dataTask.taskIdentifier] else { lock.unlock(); return }
        if item.0.count + data.count > 16_384 { item.1 = -1; replies[dataTask.taskIdentifier] = item; lock.unlock(); dataTask.cancel(); return }
        item.0.append(data); replies[dataTask.taskIdentifier] = item; lock.unlock()
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        lock.lock(); let item = replies.removeValue(forKey: task.taskIdentifier); lock.unlock()
        guard let (data, code, callback) = item else { return }
        callback(data, error == nil && code > 0 ? code : code == 0 ? 0 : -1)
    }
}

struct HostDiagnostics: Decodable {
    struct Frontend: Decodable { let verified: Bool; let entry: String? }
    struct Capture: Decodable { let verified: Bool; let version: String?; let reason: String? }
    let host: ServiceIdentity
    let running_backend: String
    let disk_backend: String
    let frontend: Frontend
    let capture: Capture
    let busy: Bool?

    func summary(boundBackend: String?, cleanBuild: Bool?, loadedEntry: String?) -> String {
        var lines: [String] = []
        if running_backend != disk_backend {
            lines.append("后台仍在运行旧版本。请安全重启本机服务。")
        } else {
            lines.append("后台与本机源码匹配。")
        }
        if !frontend.verified {
            lines.append("网页资源不完整，请重新构建前端。")
        } else if let loadedEntry, let entry = frontend.entry, loadedEntry != entry {
            lines.append("内嵌页面版本较旧，保存编辑后可重新载入。")
        } else {
            lines.append("网页资源就绪。")
        }
        if cleanBuild == false {
            lines.append("当前宿主由含未提交改动的源码构建。请从干净工作区重新构建宿主。")
        } else if boundBackend != disk_backend {
            lines.append("宿主的构建记录与当前源码不匹配，请重新构建宿主。")
        }
        let captureVersion = capture.version.map { "（\($0)）" } ?? ""
        lines.append(capture.verified ? "采集器\(captureVersion)已通过检查。" : "采集器需要检查。")
        switch busy {
        case true?: lines.append("录音或后台任务进行中，请完成后再更新。")
        case false?: lines.append("检查时没有录音或后台任务。")
        case nil: lines.append("当前无法确认更新时机，请先核对录音与任务。")
        }
        return lines.joined(separator: "\n")
    }
}

enum CompanionMode: String, CaseIterable {
    case status, bilingual, highlights
    var title: String {
        switch self {
        case .status: return "录音状态"
        case .bilingual: return "实时字幕"
        case .highlights: return "重点"
        }
    }
}

struct CompanionSnapshot: Decodable {
    struct Draft: Decodable {
        let text: String
        let stable_text: String
        let source_track: String?
    }
    struct TranslationRequest: Decodable, Equatable {
        let aid: String
        let index: Int
        let profile_id: String
    }
    let subtitle_seq: Int?
    let drafts: [Draft]?
    let translation_request: TranslationRequest?

    let observed_at: Double
    let recording_id: String?
    let meeting_id: String?
    let source: String?
    let phase: String
    let storage: String?
    let asr: String?
    let lines: [String]
    let duration_seconds: Double?
    let system_levels: [Double]?
    let microphone_levels: [Double]?

    static func verified(_ data: Data, code: Int, configuration: HostConfiguration, recording: String?) -> Self? {
        guard code == 200, let envelope = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let host = envelope["host"], let body = envelope["snapshot"],
              let identity = try? JSONSerialization.data(withJSONObject: host, options: [.fragmentsAllowed]),
              validateIdentity(identity, statusCode: code, configuration: configuration).canOpen,
              let payload = try? JSONSerialization.data(withJSONObject: body, options: [.fragmentsAllowed]),
              let snapshot = try? JSONDecoder().decode(Self.self, from: payload),
              [snapshot.recording_id, snapshot.meeting_id].compactMap({ $0 }).allSatisfy({ $0.range(of: #"^[0-9a-f]{32}$"#, options: .regularExpression) != nil }),
              snapshot.recording_id == nil || recording == nil || snapshot.recording_id == recording else { return nil }
        return snapshot
    }
}

struct CompanionObservation {
    private(set) var latest: CompanionSnapshot?
    private(set) var reachable = false

    mutating func receive(_ snapshot: CompanionSnapshot?) {
        reachable = snapshot != nil
        if let snapshot { latest = snapshot }
    }
    func isCurrent(at now: Date) -> Bool {
        guard reachable, let latest else { return false }
        return abs(now.timeIntervalSince1970 - latest.observed_at) <= 8
    }
    func active(at now: Date) -> CompanionSnapshot? {
        guard isCurrent(at: now), let latest, latest.recording_id != nil,
              ["starting", "recording", "paused"].contains(latest.phase),
              ["native", "browser"].contains(latest.source ?? "") else { return nil }
        return latest
    }

    func statusTitle(at now: Date) -> String {
        guard isCurrent(at: now), let latest else { return "录音状态待核对" }
        switch latest.phase {
        case "recording": return "正在录音"
        case "paused": return "录音已暂停"
        case "starting": return "正在准备录音"
        case "multiple": return "有多个录音，请在工作台查看"
        case "idle": return "当前没有录音"
        default: return "录音状态待核对"
        }
    }

    func target(at now: Date) -> String? {
        isCurrent(at: now) ? latest?.recording_id : nil
    }
}

// Start and restart both use the same guarded local service manager.
struct ServiceStartPlan {
    let executable: URL
    let arguments: [String]
    let directory: URL
    let environment: [String:String]
    init(_ configuration: HostConfiguration, restartPID: Int? = nil) throws {
        let fm = FileManager.default
        guard fm.isExecutableFile(atPath: configuration.pythonURL.path), fm.fileExists(atPath: configuration.managerURL.path), HostConfiguration.canonical(configuration.managerURL.path) == configuration.managerURL.path else { throw HostFailure.invalidConfiguration }
        executable = configuration.pythonURL
        if let restartPID {
            guard restartPID > 1 else { throw HostFailure.invalidConfiguration }
            arguments = [configuration.managerURL.path, "restart", "--expected-pid", String(restartPID), "--json-errors"]
        } else {
            arguments = [configuration.managerURL.path, "start", "--detached"]
        }
        directory = URL(fileURLWithPath: configuration.projectRoot)
        // Finder launches do not inherit shell setup. Pin paths; do not pass inherited Python/loader overrides.
        var serviceEnvironment = ["PATH":"/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin", "HOME":NSHomeDirectory(),
                       "PORT":String(configuration.port), "MEETING_DATA":configuration.dataRoot, "MEETING_BACKUPS":configuration.backupsRoot,
                       "PYDANTIC_DISABLE_PLUGINS":"__all__"]
        if let runtime = configuration.portableRuntime {
            serviceEnvironment["TINGJI_PORTABLE_SERVICE"] = "1"
            serviceEnvironment["TINGJI_APP_VERSION"] = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String
            serviceEnvironment["TINGJI_CAPTURE_APP"] = runtime.appendingPathComponent("capture/TingjiCapture.app").path
            serviceEnvironment["TINGJI_CAPTURE_DELIVERY_ROOT"] = configuration.dataRoot + "/capture-delivery"
            serviceEnvironment["MEETING_MODELS"] = configuration.dataRoot + "/models"
        }
        environment = serviceEnvironment
    }
}

struct ServiceRestartResult: Decodable {
    let ready: Bool?
    let pid: Int?
    let recoverable: Int?
    let blockers: [String]?
}

final class ServiceStarter {
    func start(_ configuration: HostConfiguration, completion: @escaping (Bool) -> Void) {
        run(configuration, restartPID: nil) { success, _ in completion(success) }
    }

    func restart(_ configuration: HostConfiguration, pid: Int, completion: @escaping (ServiceRestartResult?) -> Void) {
        run(configuration, restartPID: pid) { _, result in completion(result) }
    }

    private func run(_ configuration: HostConfiguration, restartPID: Int?,
                     completion: @escaping (Bool, ServiceRestartResult?) -> Void) {
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                let plan = try ServiceStartPlan(configuration, restartPID: restartPID)
                let process = Process(); process.executableURL = plan.executable; process.arguments = plan.arguments
                process.currentDirectoryURL = plan.directory; process.environment = plan.environment
                let output = Pipe()
                process.standardInput = FileHandle.nullDevice
                process.standardOutput = restartPID == nil ? FileHandle.nullDevice : output
                process.standardError = FileHandle.nullDevice
                try process.run()
                let deadline = Date().addingTimeInterval(restartPID == nil ? 40 : 150)
                while process.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.1) }
                if process.isRunning { process.terminate(); completion(false, nil); return }
                // Restart emits one small, structured reply. Discard arbitrary error text.
                let result: ServiceRestartResult?
                if restartPID != nil {
                    let data = (try? output.fileHandleForReading.read(upToCount: 16_384)) ?? Data()
                    result = try? JSONDecoder().decode(ServiceRestartResult.self, from: data)
                } else {
                    result = nil
                }
                completion(process.terminationStatus == 0, result)
            } catch { completion(false, nil) }
        }
    }
}
