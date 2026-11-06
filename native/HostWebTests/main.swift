// Synthetic local fixture only. Does not request device access or run models.
import AppKit
import WebKit
import Foundation
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let configuration = try HostConfiguration.load(Bundle.main.url(forResource: "host-config", withExtension: "json")!)
let controller = WorkspaceController(configuration: configuration)
var checks = 0
func spin(_ seconds: Double = 0.2) { RunLoop.current.run(until: Date().addingTimeInterval(seconds)) }
func check(_ value: Bool, _ label: String) { guard value else { fputs("FAIL: \(label)\n", stderr); exit(1) }; checks += 1 }
func js(_ code: String) -> Any? {
    var done = false; var output: Any?
    controller.webView.evaluateJavaScript(code) { value, _ in output = value; done = true }
    let deadline = Date().addingTimeInterval(5)
    while !done && Date() < deadline { spin(0.05) }
    return output
}
func mode(_ value: String) {
    var done = false
    var request = URLRequest(url: configuration.workspaceURL.appendingPathComponent("_fixture/host/" + value)); request.httpMethod = "POST"
    let session = URLSession(configuration: .ephemeral)
    session.dataTask(with: request) { _, _, _ in done = true }.resume()
    let deadline = Date().addingTimeInterval(5)
    while !done && Date() < deadline { spin(0.05) }
    session.invalidateAndCancel(); check(done, "fixture control")
}
controller.prepareAndLoad()
let deadline = Date().addingTimeInterval(15)
while Date() < deadline && js("document.title") as? String != "听记 E3 合成工作区" { spin() }
check(js("document.title") as? String == "听记 E3 合成工作区", "real WebKit loaded bound fixture")
check(js("window.__TINGJI_HOST__.web_capture === false") as? Bool == true, "permission marker injected")
check(js("Object.getOwnPropertyDescriptor(navigator.mediaDevices,'getUserMedia').writable === false") as? Bool == true, "media override locked without device calls")
_ = js("document.getElementById('draft').value='E3 retained synthetic draft'; true")
controller.window.performClose(nil); spin(); check(!controller.window.isVisible, "close hides")
controller.show(); check(js("document.getElementById('draft').value") as? String == "E3 retained synthetic draft", "reopen preserves draft")
_ = js("document.querySelector('a[href=\"https://example.com/\"]').click(); true"); spin(0.5)
check(controller.webView.url?.host == "127.0.0.1", "external navigation denied")
_ = js("document.querySelector('a[href=\"/local\"]').click(); true"); spin(1)
check(js("document.querySelector('h1').textContent") as? String == "本机导航成功", "local navigation allowed")
_ = js("document.querySelector('a').click(); true"); spin(1)
_ = js("document.getElementById('draft').value='preserve across mismatch'; true")
mode("mismatch")
_ = js("fetch('/write',{method:'POST'}).then(r=>document.getElementById('result').textContent=String(r.status));true"); spin(1)
check(js("document.getElementById('result').textContent") as? String == "409", "cookie rejects reused port writes")
controller.updateConnection(false); check(controller.webView.isHidden, "mismatch shields view")
mode("ready"); controller.updateConnection(true)
check(js("document.getElementById('draft').value") as? String == "preserve across mismatch", "reconnect preserves DOM without reload")
_ = js("document.querySelector('a[target=\"_blank\"]').click();true"); spin(2)
check(app.windows.contains { $0.title == "听记 · 纪要预览" }, "trusted PDF opens separate preview")
if let preview = app.windows.first(where: { $0.title == "听记 · 纪要预览" }) {
    preview.performClose(nil); spin(); check(!preview.isVisible, "preview closes instead of retaining hidden window")
} else { check(false, "preview exists before close") }
controller.dispose()
print("{\"suite\":\"native-webkit-e3\",\"passed\":\(checks),\"capture\":false,\"models\":false}")
