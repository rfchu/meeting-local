import AppKit

final class HostAppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, NSMenuDelegate {
    private var configuration: HostConfiguration
    private let defaults: HostConfiguration
    private let preferences: HostPreferences
    private var loaded: HostConfigurationLoad
    private var settings: HostSettingsController?
    private var companion: CompanionController?
    private var workspace: WorkspaceController?
    private var menuBrowser: NSMenuItem?
    private let browserButton = NSButton(title: "浏览器工作台", target: nil, action: nil)
    private var activationObserver: NSObjectProtocol?
    private var statusItem: NSStatusItem?
    private var menuStatus: NSMenuItem?
    private var menuReturn: NSMenuItem?
    private var observation = CompanionObservation()
    private var observing = false
    private var menuOpen: NSMenuItem?
    private var menuStart: NSMenuItem?
    private var menuSettings: NSMenuItem?
    private var menuRefresh: NSMenuItem?
    private var configurationButton: NSButton?
    private let client = ServiceClient()
    private let starter = ServiceStarter()
    private var generation = ProbeGeneration()
    private var probing = false
    private var opening = false
    private var starting = false
    private var startupDeadline: Date?
    private var configurationGeneration = 0
    private var timer: Timer?
    private var state = ConnectionState.checking
    private var window: NSWindow!
    private let status = NSTextField(labelWithString: "正在核对本机服务")
    private let detail = NSTextField(wrappingLabelWithString: "")
    private let feedback = NSTextField(wrappingLabelWithString: "")
    private let openButton = NSButton(title: "打开内嵌工作区", target: nil, action: nil)
    private let startButton = NSButton(title: "启动本机服务", target: nil, action: nil)
    private let refreshButton = NSButton(title: "重新检查", target: nil, action: nil)

