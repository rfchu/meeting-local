import Foundation
import Darwin
import CryptoKit

// Held by the writer, not the API. Kernel releases it on exit, including SIGKILL.
final class CaptureOwnerLease {
    private var descriptor: Int32 = -1
    let incarnation: String
    init(folder: URL, incarnation: String) throws {
        func token(_ value: String) -> Bool { value.count == 32 && value.allSatisfy { "0123456789abcdef".contains($0) } }
        guard token(folder.lastPathComponent), token(incarnation),
              let session = try JSONSerialization.jsonObject(with: Data(contentsOf: folder.appendingPathComponent("session.json"))) as? [String:Any],
              session["id"] as? String == folder.lastPathComponent, session["source"] as? String == "macos",
              ["recording","starting","paused"].contains(session["state"] as? String ?? ""), session["capture_incarnation"] as? String == incarnation else {
            throw NSError(domain: "TingjiOwnership", code: 1, userInfo: [NSLocalizedDescriptionKey:"采集会话身份不匹配，未打开设备"])
        }
        self.incarnation = incarnation
        let root = folder.deletingLastPathComponent().resolvingSymlinksInPath()
        let fd = Darwin.open(root.appendingPathComponent("capture-owner.lock").path, O_RDWR | O_CREAT | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC, 0o600)
        guard fd >= 0 else { throw NSError(domain:"TingjiOwnership",code:2) }
        var info = stat()
        guard fstat(fd,&info) == 0, info.st_mode & S_IFMT == S_IFREG, info.st_uid == getuid(), info.st_mode & 0o077 == 0,
              flock(fd,LOCK_EX | LOCK_NB) == 0 else { Darwin.close(fd); throw NSError(domain:"TingjiOwnership",code:3,userInfo:[NSLocalizedDescriptionKey:"已有采集所有者或锁无法核对，未打开设备"]) }
        descriptor = fd
        do {
            // POSIX canonical spelling must match Python, including /private/var and /private/tmp.
            guard let resolved = realpath(root.path,nil) else { throw NSError(domain:"TingjiOwnership",code:4) }
            let canonical = String(cString:resolved); free(resolved)
            let fingerprint = SHA256.hash(data:Data(canonical.utf8)).map { String(format:"%02x",$0) }.joined()
            let value: [String:Any] = ["protocol_version":1,"recording_id":folder.lastPathComponent,"incarnation":incarnation,
                "workspace_fingerprint":fingerprint,"pid":getpid(),"transport":"stdin_v1","reattach":false]
            let path = root.appendingPathComponent("capture-owner.json")
            let temporary = root.appendingPathComponent(".capture-owner-"+UUID().uuidString)
            let data = try JSONSerialization.data(withJSONObject:value)
            let out = Darwin.open(temporary.path,O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW,0o600)
            guard out >= 0 else { throw NSError(domain:"TingjiOwnership",code:5) }
            defer { Darwin.close(out); try? FileManager.default.removeItem(at:temporary) }
            try data.withUnsafeBytes { bytes in
                var offset=0
                while offset < data.count {
                    let count=Darwin.write(out,bytes.baseAddress!.advanced(by:offset),data.count-offset)
                    if count < 0 && errno == EINTR { continue }
                    guard count > 0 else { throw NSError(domain:"TingjiOwnership",code:6) };offset += count
                }
            }
            guard fsync(out) == 0, rename(temporary.path,path.path) == 0 else { throw NSError(domain:"TingjiOwnership",code:7) }
            let parent = Darwin.open(root.path,O_RDONLY | O_CLOEXEC)
            guard parent >= 0 else { throw NSError(domain:"TingjiOwnership",code:8) }
            defer { Darwin.close(parent) }
            guard fsync(parent) == 0 else { throw NSError(domain:"TingjiOwnership",code:9) }
        } catch { Darwin.close(fd);descriptor = -1;throw error }
    }
    deinit { if descriptor >= 0 { Darwin.close(descriptor) } }
}
