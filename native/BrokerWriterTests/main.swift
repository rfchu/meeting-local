// E6 synthetic helper: shared E5 kernel lease, periodic four-byte PCM, no device frameworks.
import Foundation
import Darwin
let args=CommandLine.arguments
func option(_ key:String)->String { let i=args.firstIndex(of:key)!;return args[i+1] }
let folder=URL(fileURLWithPath:option("--output"));let incarnation=option("--incarnation")
do {
    let lease=try CaptureOwnerLease(folder:folder,incarnation:incarnation)
    let lock=NSLock();var state="recording";var frames=0
    func append(_ name:String,_ bytes:Data) {
        let url=folder.appendingPathComponent(name)
        if !FileManager.default.fileExists(atPath:url.path) { FileManager.default.createFile(atPath:url.path,contents:nil) }
        let file=try! FileHandle(forWritingTo:url);try! file.seekToEnd();try! file.write(contentsOf:bytes);try! file.synchronize();try! file.close()
    }
    func report() {
        let value:[String:Any] = ["state":state,"duration":Double(frames)/16000,"error":"","capture_incarnation":incarnation,"system_frames":frames,"sample_rate":16000,"updated":Date().timeIntervalSince1970]
        try! JSONSerialization.data(withJSONObject:value).write(to:folder.appendingPathComponent("capture.json"),options:.atomic)
    }
    let timer=DispatchSource.makeTimerSource(queue:.global());timer.schedule(deadline:.now(),repeating:.milliseconds(40))
    timer.setEventHandler { lock.lock();defer { lock.unlock() };if state=="recording" { append("system.pcm",Data([1,0,2,0]));frames+=2 };report() };timer.resume()
    withExtendedLifetime(lease) {
        while let command=readLine() {
            lock.lock()
            append("commands.log",Data((command+"\n").utf8))
            let ignore=(try? String(contentsOf:folder.appendingPathComponent("test-fault"),encoding:.utf8)) == "ignore_pause"
            if command=="pause" && !ignore { state="paused" }
            if command=="resume" { state="recording" }
            if command=="stop" { state="stopped" }
            report();lock.unlock()
            if command=="stop" { break }
        }
        timer.cancel();lock.lock();state="stopped";report();lock.unlock()
    }
} catch { fputs("synthetic writer ownership rejected\n",stderr);exit(3) }