    init(defaults: HostConfiguration, loaded: HostConfigurationLoad, preferences: HostPreferences) {
        self.configuration = loaded.configuration; self.defaults = defaults; self.loaded = loaded; self.preferences = preferences; super.init()
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu(); buildWindow(); buildStatusItem(); probe()
        activationObserver = DistributedNotificationCenter.default().addObserver(forName: HostInstanceLock.activationName(preferences: preferences), object: nil, queue: .main) { [weak self] _ in self?.showWindow() }
        timer = Timer(timeInterval: 5, repeats: true) { [weak self] _ in self?.probe() }
        if let timer { RunLoop.main.add(timer, forMode: .common) }
        NSApp.activate(ignoringOtherApps: true)
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard workspace != nil || (settings?.isPresented == true && settings?.hasUnsavedChanges == true) else { return .terminateNow }
        let alert = NSAlert(); alert.messageText = "退出听记宿主？"
        alert.informativeText = "请先保存内嵌页面和连接设置，并完成上传、下载及会中操作。退出会关闭页面，但不会停止 API 服务或其原生采集。"
        alert.addButton(withTitle: "返回宿主"); alert.addButton(withTitle: "放弃修改并退出")
        return alert.runModal() == .alertFirstButtonReturn ? .terminateCancel : .terminateNow
    }
    func applicationWillTerminate(_ notification: Notification) {
        timer?.invalidate(); settings?.close(); workspace?.dispose(); companion?.close(); client.close()
        if let activationObserver { DistributedNotificationCenter.default().removeObserver(activationObserver) }
        if let statusItem { NSStatusBar.system.removeStatusItem(statusItem) }
        // Closing this UI never sends a stop, restart or recording command to the service.
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showWindow(); return true
    }
    private func label(_ text: String, size: CGFloat = 13, color: NSColor = .secondaryLabelColor) -> NSTextField {
        let value = NSTextField(wrappingLabelWithString: text)
        value.font = .systemFont(ofSize: size); value.textColor = color; return value
    }
    private func buildMenu() {
        let menu = NSMenu(); let app = NSMenuItem(); let submenu = NSMenu()
        submenu.addItem(withTitle: "关于听记宿主", action: #selector(about), keyEquivalent: "")
        let config = submenu.addItem(withTitle: "连接设置…", action: #selector(showConfiguration), keyEquivalent: ","); config.target = self
        submenu.addItem(withTitle: "打开伴随窗口", action: #selector(showCompanion), keyEquivalent: "").target = self
        submenu.addItem(withTitle: "版本诊断…", action: #selector(showDiagnostics), keyEquivalent: "").target = self
        submenu.addItem(.separator())
        let quit = submenu.addItem(withTitle: "退出听记宿主", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"); quit.target = NSApp
        app.submenu = submenu; menu.addItem(app); NSApp.mainMenu = menu
    }
    private func buildWindow() {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 640, height: 560), styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        window.title = "听记 · 本机宿主"; window.delegate = self; window.isReleasedWhenClosed = false
        let root = NSStackView(); root.orientation = .vertical; root.alignment = .leading; root.spacing = 16
        root.translatesAutoresizingMaskIntoConstraints = false
        let content = window.contentView!; content.addSubview(root)
        NSLayoutConstraint.activate([root.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 36), root.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -36), root.topAnchor.constraint(equalTo: content.topAnchor, constant: 32), root.bottomAnchor.constraint(lessThanOrEqualTo: content.bottomAnchor, constant: -28)])
        let title = label("听记", size: 30, color: .labelColor); title.font = .systemFont(ofSize: 30, weight: .semibold)
        root.addArrangedSubview(title)
        root.addArrangedSubview(label("本机会议工作台 · 原生宿主预览", size: 14))
        let divider = NSBox(); divider.boxType = .separator; root.addArrangedSubview(divider)
        divider.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true
        status.font = .systemFont(ofSize: 17, weight: .medium); status.setAccessibilityIdentifier("host-status")
        root.addArrangedSubview(status)
        detail.font = .systemFont(ofSize: 13); detail.textColor = .secondaryLabelColor
        detail.setAccessibilityIdentifier("host-connection-detail"); root.addArrangedSubview(detail)
        for view in [title, status, detail] { view.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true }
        browserButton.target = self; browserButton.action = #selector(openBrowserWorkspace); browserButton.bezelStyle = .rounded
        let controls = NSStackView(views: [openButton, browserButton, startButton, refreshButton]); controls.orientation = .horizontal; controls.spacing = 10
        openButton.target = self; openButton.action = #selector(openWorkspace); openButton.bezelStyle = .rounded; openButton.keyEquivalent = "\r"
        openButton.setAccessibilityIdentifier("host-open-workspace")
        startButton.target = self; startButton.action = #selector(startService); startButton.bezelStyle = .rounded
        startButton.setAccessibilityIdentifier("host-start-service")
        refreshButton.target = self; refreshButton.action = #selector(refresh); refreshButton.bezelStyle = .rounded
        root.addArrangedSubview(controls)
        feedback.font = .systemFont(ofSize: 12); feedback.textColor = .secondaryLabelColor
        feedback.setAccessibilityIdentifier("host-open-feedback"); root.addArrangedSubview(feedback)
        feedback.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true
        root.addArrangedSubview(label("内嵌页面支持会议整理与导出；浏览器工作台仍可独立使用。", size: 13))
        let note = label("关闭窗口后可从菜单栏重新打开。退出宿主不会停止本机服务或独立原生采集。请在工作台结束录音，再升级采集器；浏览器录音仍由页面持有。", size: 12)
        root.addArrangedSubview(note); note.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true
        let config = NSButton(title: "连接设置…", target: self, action: #selector(showConfiguration)); config.bezelStyle = .inline; configurationButton = config
        let menuButton = NSButton(title: "宿主菜单", target: self, action: #selector(showStatusMenu(_:))); menuButton.bezelStyle = .inline
        let quitButton = NSButton(title: "退出宿主", target: NSApp, action: #selector(NSApplication.terminate(_:))); quitButton.bezelStyle = .inline
        let secondary = NSStackView(views: [config, menuButton, quitButton]); secondary.spacing = 20; root.addArrangedSubview(secondary)
        render(); window.center(); window.makeKeyAndOrderFront(nil)
    }
    private func render() {
        status.stringValue = loaded.problem ? "连接配置需要修复" : starting ? "正在请求启动，等待连接确认…" : state.title
        status.textColor = state.canOpen ? .systemGreen : .labelColor
        openButton.isEnabled = state.canOpen && !loaded.problem && !starting && !opening && settings?.isPresented != true
        startButton.isEnabled = state.canStart && !loaded.problem && !starting && !opening && settings?.isPresented != true && (try? ServiceStartPlan(configuration)) != nil
        refreshButton.isEnabled = !probing && !starting && !loaded.problem
        menuRefresh?.isEnabled = refreshButton.isEnabled
        configurationButton?.isEnabled = !starting
        let recordingStatus = observation.statusTitle(at: Date())
        let recording = state.canOpen ? observation.active(at: Date()) : nil
        menuStatus?.title = state.canOpen && !loaded.problem ? recordingStatus : status.stringValue
        menuReturn?.title = recording?.source == "browser" ? "返回本场 · 浏览器提示" : "返回本场"
        menuReturn?.isEnabled = recording != nil && openButton.isEnabled
        statusItem?.button?.title = recording?.phase == "recording" ? " 听记 · 录音中" :
            recording?.phase == "paused" ? " 听记 · 已暂停" : " 听记"
        browserButton.isEnabled = openButton.isEnabled; menuBrowser?.isEnabled = browserButton.isEnabled
        menuOpen?.isEnabled = openButton.isEnabled; menuStart?.isEnabled = startButton.isEnabled; menuSettings?.isEnabled = !starting
        statusItem?.button?.toolTip = "听记 · " + status.stringValue
        if loaded.problem { detail.stringValue = "已保存配置无法使用。请打开连接设置修复；当前没有连接或启动任何服务。"; return }
        switch state {
        case let .connected(pid, mode): detail.stringValue = "\(configuration.workspaceURL.absoluteString)\n服务 PID \(pid) · \(mode) · 工作空间匹配 · \(loaded.saved ? "已保存配置" : "构建默认")"
        case .differentWorkspace: detail.stringValue = "端口上已有其他工作空间的听记服务。请核对连接配置；不会启动第二个实例。"
        case .incompatible: detail.stringValue = "端口响应无法通过宿主协议核对。请更新对应服务或检查端口；不会自动替换。"
        case .unavailable: detail.stringValue = "\(configuration.workspaceURL.absoluteString)\n" + ((try? ServiceStartPlan(configuration)) != nil ? "可重新检查，或明确启动此配置对应的本机服务。" : "当前项目缺少可用 Python 环境或服务管理脚本，请检查连接设置。")
        case .checking: detail.stringValue = "仅连接 127.0.0.1，正在核对协议、项目和数据目录。"
        }
    }
    private func probe() {
        guard !probing, !loaded.problem else { return }
        probing = true; let token = generation.next(); render()
        client.probe(configuration) { [weak self] result in
            DispatchQueue.main.async {
                guard let self, self.generation.accepts(token) else { return }
                self.probing = false; self.state = result
                self.workspace?.updateConnection(result.canOpen)
                if result.canOpen { self.observeRecording() }
                else { self.observation.receive(nil) }
                if result.canOpen || result == .differentWorkspace || result == .incompatible || (self.startupDeadline.map { Date() >= $0 } ?? false) {
                    self.starting = false; self.startupDeadline = nil
                }
                self.render()
            }
        }
    }
    private func observeRecording() {
        guard !observing else { return }
        observing = true
        let operation = configurationGeneration
        client.companion(configuration, mode: .status, recording: nil) { [weak self] snapshot in
            DispatchQueue.main.async {
                guard let self, self.configurationGeneration == operation else { return }
                self.observing = false
                self.observation.receive(self.state.canOpen ? snapshot : nil)
                self.render()
            }
        }
    }

    @objc private func returnToCurrentRecording() {
        guard let expected = observation.active(at: Date()), openButton.isEnabled else { return }
        let operation = configurationGeneration
        client.companion(configuration, mode: .status, recording: nil) { [weak self] snapshot in
            DispatchQueue.main.async {
                guard let self, self.configurationGeneration == operation else { return }
                self.observation.receive(snapshot)
                self.render()
                guard self.openButton.isEnabled,
                      let current = self.observation.active(at: Date()),
                      current.recording_id == expected.recording_id, current.source == expected.source else { return }
                if current.source == "native" {
                    self.openVerifiedWorkspace(recording: current.recording_id)
                } else {
                    self.showWindow()
                    let alert = NSAlert()
                    alert.messageText = "回到浏览器中的本场录音"
                    alert.informativeText = "请切换到开始录音的原浏览器标签页，那里仍控制本次录音。"
                    alert.beginSheetModal(for: self.window)
                }
            }
        }
    }

    @objc private func showDiagnostics() {
        guard !loaded.problem else { return }
        showWindow()
        let operation = configurationGeneration
        client.diagnostics(configuration) { [weak self] report in
            DispatchQueue.main.async {
                guard let self, self.configurationGeneration == operation else { return }
                let present: (String?) -> Void = { [weak self] entry in
                    guard let self, self.configurationGeneration == operation, self.window.attachedSheet == nil else { return }
                    let url = Bundle.main.url(forResource: "delivery", withExtension: "json")
                    let data = url.flatMap { try? Data(contentsOf: $0) }
                    let manifest = data.flatMap { try? JSONSerialization.jsonObject(with: $0) } as? [String: Any]
                    let alert = NSAlert()
                    alert.messageText = "桌面版本诊断"
                    alert.informativeText = report?.summary(boundBackend: manifest?["runtime_digest"] as? String, cleanBuild: (manifest?["source_dirty"] as? Bool).map { !$0 }, loadedEntry: entry)
                        ?? "当前后台无法提供版本诊断。请检查服务连接，旧版本后台需要更新后重启。"
                    alert.beginSheetModal(for: self.window)
                }
                if let workspace = self.workspace { workspace.loadedFrontendEntry(completion: present) }
                else { present(nil) }
            }
        }
    }

    @objc private func refresh() { probe() }
    @objc private func showCompanion() {
        guard !loaded.problem else { return }
        if companion == nil { companion = CompanionController(configuration: configuration, client: client) }
        companion?.returnToRecording = { [weak self] id in self?.openVerifiedWorkspace(recording: id) }
        companion?.show()
    }
    @objc private func openWorkspace() { openVerifiedWorkspace(recording: nil) }
    private func openVerifiedWorkspace(recording: String?) {
        guard (recording != nil || state.canOpen), !loaded.problem, !starting, !opening, settings?.isPresented != true else { return }
        // Verify again at the click; a stale green indicator cannot open a different workspace.
        opening = true; probing = true; let token = generation.next(); render()
        client.probe(configuration) { [weak self] result in
            DispatchQueue.main.async {
                guard let self, self.generation.accepts(token) else { return }
                self.opening = false; self.probing = false; self.state = result; self.render()
                if result.canOpen {
                    if let existing = self.workspace { existing.updateConnection(true); existing.show(); if let recording { existing.showRecording(recording) } }
                    else {
                        let controller = WorkspaceController(configuration: self.configuration)
                        controller.openBrowser = { [weak self] in self?.openBrowserWorkspace() }
                        controller.showHost = { [weak self] in self?.showWindow() }
                        self.workspace = controller; if let recording { controller.showRecording(recording) }; controller.prepareAndLoad()
                    }
                    self.feedback.stringValue = "已打开内嵌工作区；关闭窗口会保留页面。"
                } else { self.feedback.stringValue = "连接状态已变化，未打开工作台；请重新检查。" }
            }
        }
    }
    @objc private func openBrowserWorkspace() {
        guard state.canOpen, !loaded.problem, !starting, !opening else { return }
        opening = true; probing = true; let token = generation.next(); render()
        client.probe(configuration) { [weak self] result in
            DispatchQueue.main.async {
                guard let self, self.generation.accepts(token) else { return }
                self.opening = false; self.probing = false; self.state = result; self.render()
                if result.canOpen { _ = NSWorkspace.shared.open(self.configuration.workspaceURL); self.feedback.stringValue = "已请求默认浏览器打开当前工作台。" }
            }
        }
    }
    @objc private func startService() {
        guard state.canStart, !loaded.problem, !starting, !opening, settings?.isPresented != true else { return }
        starting = true; startupDeadline = Date().addingTimeInterval(50); _ = generation.next(); probing = false; render()
        let operation = configurationGeneration
        starter.start(configuration) { [weak self] success in
            DispatchQueue.main.async {
                guard let self, self.configurationGeneration == operation else { return }
                if !success {
                    self.starting = false; self.startupDeadline = nil
                    let alert = NSAlert(); alert.messageText = "服务启动尚未确认"
                    alert.informativeText = "已有实例、端口占用或运行环境可能阻止启动。请重新检查；宿主不会强制重启或终止录音。"
                    alert.beginSheetModal(for: self.window)
                }
                self.probe()
            }
        }
    }
    @objc private func showWindow() {
        window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
    }
    private func buildStatusItem() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem = item; item.button?.title = " 听记"
        item.button?.image = NSImage(systemSymbolName: "waveform", accessibilityDescription: "听记菜单栏")
        item.button?.image?.isTemplate = true
        let menu = NSMenu(); menu.autoenablesItems = false
        let summary = menu.addItem(withTitle: "正在检查连接", action: nil, keyEquivalent: ""); summary.isEnabled = false; menuStatus = summary
        menu.addItem(.separator())
        func action(_ title: String, _ selector: Selector) -> NSMenuItem {
            let value = menu.addItem(withTitle: title, action: selector, keyEquivalent: ""); value.target = self; return value
        }
        _ = action("显示主窗口", #selector(showWindow))
        menuReturn = action("返回本场", #selector(returnToCurrentRecording))
        menuOpen = action("打开内嵌工作区", #selector(openWorkspace))
        _ = action("打开伴随窗口", #selector(showCompanion))
        menuBrowser = action("浏览器工作台", #selector(openBrowserWorkspace))
        menuSettings = action("连接设置…", #selector(showConfiguration))
        _ = action("版本诊断…", #selector(showDiagnostics))
        menuRefresh = action("重新检查", #selector(refresh))
        menuStart = action("启动本机服务", #selector(startService))
        menu.addItem(.separator())
        let quit = menu.addItem(withTitle: "退出听记宿主（服务继续运行）", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"); quit.target = NSApp
        menu.delegate = self; item.menu = menu; render()
    }
    func menuWillOpen(_ menu: NSMenu) { probe() }
    @objc private func showStatusMenu(_ sender: NSButton) {
        statusItem?.menu?.popUp(positioning: nil, at: NSPoint(x: 0, y: sender.bounds.maxY), in: sender)
    }
    func windowShouldClose(_ sender: NSWindow) -> Bool { settings?.isPresented != true }
    @objc private func showConfiguration() {
        guard !starting else { return }
        showWindow()
        if settings?.isPresented == true { return }
        // Re-read on opening so external edits become visible; active connection changes only on save.
        let latest = preferences.load(defaults: defaults)
        let initial = HostConfigurationLoad(configuration: latest.saved || latest.problem ? latest.configuration : configuration, token: latest.token, saved: latest.saved, problem: latest.problem)
        let controller = HostSettingsController(defaults: defaults, load: initial, preferences: preferences, requiresWorkspaceConfirmation: { [weak self] value in self?.workspace != nil && value != self?.configuration }, canApply: { [weak self] value in value == self?.configuration || self?.workspace?.busy != true }, onCancel: { [weak self] in self?.settings = nil; self?.render() }) { [weak self] value in
            guard let self else { return }
            if value.configuration != self.configuration { self.workspace?.dispose(); self.workspace = nil; self.companion?.close(); self.companion = nil }
            self.observation = CompanionObservation(); self.observing = false
            self.configurationGeneration += 1; self.loaded = value; self.configuration = value.configuration
            _ = self.generation.next(); self.opening = false; self.probing = false; self.state = .checking; self.feedback.stringValue = "连接配置已保存；服务未被启停。"
            self.settings = nil; self.render(); self.probe()
        }
        settings = controller; controller.present(on: window); render()
    }
    @objc private func about() {
        let alert = NSAlert(); alert.messageText = "听记宿主 · E4"
        alert.informativeText = "宿主 0.4.0 · 本机源码运行。应用包、Python 环境、模型与会议数据分别管理；更新宿主不迁移数据。开发签名，未公证发行。\n本机连接与启动底座。连接设置保存在本机，菜单栏可随时打开工作台。关闭窗口保留菜单栏，退出宿主不停止服务；采集仍由原页面／API 管理。"
        alert.beginSheetModal(for: window)
    }
}
