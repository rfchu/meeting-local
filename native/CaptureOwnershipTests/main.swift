// No AppKit, ScreenCaptureKit, microphone, model or API controls.
import Foundation
import Darwin
let args=CommandLine.arguments
guard args.count == 3 else { exit(2) }
do {
    let folder=URL(fileURLWithPath:args[1])
    let lease=try CaptureOwnerLease(folder:folder,incarnation:args[2])
    print("held");fflush(stdout)
    withExtendedLifetime(lease) {
        while let command=readLine() {
            if command == "write" {
                let path=folder.appendingPathComponent("system.pcm")
                if !FileManager.default.fileExists(atPath:path.path) { FileManager.default.createFile(atPath:path.path,contents:nil) }
                if let handle=try? FileHandle(forWritingTo:path) { try? handle.seekToEnd();try? handle.write(contentsOf:Data([1,0,2,0]));try? handle.synchronize();try? handle.close() }
                print("written");fflush(stdout)
            }
            if command == "stop" { break }
        }
    }
} catch { fputs("ownership rejected\n",stderr);exit(3) }
