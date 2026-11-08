import Foundation
import CryptoKit
import Darwin

struct HostPreferenceFailure: Error, LocalizedError {
    let message: String
    var errorDescription: String? { message }
}
struct HostConfigurationLoad {
    let configuration: HostConfiguration
    let token: String?
    let saved: Bool
    let problem: Bool
}

// One private, atomic configuration document. No data directory creation or service action.
final class HostPreferences {
    let directory: URL
    var file: URL { directory.appendingPathComponent("connection.json") }
    init(directory: URL) throws {
        guard directory.path.hasPrefix("/"), !directory.path.contains("\n"), !directory.path.contains("\0") else { throw HostFailure.invalidConfiguration }
        self.directory = URL(fileURLWithPath: HostConfiguration.canonical(directory.path))
    }
    static func resolveDirectory(environment: [String:String] = ProcessInfo.processInfo.environment, bundle: Bundle = .main) throws -> URL {
        let standalone = bundle.object(forInfoDictionaryKey: "TingjiPortableRuntime") as? Bool == true
        let domain = standalone ? "cn.seeknow.tingji.host.standalone" : "cn.seeknow.tingji.host"
        let path = environment["TINGJI_HOST_STATE_DIR"]
            ?? (bundle.object(forInfoDictionaryKey: "TingjiHostStateDirectory") as? String)
            ?? NSHomeDirectory() + "/Library/Application Support/" + domain
        guard path.hasPrefix("/"), !path.contains("\0"), !path.contains("\n") else { throw HostFailure.invalidConfiguration }
        return URL(fileURLWithPath: path)
    }
    func prepareDirectory() throws {
        let fm = FileManager.default
        if !fm.fileExists(atPath: directory.path) { try fm.createDirectory(at: directory, withIntermediateDirectories: true, attributes: [.posixPermissions:0o700]) }
        var info = stat()
        guard lstat(directory.path, &info) == 0, info.st_mode & S_IFMT == S_IFDIR, info.st_uid == getuid(), info.st_mode & 0o077 == 0 else { throw HostPreferenceFailure(message: "宿主配置目录必须是当前用户的私有目录。") }
    }
    private func raw(at target: URL? = nil) throws -> Data? {
        var parent = stat()
        if lstat(directory.path, &parent) == 0 && (parent.st_mode & S_IFMT != S_IFDIR || parent.st_uid != getuid() || parent.st_mode & 0o077 != 0) { throw HostPreferenceFailure(message: "宿主配置目录不是当前用户的私有目录。") }
        let fd = Darwin.open((target ?? file).path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
        if fd < 0 {
            if errno == ENOENT { return nil }
            throw HostPreferenceFailure(message: "无法安全读取宿主配置，请检查文件权限。")
        }
        defer { Darwin.close(fd) }
        var info = stat()
        guard fstat(fd, &info) == 0, info.st_mode & S_IFMT == S_IFREG, info.st_uid == getuid(), info.st_mode & 0o077 == 0, info.st_size <= 65_536 else { throw HostPreferenceFailure(message: "宿主配置文件权限或大小不符合要求。") }
        var result = Data(); var buffer = [UInt8](repeating: 0, count: 4096)
        while true {
            let count = Darwin.read(fd, &buffer, buffer.count)
            if count < 0 { if errno == EINTR { continue }; throw HostPreferenceFailure(message: "读取宿主配置失败。") }
            if count == 0 { return result }
            result.append(contentsOf: buffer.prefix(count))
            if result.count > 65_536 { throw HostPreferenceFailure(message: "宿主配置过大，未读取。") }
        }
    }
    private func token(_ value: Data?) -> String { value.map { HostConfiguration.dataFingerprint($0) } ?? "missing" }
    func load(defaults: HostConfiguration) -> HostConfigurationLoad {
        do {
            guard let data = try raw() else { return HostConfigurationLoad(configuration: defaults, token: "missing", saved: false, problem: false) }
            do {
                let value = try HostConfiguration.decode(data)
                return HostConfigurationLoad(configuration: value, token: token(data), saved: true, problem: false)
            } catch { return HostConfigurationLoad(configuration: defaults, token: token(data), saved: false, problem: true) }
        } catch { return HostConfigurationLoad(configuration: defaults, token: nil, saved: false, problem: true) }
    }
    private func writePrivate(_ data: Data, to target: URL) throws {
        let fd = Darwin.open(target.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
        guard fd >= 0 else { throw HostPreferenceFailure(message: "无法写入宿主配置，原配置保持不变。") }
        defer { Darwin.close(fd) }
        try data.withUnsafeBytes { bytes in
            var offset = 0
            while offset < data.count {
                let count = Darwin.write(fd, bytes.baseAddress!.advanced(by: offset), data.count-offset)
                if count < 0 && errno == EINTR { continue }
                guard count > 0 else { throw HostPreferenceFailure(message: "保存宿主配置失败。") }
                offset += count
            }
        }
        guard fsync(fd) == 0 else { throw HostPreferenceFailure(message: "配置写入尚未确认。") }
    }
    func workspaceKey(create: Bool = false) throws -> Data {
        let target = directory.appendingPathComponent("workspace.key")
        if let existing = try raw(at: target) {
            guard existing.count == 32 else { throw HostFailure.invalidConfiguration }
            return existing
        }
        guard create else { throw HostFailure.invalidConfiguration }
        let key = SymmetricKey(size: .bits256).withUnsafeBytes { Data($0) }
        let temporary = directory.appendingPathComponent(".workspace-" + UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: temporary) }
        try writePrivate(key, to: temporary)
        guard renamex_np(temporary.path, target.path, UInt32(RENAME_EXCL)) == 0 else { throw HostFailure.invalidConfiguration }
        return key
    }
    func save(_ configuration: HostConfiguration, expectedToken: String?, preservePrevious: Bool = false) throws -> HostConfigurationLoad {
        guard let expectedToken else { throw HostPreferenceFailure(message: "现有配置无法安全读取，未覆盖；请先检查文件权限。") }
        _ = try HostConfiguration.decode(JSONEncoder().encode(configuration))
        try prepareDirectory()
        let previous = try raw()
        guard token(previous) == expectedToken else { throw HostPreferenceFailure(message: "配置已被其他操作修改，请重新打开设置后再保存。") }
        let data = try JSONEncoder().encode(configuration)
        guard data.count <= 65_536 else { throw HostFailure.invalidConfiguration }
        let temporary = directory.appendingPathComponent(".connection-" + UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: temporary) }
        try writePrivate(data, to: temporary)
        if preservePrevious, let previous {
            try writePrivate(previous, to: directory.appendingPathComponent("connection-invalid-" + UUID().uuidString + ".json"))
        }
        guard token(try raw()) == expectedToken else { throw HostPreferenceFailure(message: "保存期间配置已变化，请重新核对。") }
        guard Darwin.rename(temporary.path, file.path) == 0 else { throw HostPreferenceFailure(message: "替换宿主配置失败，原配置保持不变。") }
        return HostConfigurationLoad(configuration: configuration, token: HostConfiguration.dataFingerprint(data), saved: true, problem: false)
    }
}

