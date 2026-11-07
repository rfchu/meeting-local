import Foundation
import CryptoKit
import Darwin

// The helper itself holds this across API/broker exit. Self-test/probe never opens devices.
final class CaptureArtifactLease {
    private var descriptor:Int32 = -1
    init(executable:String) throws {
        guard let resolved=realpath(executable,nil) else { throw NSError(domain:"TingjiArtifact",code:1) }
        let canonical=String(cString:resolved);free(resolved)
        let base=ProcessInfo.processInfo.environment["TINGJI_CAPTURE_DELIVERY_ROOT"] ?? NSHomeDirectory()+"/Library/Caches/cn.seeknow.tingji/capture-delivery"
        guard base.hasPrefix("/"), !base.contains("\n") else { throw NSError(domain:"TingjiArtifact",code:2) }
        let hash=SHA256.hash(data:Data(canonical.utf8)).map { String(format:"%02x",$0) }.joined()
        let folder=URL(fileURLWithPath:base).appendingPathComponent(hash)
        for url in [URL(fileURLWithPath:base),folder] {
            try FileManager.default.createDirectory(at:url,withIntermediateDirectories:true,attributes:[.posixPermissions:0o700])
            var info=stat()
            guard lstat(url.path,&info)==0, info.st_mode & S_IFMT == S_IFDIR, info.st_uid==getuid(), info.st_mode & 0o077==0 else { throw NSError(domain:"TingjiArtifact",code:3) }
        }
        let fd=Darwin.open(folder.appendingPathComponent("binary.lock").path,O_RDWR | O_CREAT | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC,0o600)
        guard fd>=0 else { throw NSError(domain:"TingjiArtifact",code:4) }
        var info=stat()
        guard fstat(fd,&info)==0, info.st_mode & S_IFMT == S_IFREG, info.st_uid==getuid(), info.st_mode & 0o077==0, flock(fd,LOCK_SH | LOCK_NB)==0 else { Darwin.close(fd);throw NSError(domain:"TingjiArtifact",code:5,userInfo:[NSLocalizedDescriptionKey:"采集器正在更新，未打开设备"]) }
        descriptor=fd
    }
    deinit { if descriptor>=0 { Darwin.close(descriptor) } }
}
