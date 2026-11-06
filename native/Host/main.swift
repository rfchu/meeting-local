import Foundation
import AppKit

let arguments = Array(CommandLine.arguments.dropFirst())
let probeOnly = arguments == ["--probe"]
let activateOnly = arguments == ["--activate"]
guard arguments.isEmpty || probeOnly || activateOnly else { fputs("用法：TingjiHost [--probe|--activate]\n", stderr); exit(2) }
guard let configurationURL = Bundle.main.url(forResource: "host-config", withExtension: "json"),
      let defaults = try? HostConfiguration.load(configurationURL),
      let directory = try? HostPreferences.resolveDirectory(),
      let preferences = try? HostPreferences(directory: directory) else {
    fputs("宿主连接或配置目录无效，请检查本机配置。\n", stderr); exit(2)
}
func activateOwner() {
    DistributedNotificationCenter.default().postNotificationName(HostInstanceLock.activationName(preferences: preferences), object: nil, userInfo: nil, deliverImmediately: true)
}
if activateOnly {
    guard HostInstanceLock.isRunning(preferences: preferences) else { exit(1) }
    activateOwner(); exit(0)
}
let loaded = preferences.load(defaults: defaults)
let configuration = loaded.configuration
if probeOnly {
    guard !loaded.problem else { print("{\"connected\":false,\"configuration_error\":true}"); exit(2) }
    let client = ServiceClient(); let semaphore = DispatchSemaphore(value: 0)
    var connected = false
    client.probe(configuration) { result in
        connected = result.canOpen
        let output: [String:Any] = ["protocol_version":1, "connected":result.canOpen, "can_start":result.canStart, "state":result.title, "capture_owner":"api_service_or_recording_page", "host_capture_owner":false, "configuration_source":loaded.saved ? "saved" : "bundled"]
        if let data = try? JSONSerialization.data(withJSONObject: output, options: [.sortedKeys]) { FileHandle.standardOutput.write(data); FileHandle.standardOutput.write(Data([10])) }
        semaphore.signal()
    }
    let wait = semaphore.wait(timeout: .now() + 6); client.close()
    exit(wait == .success && connected ? 0 : 1)
}
let lease: HostInstanceLock
do {
    guard let acquired = try HostInstanceLock(preferences: preferences) else { activateOwner(); exit(0) }
    lease = acquired
} catch { fputs("宿主运行锁不可用，未启动新实例。\n", stderr); exit(2) }
let application = NSApplication.shared
let activeLoad = preferences.load(defaults: defaults)
let delegate = HostAppDelegate(defaults: defaults, loaded: activeLoad, preferences: preferences)
application.setActivationPolicy(.regular); application.delegate = delegate
withExtendedLifetime((lease, delegate)) { application.run() }