// A kernel-held lease, independent of API/data locks. Stale lock files do not imply a live owner.
final class HostInstanceLock {
    private var descriptor: Int32 = -1
    init?(preferences: HostPreferences) throws {
        try preferences.prepareDirectory()
        let path = preferences.directory.appendingPathComponent("host.lock").path
        let fd = Darwin.open(path, O_RDWR | O_CREAT | O_NOFOLLOW | O_NONBLOCK, 0o600)
        guard fd >= 0 else { throw HostPreferenceFailure(message: "无法核对已有宿主实例。") }
        var info = stat()
        guard fstat(fd, &info) == 0, info.st_mode & S_IFMT == S_IFREG, info.st_uid == getuid(), info.st_mode & 0o077 == 0 else { Darwin.close(fd); throw HostFailure.invalidConfiguration }
        if flock(fd, LOCK_EX | LOCK_NB) != 0 {
            let busy = errno == EWOULDBLOCK || errno == EAGAIN; Darwin.close(fd)
            if busy { return nil }
            throw HostPreferenceFailure(message: "无法取得宿主运行锁。")
        }
        descriptor = fd
    }
    deinit { if descriptor >= 0 { flock(descriptor, LOCK_UN); Darwin.close(descriptor) } }
    static func isRunning(preferences: HostPreferences) -> Bool {
        let fd = Darwin.open(preferences.directory.appendingPathComponent("host.lock").path, O_RDWR | O_NOFOLLOW | O_NONBLOCK)
        guard fd >= 0 else { return false }
        defer { Darwin.close(fd) }
        if flock(fd, LOCK_EX | LOCK_NB) == 0 { flock(fd, LOCK_UN); return false }
        return errno == EWOULDBLOCK || errno == EAGAIN
    }
    static func activationName(preferences: HostPreferences) -> Notification.Name {
        Notification.Name("cn.seeknow.tingji.host.activate." + HostConfiguration.fingerprint(preferences.directory.path))
    }
}
