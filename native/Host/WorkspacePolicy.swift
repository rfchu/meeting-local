import Foundation
import Darwin

struct WorkspacePolicy {
    let configuration: HostConfiguration
    var binding: String { HostConfiguration.dataFingerprint(Data(("v1:" + HostConfiguration.fingerprint(configuration.projectRoot) + ":" + HostConfiguration.fingerprint(configuration.dataRoot)).utf8)) }
    var storeIdentifier: UUID {
        let digest = HostConfiguration.dataFingerprint(Data((binding + ":" + String(configuration.port)).utf8))
        let value = String(digest.prefix(32)); let parts = [value.prefix(8), value.dropFirst(8).prefix(4), value.dropFirst(12).prefix(4), value.dropFirst(16).prefix(4), value.dropFirst(20).prefix(12)]
        return UUID(uuidString: parts.map(String.init).joined(separator: "-"))!
    }
    func allows(_ url: URL?) -> Bool {
        guard let url, let parts = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return false }
        return parts.scheme == "http" && parts.host == "127.0.0.1" && parts.port == configuration.port && parts.user == nil && parts.password == nil
    }
    func localBlob(_ url: URL?) -> Bool {
        guard let text = url?.absoluteString, text.hasPrefix("blob:" + configuration.workspaceURL.absoluteString) else { return false }
        return UUID(uuidString: String(text.dropFirst(("blob:" + configuration.workspaceURL.absoluteString).count))) != nil
    }
    func preview(_ url: URL?) -> Bool {
        guard allows(url), let url else { return false }
        return url.path.range(of: "^/api/meetings/[a-f0-9]{32}/minutes/export/pdf$", options: .regularExpression) != nil
    }
    var networkRules: String {
        let origin = "http://127[.]0[.]0[.]1:" + String(configuration.port) + "/"
        let rules: [[String:Any]] = [
            ["trigger":["url-filter":".*"],"action":["type":"block"]],
            ["trigger":["url-filter":"^"+origin],"action":["type":"ignore-previous-rules"]],
            ["trigger":["url-filter":"^blob:"+origin],"action":["type":"ignore-previous-rules"]]]
        return String(data: try! JSONSerialization.data(withJSONObject: rules), encoding: .utf8)!
    }
    static func filename(_ suggestion: String) -> String {
        let name = suggestion.replacingOccurrences(of: "\\", with: "/").components(separatedBy: "/").last ?? ""
        let filtered = name.unicodeScalars.filter { !CharacterSet.controlCharacters.contains($0) && $0 != ":" }
        let clean = String(String.UnicodeScalarView(filtered)).trimmingCharacters(in: .whitespacesAndNewlines)
        return clean.isEmpty || clean == "." || clean == ".." ? "会议导出.download" : String(clean.prefix(140))
    }
}

private struct DestinationStamp: Equatable {
    let device: Int32; let inode: UInt64; let size: Int64; let seconds: Int; let nanos: Int
    static func read(_ url: URL) throws -> DestinationStamp? {
        var info = stat()
        if lstat(url.path, &info) != 0 {
            if errno == ENOENT { return nil }; throw HostFailure.invalidConfiguration
        }
        guard info.st_mode & S_IFMT == S_IFREG else { throw HostFailure.invalidConfiguration }
        return DestinationStamp(device: info.st_dev, inode: info.st_ino, size: info.st_size, seconds: info.st_mtimespec.tv_sec, nanos: info.st_mtimespec.tv_nsec)
    }
}

// Only a user-selected file can be published. Original files are never deleted before completion.
final class DownloadStaging {
    let destination: URL
    let folder: URL
    let temporary: URL
    private let original: DestinationStamp?
    init(destination: URL) throws {
        guard destination.isFileURL else { throw HostFailure.invalidConfiguration }
        self.destination = destination; original = try DestinationStamp.read(destination)
        folder = destination.deletingLastPathComponent().appendingPathComponent(".tingji-download-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: false, attributes: [.posixPermissions:0o700])
        temporary = folder.appendingPathComponent("download")
    }
    func publish() throws {
        guard try DestinationStamp.read(destination) == original else { throw HostPreferenceFailure(message: "目标文件在下载期间变化，未覆盖；请重新导出。") }
        guard try DestinationStamp.read(temporary) != nil else { throw HostFailure.invalidResponse }
        if original == nil {
            guard renamex_np(temporary.path, destination.path, UInt32(RENAME_EXCL)) == 0 else { throw HostPreferenceFailure(message: "目标文件已存在或无法保存，未覆盖。") }
        } else {
            _ = try FileManager.default.replaceItemAt(destination, withItemAt: temporary)
        }
    }
    func cleanup() { try? FileManager.default.removeItem(at: folder) }
    deinit { cleanup() }
}
