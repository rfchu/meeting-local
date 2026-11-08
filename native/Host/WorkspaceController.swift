import AppKit
import WebKit

final class WorkspaceController: NSObject, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate {
    let policy: WorkspacePolicy
    let window: NSWindow
    private(set) var webView: WKWebView
    private let notice = NSTextField(wrappingLabelWithString: "正在准备工作区…")
    private let shield = NSTextField(wrappingLabelWithString: "正在核对连接…")
    private let downloads: WorkspaceDownloads
    private var previews: [WorkspaceController] = []
    private var valid = true
    private var permissionBoundaryReady = false
    private var prepared = false
    private var preparing = false
    private var contentFailed = false
    private var disposed = false
    private var pendingRecording: String?
    private var choosingFile = false
    private var openPanel: NSOpenPanel?
    private let previewOnly: Bool
    private let initialURL: URL
    var openBrowser: () -> Void = {}
    var showHost: () -> Void = {}
    var didClosePreview: () -> Void = {}
    var busy: Bool { choosingFile || downloads.busy || previews.contains { $0.busy } }
    init(configuration: HostConfiguration, webConfiguration supplied: WKWebViewConfiguration? = nil, previewOnly: Bool = false, initialURL: URL? = nil) {
        policy = WorkspacePolicy(configuration: configuration); self.previewOnly = previewOnly; self.initialURL = initialURL ?? configuration.workspaceURL
        let options = supplied ?? WKWebViewConfiguration()
        if supplied == nil {
            options.websiteDataStore = WKWebsiteDataStore(forIdentifier: policy.storeIdentifier)
            options.preferences.javaScriptCanOpenWindowsAutomatically = false
            options.preferences.isElementFullscreenEnabled = false
            if let path = Bundle.main.url(forResource: "workspace-permissions", withExtension: "js"), let script = try? String(contentsOf: path, encoding: .utf8) {
                options.userContentController.addUserScript(WKUserScript(source: script, injectionTime: .atDocumentStart, forMainFrameOnly: false))
                permissionBoundaryReady = true
            }
        }
        webView = WKWebView(frame: .zero, configuration: options)
        downloads = WorkspaceDownloads(policy: policy)
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1180, height: 800), styleMask: [.titled,.closable,.miniaturizable,.resizable], backing: .buffered, defer: false)
        super.init()
        window.title = previewOnly ? "听记 · 纪要预览" : "听记 · 会议工作区"; window.delegate = self; window.isReleasedWhenClosed = false
        window.minSize = NSSize(width: 900, height: 600); window.isRestorable = false
        webView.navigationDelegate = self; webView.uiDelegate = self
        downloads.window = window; downloads.message = { [weak self] text in self?.notice.stringValue = text }
        let root = NSStackView(); root.orientation = .vertical; root.alignment = .leading; root.spacing = 8; root.distribution = .fill; root.translatesAutoresizingMaskIntoConstraints = false
        window.contentView!.addSubview(root)
        NSLayoutConstraint.activate([root.leadingAnchor.constraint(equalTo: window.contentView!.leadingAnchor, constant: 12), root.trailingAnchor.constraint(equalTo: window.contentView!.trailingAnchor, constant: -12),root.topAnchor.constraint(equalTo: window.contentView!.topAnchor, constant: 10),root.bottomAnchor.constraint(equalTo: window.contentView!.bottomAnchor, constant: -10)])
        let reload = NSButton(title: "重新载入", target: self, action: #selector(reloadWorkspace)); reload.bezelStyle = .rounded
        let browser = NSButton(title: "浏览器工作台", target: self, action: #selector(externalBrowser)); browser.bezelStyle = .rounded
        let host = NSButton(title: "返回宿主", target: self, action: #selector(backToHost)); host.bezelStyle = .rounded
        let back = NSButton(title: "后退", target: self, action: #selector(goBack)); back.bezelStyle = .rounded
        let forward = NSButton(title: "前进", target: self, action: #selector(goForward)); forward.bezelStyle = .rounded
        let controls = NSStackView(views: configuration.portableRuntime == nil ? [back,forward,reload,browser,host] : [back,forward,reload,host]); controls.spacing = 12; root.addArrangedSubview(controls)
        notice.font = .systemFont(ofSize: 12); notice.textColor = .secondaryLabelColor; root.addArrangedSubview(notice)
        notice.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true
        let content = NSView(); root.addArrangedSubview(content); content.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true
        content.heightAnchor.constraint(greaterThanOrEqualToConstant: 400).isActive = true
        webView.translatesAutoresizingMaskIntoConstraints = false; content.addSubview(webView)
        shield.translatesAutoresizingMaskIntoConstraints = false; shield.font = .systemFont(ofSize: 18); shield.alignment = .center; content.addSubview(shield)
        NSLayoutConstraint.activate([webView.leadingAnchor.constraint(equalTo: content.leadingAnchor),webView.trailingAnchor.constraint(equalTo: content.trailingAnchor),webView.topAnchor.constraint(equalTo: content.topAnchor),webView.bottomAnchor.constraint(equalTo: content.bottomAnchor),shield.centerXAnchor.constraint(equalTo: content.centerXAnchor),shield.centerYAnchor.constraint(equalTo: content.centerYAnchor),shield.widthAnchor.constraint(lessThanOrEqualTo: content.widthAnchor, constant: -40)])
        window.center(); window.makeKeyAndOrderFront(nil)
        if supplied != nil { permissionBoundaryReady = true; prepared = true; webView.isHidden = false; shield.isHidden = true; updateConnection(true) }
    }
    func prepareAndLoad() {
        guard !preparing, !disposed else { return }
        guard permissionBoundaryReady else { shield.stringValue = "权限边界未就绪，请返回宿主重新打开。"; return }
        preparing = true
        let ruleID = "tingji-e3-" + policy.storeIdentifier.uuidString
        WKContentRuleListStore.default().compileContentRuleList(forIdentifier: ruleID, encodedContentRuleList: policy.networkRules) { [weak self] rules, error in
            guard let self, !self.disposed else { return }
            guard let rules, error == nil else { self.preparing = false; self.shield.stringValue = "网络边界初始化失败，请明确重试载入。"; self.message("工作区未载入，可点击重新载入重试。"); return }
            self.webView.configuration.userContentController.add(rules)
            let configuration = self.policy.configuration
            var credentials = [("tingji_workspace_binding", self.policy.binding)]
            if configuration.portableRuntime != nil {
                guard let authorization = configuration.workspaceAuthorization else {
                    self.preparing = false
                    self.shield.stringValue = "工作空间授权不可用，请返回宿主重新打开。"
                    return
                }
                credentials.append(("tingji_workspace_authorization", authorization))
            }
            let cookies = self.webView.configuration.websiteDataStore.httpCookieStore
            let ready = DispatchGroup()
            for (name, value) in credentials {
                let cookie = HTTPCookie(properties: [.name:name,.value:value,.domain:"127.0.0.1",.path:"/",HTTPCookiePropertyKey("HttpOnly"):"TRUE",HTTPCookiePropertyKey("SameSite"):"Strict"])!
                ready.enter()
                cookies.setCookie(cookie) { ready.leave() }
            }
            ready.notify(queue: .main) { [weak self] in
                guard let self, !self.disposed else { return }
                self.preparing = false; self.prepared = true; self.updateConnection(self.valid)
                if self.valid { self.webView.load(URLRequest(url: configuration.workspaceURL)) }
            }
        }
    }
    func updateConnection(_ verified: Bool) {
        valid = verified
        webView.isHidden = !verified || !prepared || contentFailed
        shield.isHidden = verified && prepared && !contentFailed
        if !verified { webView.stopLoading(); shield.stringValue = "服务连接无法核对，工作区已暂停。\n请返回宿主检查连接；当前页面保留。"; notice.stringValue = "连接中断或工作空间变化；未自动重载。" }
        else if contentFailed { shield.stringValue = "页面进程已结束，请核对已保存内容，再明确重新载入。" }
        else if prepared { notice.stringValue = "本机工作区 · 浏览器采集未启用 · 关闭此窗口会保留页面" }
        previews.forEach { $0.updateConnection(verified) }
    }
    func show() { window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true) }
    func loadedFrontendEntry(completion: @escaping (String?) -> Void) {
        guard valid, prepared, !contentFailed, policy.allows(webView.url), webView.url?.path == "/" else {
            completion(nil)
            return
        }
        webView.callAsyncJavaScript("""
            if (location.origin !== origin) return null;
            const entry = document.querySelector('script[type="module"][src^="/assets/"]');
            return entry?.getAttribute("src") ?? null;
            """, arguments: ["origin": "http://127.0.0.1:\(policy.configuration.port)"], in: nil, in: .page) { result in
                completion((try? result.get()) as? String)
            }
    }

    func showRecording(_ id: String) {
        guard !previewOnly, id.range(of: #"^[0-9a-f]{32}$"#, options: .regularExpression) != nil else { return }
        pendingRecording = id
        deliverRecordingTarget()
    }
    private func deliverRecordingTarget() {
        guard let id = pendingRecording, valid, prepared, !contentFailed, !webView.isLoading,
              let url = webView.url, policy.allows(url), url.path == "/" else { return }
        pendingRecording = nil
        webView.callAsyncJavaScript("""
            if (location.origin === origin && location.pathname === "/") {
                location.hash = "companion-recording=" + recording;
            }
            """, arguments: ["origin": "http://127.0.0.1:\(policy.configuration.port)", "recording": id],
            in: nil, in: .page) { _ in }
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) { deliverRecordingTarget() }
    func windowShouldClose(_ sender: NSWindow) -> Bool {
        if previewOnly { return true }
        window.orderOut(nil); return false
    }
    func windowWillClose(_ notification: Notification) {
        if previewOnly && !disposed { disposed = true; downloads.close(); webView.stopLoading(); webView.navigationDelegate = nil; webView.uiDelegate = nil; didClosePreview() }
    }
    func dispose() {
        disposed = true; downloads.close(); openPanel?.cancel(nil); previews.forEach { $0.dispose() }; previews.removeAll()
        webView.stopLoading(); webView.navigationDelegate = nil; webView.uiDelegate = nil; window.delegate = nil; window.close()
    }
    private func message(_ text: String) { notice.stringValue = text }
    @objc private func goBack() { if valid && !busy && webView.canGoBack { webView.goBack() } }
    @objc private func goForward() { if valid && !busy && webView.canGoForward { webView.goForward() } }
    @objc private func backToHost() { showHost() }
    @objc private func externalBrowser() { openBrowser() }
    @objc private func reloadWorkspace() {
        guard valid, !busy else { message("请先完成或取消文件选择与下载。"); return }
        if !prepared { prepareAndLoad(); return }
        let alert = NSAlert(); alert.messageText = "重新载入内嵌页面？"; alert.informativeText = "请先保存编辑并完成会中操作；重新载入不会停止 API 服务。"
        alert.addButton(withTitle: "取消"); alert.addButton(withTitle: "重新载入")
        alert.beginSheetModal(for: window) { [weak self] choice in if choice == .alertSecondButtonReturn, self?.valid == true { if let self { self.contentFailed = false; self.updateConnection(true); if self.webView.url == nil { self.webView.load(URLRequest(url: self.initialURL)) } else { self.webView.reload() } } } }
    }
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard valid, !disposed else { decisionHandler(.cancel); return }
        let url = navigationAction.request.url
        if url?.absoluteString == "about:blank" && webView.url == nil { decisionHandler(.allow); return }
        if navigationAction.shouldPerformDownload && (policy.allows(url) || policy.localBlob(url)) { decisionHandler(.download); return }
        guard policy.allows(url), !previewOnly || policy.preview(url) else { decisionHandler(.cancel); message("已阻止离开当前本机工作空间的导航。可使用浏览器工作台处理外部链接。"); return }
        decisionHandler(.allow)
    }
    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse, decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        if valid && policy.localBlob(navigationResponse.response.url) { decisionHandler(.download); return }
        guard valid, policy.allows(navigationResponse.response.url), (navigationResponse.response as? HTTPURLResponse)?.statusCode != 409 else { decisionHandler(.cancel); updateConnection(false); return }
        let disposition = (navigationResponse.response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Disposition")?.lowercased() ?? ""
        decisionHandler(!navigationResponse.canShowMIMEType || disposition.hasPrefix("attachment") ? .download : .allow)
    }
    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { downloads.receive(download) }
    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { downloads.receive(download) }
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        guard valid, !previewOnly, previews.count < 4, policy.preview(navigationAction.request.url) else { message("此新窗口未获允许；仅在独立窗口打开本机纪要 PDF 预览。"); return nil }
        let child = WorkspaceController(configuration: policy.configuration, webConfiguration: configuration, previewOnly: true, initialURL: navigationAction.request.url)
        child.openBrowser = openBrowser; child.showHost = showHost
        child.didClosePreview = { [weak self, weak child] in self?.previews.removeAll { $0 === child } }
        previews.append(child); return child.webView
    }
    func webView(_ webView: WKWebView, requestMediaCapturePermissionFor origin: WKSecurityOrigin, initiatedByFrame frame: WKFrameInfo, type: WKMediaCaptureType, decisionHandler: @escaping (WKPermissionDecision) -> Void) { decisionHandler(.deny); message("内嵌页面不申请采集权限。请使用已有原生音轨模式或浏览器工作台。") }
    @available(macOS 27.0, *)
    func webView(_ webView: WKWebView, requestGeolocationPermissionFor origin: WKSecurityOrigin, initiatedByFrame frame: WKFrameInfo, decisionHandler: @escaping (WKPermissionDecision) -> Void) { decisionHandler(.deny) }
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        guard valid, frame.isMainFrame, policy.allows(frame.request.url), !choosingFile else { completionHandler(nil); return }
        let panel = NSOpenPanel(); panel.canChooseFiles = true; panel.canChooseDirectories = false; panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        choosingFile = true; openPanel = panel
        panel.beginSheetModal(for: window) { [weak self] response in
            guard let self else { completionHandler(nil); return }
            self.choosingFile = false; self.openPanel = nil; completionHandler(self.valid && response == .OK ? panel.urls : nil)
        }
    }
    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) { self.message(String(message.prefix(1000))); completionHandler() }
    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        guard valid, frame.isMainFrame, policy.allows(frame.request.url) else { completionHandler(false); return }
        let alert = NSAlert(); alert.messageText = String(message.prefix(1000)); alert.addButton(withTitle: "取消"); alert.addButton(withTitle: "确认")
        alert.beginSheetModal(for: window) { [weak self] result in completionHandler(self?.valid == true && result == .alertSecondButtonReturn) }
    }
    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String, defaultText: String?, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (String?) -> Void) { completionHandler(nil); message("请使用页面中的编辑表单；内嵌窗口不执行脚本输入提示。") }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) { if (error as NSError).code != NSURLErrorCancelled { message("页面加载失败，可检查连接后重新载入；未切换到其他地址。") } }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { contentFailed = true; updateConnection(valid); message("内嵌页面进程已结束，请先核对已保存内容，再明确重新载入。") }
}
