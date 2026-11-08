import Foundation
import AppKit

let arguments = Array(CommandLine.arguments.dropFirst())
let probeOnly = arguments == ["--probe"]
let activateOnly = arguments == ["--activate"]
guard arguments.isEmpty || probeOnly || activateOnly else { fputs("用法：TingjiHost [--probe|--activate]\n", stderr); exit(2) }
guard let configurationURL = Bundle.main.url(forResource: "host-config", withExtension: "json"),
      let bundled = try? HostConfiguration.load(configurationURL),
      let directory = try? HostPreferences.resolveDirectory(),
      let preferences = try? HostPreferences(directory: directory) else {
    fputs("宿主连接或配置目录无效，请检查本机配置。\n", stderr); exit(2)
}
let defaults: HostConfiguration
if Bundle.main.object(forInfoDictionaryKey: "TingjiPortableRuntime") as? Bool == true,
   let resources = Bundle.main.resourceURL,
   let portable = try? HostConfiguration(
       projectRoot: resources.appendingPathComponent("PortableRuntime/code").path,
       dataRoot: directory.appendingPathComponent("data").path,
       backupsRoot: directory.appendingPathComponent("backups").path,
       port: bundled.port) {
    defaults = portable
} else {
    defaults = bundled
}
func loadConnection() -> HostConfigurationLoad {
    if defaults.portableRuntime != nil {
        return HostConfigurationLoad(configuration: defaults, token: nil, saved: false, problem: false)
    }
    return preferences.load(defaults: defaults)
}
func activateOwner() {
    DistributedNotificationCenter.default().postNotificationName(HostInstanceLock.activationName(preferences: preferences), object: nil, userInfo: nil, deliverImmediately: true)
}
if activateOnly {
    guard HostInstanceLock.isRunning(preferences: preferences) else { exit(1) }
    activateOwner(); exit(0)
}
let loaded = loadConnection()
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
if configuration.portableRuntime != nil {
    do { _ = try preferences.workspaceKey(create: true) }
    catch { fputs("无法建立私有工作空间授权，请检查本机配置。\n", stderr); exit(2) }
}
let application = NSApplication.shared
let activeLoad = loadConnection()
let delegate = HostAppDelegate(defaults: defaults, loaded: activeLoad, preferences: preferences)
application.setActivationPolicy(.regular); application.delegate = delegate
withExtendedLifetime((lease, delegate)) { application.run() }
