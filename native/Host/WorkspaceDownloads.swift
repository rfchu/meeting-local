import AppKit
import WebKit

final class WorkspaceDownloads: NSObject, WKDownloadDelegate {
    let policy: WorkspacePolicy
    weak var window: NSWindow?
    var message: (String) -> Void = { _ in }
    private var downloads: [ObjectIdentifier:WKDownload] = [:]
    private var staging: [ObjectIdentifier:DownloadStaging] = [:]
    private var panels: [ObjectIdentifier:NSSavePanel] = [:]
    private var active = true
    var busy: Bool { !downloads.isEmpty }
    init(policy: WorkspacePolicy) { self.policy = policy }
    func receive(_ download: WKDownload) {
        guard active else { download.cancel(); return }
        downloads[ObjectIdentifier(download)] = download; download.delegate = self
    }
    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let id = ObjectIdentifier(download)
        guard active, let window, (policy.allows(response.url) && (response as? HTTPURLResponse)?.statusCode == 200) || policy.localBlob(response.url) else { completionHandler(nil); remove(id); message("下载来源无法核对，已取消。"); return }
        let panel = NSSavePanel(); panel.title = "保存会议导出"; panel.nameFieldStringValue = WorkspacePolicy.filename(suggestedFilename)
        panel.canCreateDirectories = true; panels[id] = panel
        panel.beginSheetModal(for: window) { [weak self] choice in
            guard let self else { completionHandler(nil); return }
            self.panels.removeValue(forKey: id)
            guard self.active, choice == .OK, let destination = panel.url else { completionHandler(nil); self.remove(id); self.message("已取消保存。"); return }
            do {
                let item = try DownloadStaging(destination: destination); self.staging[id] = item
                completionHandler(item.temporary); self.message("正在下载，完成后保存到所选文件。")
            } catch { completionHandler(nil); self.remove(id); self.message("无法写入所选位置，原文件保持不变。") }
        }
    }
    func download(_ download: WKDownload, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, decisionHandler: @escaping (WKDownload.RedirectPolicy) -> Void) {
        decisionHandler(.cancel); message("下载发生跳转，已取消；请重新核对来源。")
    }
    func downloadDidFinish(_ download: WKDownload) {
        let id = ObjectIdentifier(download)
        guard active, let item = staging[id] else { remove(id); return }
        do { try item.publish(); message("已保存：" + item.destination.lastPathComponent) }
        catch { message((error as? HostPreferenceFailure)?.message ?? "保存未完成，所选原文件保持不变。") }
        remove(id)
    }
    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) { let id = ObjectIdentifier(download); guard downloads[id] != nil else { return }; remove(id); if active { message("下载未完成，原文件保持不变；可重新导出。") } }
    private func remove(_ id: ObjectIdentifier) { staging.removeValue(forKey: id)?.cleanup(); downloads.removeValue(forKey: id); panels.removeValue(forKey: id) }
    func close() {
        active = false
        for panel in Array(panels.values) { panel.cancel(nil) }
        panels.removeAll()
        for download in Array(downloads.values) { download.cancel() }
        downloads.removeAll(); staging.values.forEach { $0.cleanup() }; staging.removeAll()
    }
}
