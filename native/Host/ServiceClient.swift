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
        options.timeoutIntervalForResource = 4
        options.httpShouldSetCookies = false
        options.httpCookieStorage = nil
        options.urlCredentialStorage = nil
        options.urlCache = nil
        options.connectionProxyDictionary = [:]
        session = URLSession(configuration: options, delegate: self, delegateQueue: nil)
    }
    func close() { session.invalidateAndCancel() }
    func probe(_ configuration: HostConfiguration, completion: @escaping (ConnectionState) -> Void) {
        get(configuration.identityURL) { data, code in
            completion(code == 0 ? .unavailable : validateIdentity(data, statusCode: code, configuration: configuration))
        }
    }
    func companion(_ configuration: HostConfiguration, mode: CompanionMode, recording: String?, completion: @escaping (CompanionSnapshot?) -> Void) {
        var url = URLComponents(url: configuration.workspaceURL.appendingPathComponent("api/runtime/companion"), resolvingAgainstBaseURL: false)!
        url.queryItems = [URLQueryItem(name: "mode", value: mode.rawValue)]
        if let recording { url.queryItems?.append(URLQueryItem(name: "rid", value: recording)) }
        get(url.url!) { data, code in
            completion(CompanionSnapshot.verified(data, code: code, configuration: configuration, recording: recording))
        }
    }
    func diagnostics(_ configuration: HostConfiguration, completion: @escaping (HostDiagnostics?) -> Void) {
        get(configuration.workspaceURL.appendingPathComponent("api/runtime/host-diagnostics")) { data, code in
            guard code == 200, let report = try? JSONDecoder().decode(HostDiagnostics.self, from: data),
                  let identity = try? JSONEncoder().encode(report.host),
                  validateIdentity(identity, statusCode: code, configuration: configuration).canOpen else {
                completion(nil)
                return
            }
            completion(report)
        }
    }
    private func get(_ url: URL, completion: @escaping (Data, Int) -> Void) {
        var request = URLRequest(url: url)
        request.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let task = session.dataTask(with: request)
        lock.lock(); replies[task.taskIdentifier] = (Data(), 0, completion); lock.unlock()
        task.resume()
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
        case .bilingual: return "双语"
        case .highlights: return "重点"
        }
    }
}

struct CompanionSnapshot: Decodable {
    let observed_at: Double
    let recording_id: String?
    let meeting_id: String?
    let source: String?
    let phase: String
    let storage: String?
    let asr: String?
    let lines: [String]

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

// Starts only the existing guarded service manager. There is deliberately no stop/restart/capture command.
struct ServiceStartPlan {
    let executable: URL
    let arguments: [String]
    let directory: URL
    let environment: [String:String]
    init(_ configuration: HostConfiguration) throws {
        let fm = FileManager.default
        guard fm.isExecutableFile(atPath: configuration.pythonURL.path), fm.fileExists(atPath: configuration.managerURL.path), HostConfiguration.canonical(configuration.managerURL.path) == configuration.managerURL.path else { throw HostFailure.invalidConfiguration }
        executable = configuration.pythonURL
        arguments = [configuration.managerURL.path, "start", "--detached"]
        directory = URL(fileURLWithPath: configuration.projectRoot)
        // Finder launches do not inherit shell setup. Pin paths; do not pass inherited Python/loader overrides.
        environment = ["PATH":"/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin", "HOME":NSHomeDirectory(),
                       "PORT":String(configuration.port), "MEETING_DATA":configuration.dataRoot, "MEETING_BACKUPS":configuration.backupsRoot,
                       "PYDANTIC_DISABLE_PLUGINS":"__all__"]
    }
}

final class ServiceStarter {
    func start(_ configuration: HostConfiguration, completion: @escaping (Bool) -> Void) {
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                let plan = try ServiceStartPlan(configuration)
                let process = Process(); process.executableURL = plan.executable; process.arguments = plan.arguments
                process.currentDirectoryURL = plan.directory; process.environment = plan.environment
                // Manager output can contain local paths. Keep it out of UI, logs and unbounded pipes.
                process.standardInput = FileHandle.nullDevice
                process.standardOutput = FileHandle.nullDevice; process.standardError = FileHandle.nullDevice
                try process.run()
                let deadline = Date().addingTimeInterval(40)
                while process.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.1) }
                if process.isRunning { process.terminate(); completion(false); return }
                completion(process.terminationStatus == 0)
            } catch { completion(false) }
        }
    }
}
