import Foundation
import AppKit

let arguments = Array(CommandLine.arguments.dropFirst())
let probeOnly = arguments == ["--probe"]
guard arguments.isEmpty || probeOnly else { fputs("用法：TingjiHost [--probe]\n", stderr); exit(2) }
guard let configurationURL = Bundle.main.url(forResource: "host-config", withExtension: "json"),
      let configuration = try? HostConfiguration.load(configurationURL) else {
    fputs("宿主连接配置无效，请重新构建。\n", stderr); exit(2)
}
if probeOnly {
    let client = ServiceClient(); let semaphore = DispatchSemaphore(value: 0)
    var connected = false
    client.probe(configuration) { result in
        connected = result.canOpen
        let output: [String:Any] = ["protocol_version":1, "connected":result.canOpen, "can_start":result.canStart, "state":result.title, "capture_owner":"api_service_or_recording_page", "host_capture_owner":false]
        if let data = try? JSONSerialization.data(withJSONObject: output, options: [.sortedKeys]) { FileHandle.standardOutput.write(data); FileHandle.standardOutput.write(Data([10])) }
        semaphore.signal()
    }
    let wait = semaphore.wait(timeout: .now() + 6); client.close()
    exit(wait == .success && connected ? 0 : 1)
}
let application = NSApplication.shared
let delegate = HostAppDelegate(configuration: configuration)
application.setActivationPolicy(.regular); application.delegate = delegate
application.run()
