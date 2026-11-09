import AppKit
import Carbon.HIToolbox

final class HostAppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, NSMenuDelegate, NSMenuItemValidation {
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
    private var menuServiceState: NSMenuItem?
    private var menuVersion: NSMenuItem?
    private var serviceMenu: NSMenu?
    private var restarting = false
    private var menuSettings: NSMenuItem?
    private var menuRefresh: NSMenuItem?
    private var configurationButton: NSButton?
    private let client = ServiceClient()
    private let starter = ServiceStarter()
    private var generation = ProbeGeneration()
    private var probing = false
    private var opening = false
    private var openOnConnection = false
    private var attemptedStartup = false
    private var starting = false
    private var startupDeadline: Date?
    private var configurationGeneration = 0
    private var timer: Timer?
    private var companionHotkey: EventHotKeyRef?
    private var companionHotkeyHandler: EventHandlerRef?
    private var state = ConnectionState.checking
    private var window: NSWindow!
    private let status = NSTextField(labelWithString: "正在核对本机服务")
    private let detail = NSTextField(wrappingLabelWithString: "")
    private let feedback = NSTextField(wrappingLabelWithString: "")
    private let openButton = NSButton(title: "打开内嵌工作区", target: nil, action: nil)

    init(defaults: HostConfiguration, loaded: HostConfigurationLoad, preferences: HostPreferences) {
        self.configuration = loaded.configuration; self.defaults = defaults; self.loaded = loaded; self.preferences = preferences; super.init()
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        openOnConnection = configuration.portableRuntime != nil
        buildMenu(); buildWindow(); buildStatusItem(); probe(); setupHotkey()
        activationObserver = DistributedNotificationCenter.default().addObserver(forName: HostInstanceLock.activationName(preferences: preferences), object: nil, queue: .main) { [weak self] _ in self?.reopenWorkspace() }
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
        if let companionHotkey { UnregisterEventHotKey(companionHotkey) }
        if let companionHotkeyHandler { RemoveEventHandler(companionHotkeyHandler) }
        if let activationObserver { DistributedNotificationCenter.default().removeObserver(activationObserver) }
        if let statusItem { NSStatusBar.system.removeStatusItem(statusItem) }
        // Closing this UI never sends a stop, restart or recording command to the service.
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        reopenWorkspace(); return true
    }
    func application(_ application: NSApplication, open urls: [URL]) {
        NSApp.activate(ignoringOtherApps: true)
        reopenWorkspace()
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
        submenu.addItem(withTitle: "打开浏览器工作台", action: #selector(openBrowserWorkspace), keyEquivalent: "").target = self
        submenu.addItem(.separator())
        submenu.addItem(withTitle: "重新载入工作区…", action: #selector(reloadWorkspace), keyEquivalent: "r").target = self
        submenu.addItem(withTitle: "返回宿主", action: #selector(showWindow), keyEquivalent: "").target = self
        submenu.addItem(.separator())
        let quit = submenu.addItem(withTitle: "退出听记宿主", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"); quit.target = NSApp
        app.submenu = submenu; menu.addItem(app)

        // Nil targets let AppKit dispatch these commands to the focused WebKit or native field.
        let edit = NSMenuItem(title: "编辑", action: nil, keyEquivalent: "")
        let editMenu = NSMenu(title: "编辑")
        editMenu.addItem(withTitle: "剪切", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "复制", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "粘贴", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "全选", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        edit.submenu = editMenu
        menu.addItem(edit)
        NSApp.mainMenu = menu
    }
    func validateMenuItem(_ menuItem: NSMenuItem) -> Bool {
        if menuItem.action == #selector(openBrowserWorkspace) {
            return browserButton.isEnabled
        }
        if menuItem.action == #selector(reloadWorkspace) {
            return workspace?.canReload == true && !starting && !restarting
        }
        return true
    }

    @objc private func reloadWorkspace() {
        guard !starting, !restarting else { return }
        workspace?.reloadWorkspace()
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
        let controls = NSStackView(views: [openButton, browserButton]); controls.orientation = .horizontal; controls.spacing = 10
        openButton.target = self; openButton.action = #selector(openWorkspace); openButton.bezelStyle = .rounded; openButton.keyEquivalent = "\r"
        openButton.setAccessibilityIdentifier("host-open-workspace")
        root.addArrangedSubview(controls)
        feedback.font = .systemFont(ofSize: 12); feedback.textColor = .secondaryLabelColor
        feedback.setAccessibilityIdentifier("host-open-feedback"); root.addArrangedSubview(feedback)
        feedback.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true
        root.addArrangedSubview(label("内嵌页面支持会议整理与导出；浏览器工作台仍可独立使用。", size: 13))
        let note = label("关闭窗口后可从菜单栏重新打开。退出宿主不会停止本机服务或独立原生采集。请在工作台结束录音，再升级采集器；浏览器录音仍由页面持有。", size: 12)
        root.addArrangedSubview(note); note.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true
        let config = NSButton(title: "连接设置…", target: self, action: #selector(showConfiguration)); config.bezelStyle = .inline; configurationButton = config
        let menuButton = NSButton(title: "服务状态…", target: self, action: #selector(showServiceMenu(_:))); menuButton.bezelStyle = .inline
        let quitButton = NSButton(title: "退出宿主", target: NSApp, action: #selector(NSApplication.terminate(_:))); quitButton.bezelStyle = .inline
        let secondary = NSStackView(views: [config, menuButton, quitButton]); secondary.spacing = 20; root.addArrangedSubview(secondary)
        render(); window.center(); window.makeKeyAndOrderFront(nil)
    }
    private func render() {
        status.stringValue = loaded.problem ? "连接配置需要修复" : restarting ? "正在重启后台…" : starting ? "正在请求启动，等待连接确认…" : state.title
        status.textColor = state.canOpen ? .systemGreen : .labelColor
        openButton.isEnabled = state.canOpen && !loaded.problem && !starting && !opening && settings?.isPresented != true
        let canManage = !loaded.problem && !starting && !opening && settings?.isPresented != true
            && (try? ServiceStartPlan(configuration)) != nil
        menuStart?.title = state.canOpen ? "重启后台…" : "启动后台"
        menuStart?.isEnabled = canManage && (state.canStart || state.canOpen)
        menuServiceState?.title = status.stringValue
        menuRefresh?.isEnabled = !probing && !starting && !loaded.problem
        configurationButton?.isEnabled = !starting
        let recordingStatus = observation.statusTitle(at: Date())
        let recording = state.canOpen ? observation.active(at: Date()) : nil
        menuStatus?.title = state.canOpen && !loaded.problem ? recordingStatus : status.stringValue
        menuReturn?.title = recording?.source == "browser" ? "返回本场 · 浏览器提示" : "返回本场"
        menuReturn?.isEnabled = recording != nil && openButton.isEnabled
        statusItem?.button?.title = recording?.phase == "recording" ? " 听记 · 录音中" :
            recording?.phase == "paused" ? " 听记 · 已暂停" : " 听记"
        browserButton.isEnabled = openButton.isEnabled; menuBrowser?.isEnabled = browserButton.isEnabled
        menuOpen?.isEnabled = openButton.isEnabled; menuSettings?.isEnabled = !starting
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
        guard !probing, !restarting, !loaded.problem else { return }
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
                guard self.openOnConnection else { return }
                if result.canOpen {
                    self.openOnConnection = false
                    self.openVerifiedWorkspace(recording: nil, findCurrent: true)
                } else if result.canStart && !self.attemptedStartup && !self.starting {
                    self.attemptedStartup = true
                    self.startService()
                }
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
    @objc private func toggleCompanion() {
        guard !loaded.problem else { return }
        if companion == nil { companion = CompanionController(configuration: configuration, client: client) }
        companion?.returnToRecording = { [weak self] id in self?.openVerifiedWorkspace(recording: id) }
        companion?.toggle()
    }
    private func setupHotkey() {
        let identifier = EventHotKeyID(signature: 0x544A484B, id: 1) // TJHK
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyReleased))
        let target = GetApplicationEventTarget()
        let install = InstallEventHandler(target, { _, event, context in
            guard let event, let context else { return OSStatus(eventNotHandledErr) }
            var key = EventHotKeyID()
            let result = GetEventParameter(event, EventParamName(kEventParamDirectObject),
                                           EventParamType(typeEventHotKeyID), nil,
                                           MemoryLayout<EventHotKeyID>.size, nil, &key)
            guard result == noErr, key.signature == 0x544A484B, key.id == 1 else {
                return OSStatus(eventNotHandledErr)
            }
            let delegate = Unmanaged<HostAppDelegate>.fromOpaque(context).takeUnretainedValue()
            delegate.toggleCompanion()
            return noErr
        }, 1, &spec, Unmanaged.passUnretained(self).toOpaque(), &companionHotkeyHandler)
        guard install == noErr else {
            NSLog("伴随窗口快捷键处理器安装失败（%d）", install)
            return
        }
        let registered = RegisterEventHotKey(UInt32(kVK_ANSI_N), UInt32(controlKey | optionKey),
                                             identifier, target, 0, &companionHotkey)
        if registered != noErr {
            if let companionHotkeyHandler { RemoveEventHandler(companionHotkeyHandler) }
            companionHotkeyHandler = nil
            NSLog("伴随窗口快捷键注册失败（%d），可从宿主菜单打开", registered)
        }
    }
    private func reopenWorkspace() {
        guard configuration.portableRuntime != nil else { showWindow(); return }
        if let workspace, state.canOpen { workspace.show(); return }
        showWindow()
        openOnConnection = true
        probe()
    }

    @objc private func openWorkspace() {
        openVerifiedWorkspace(recording: nil, findCurrent: configuration.portableRuntime != nil)
    }

    private func openVerifiedWorkspace(recording: String?, findCurrent: Bool = false) {
        guard (recording != nil || state.canOpen), !loaded.problem, !starting, !opening, settings?.isPresented != true else { return }
        opening = true; probing = true; let token = generation.next(); render()
        client.probe(configuration) { [weak self] result in
            DispatchQueue.main.async {
                guard let self, self.generation.accepts(token) else { return }
                self.state = result
                guard result.canOpen else {
                    self.opening = false; self.probing = false; self.render()
                    self.feedback.stringValue = "连接状态已变化，请重新检查。"
                    return
                }
                if findCurrent && self.workspace == nil {
                    self.client.companion(self.configuration, mode: .status, recording: nil) { [weak self] snapshot in
                        DispatchQueue.main.async {
                            guard let self, self.generation.accepts(token) else { return }
                            var current = CompanionObservation()
                            current.receive(snapshot)
                            let active = current.active(at: Date())
                            self.presentWorkspace(recording: active?.source == "native" ? active?.recording_id : nil)
                        }
                    }
                } else {
                    self.presentWorkspace(recording: recording)
                }
            }
        }
    }

    private func presentWorkspace(recording: String?) {
        opening = false; probing = false; render()
        if let existing = workspace {
            existing.updateConnection(true)
            if let recording { existing.showRecording(recording) }
            existing.show()
        } else {
            let controller = WorkspaceController(configuration: configuration)
            workspace = controller
            if let recording { controller.showRecording(recording) }
            controller.prepareAndLoad()
        }
        if configuration.portableRuntime != nil { window.orderOut(nil) }
        feedback.stringValue = "已打开内嵌工作区；关闭窗口会保留页面。"
    }
    @objc private func openBrowserWorkspace() {
        guard state.canOpen, !loaded.problem, !starting, !opening else { return }
        opening = true; probing = true; let token = generation.next(); render()
        if configuration.portableRuntime != nil {
            client.browserWorkspace(configuration) { [weak self] target in
                DispatchQueue.main.async {
                    guard let self, self.generation.accepts(token) else { return }
                    self.opening = false; self.probing = false; self.render()
                    if let target, NSWorkspace.shared.open(target) {
                        self.feedback.stringValue = "已在默认浏览器打开会议工作台。"
                    } else {
                        self.feedback.stringValue = "暂时无法授权浏览器，请稍后重试。"
                    }
                }
            }
            return
        }
        client.probe(configuration) { [weak self] result in
            DispatchQueue.main.async {
                guard let self, self.generation.accepts(token) else { return }
                self.state = result
                guard result.canOpen else {
                    self.opening = false; self.probing = false; self.render()
                    return
                }
                self.client.companion(self.configuration, mode: .status, recording: nil) { [weak self] snapshot in
                    DispatchQueue.main.async {
                        guard let self, self.generation.accepts(token) else { return }
                        self.opening = false; self.probing = false; self.render()
                        var url = URLComponents(url: self.configuration.workspaceURL, resolvingAgainstBaseURL: false)
                        var observation = CompanionObservation()
                        observation.receive(snapshot)
                        if let active = observation.active(at: Date()), let id = active.recording_id {
                            url?.fragment = "companion-recording=" + id
                        }
                        if let target = url?.url { _ = NSWorkspace.shared.open(target) }
                        self.feedback.stringValue = "已请求默认浏览器打开当前工作台。"
                    }
                }
            }
        }
    }
    @objc private func manageService() {
        if case let .connected(pid, _) = state { restartService(pid: pid) }
        else { startService() }
    }

    private func restartService(pid: Int) {
        guard !loaded.problem, !starting, !opening, settings?.isPresented != true else { return }
        showWindow()
        guard window.attachedSheet == nil else { return }
        let alert = NSAlert()
        alert.messageText = "重启后台？"
        alert.informativeText = "请先保存工作台中的编辑。后台会检查录音和任务，重启期间工作台会短暂断开。"
        alert.addButton(withTitle: "取消"); alert.addButton(withTitle: "重启后台")
        alert.beginSheetModal(for: window) { [weak self] choice in
            guard choice == .alertSecondButtonReturn, let self, !self.starting else { return }
            guard self.workspace?.busy != true else {
                self.feedback.stringValue = "请先完成内嵌工作区中的文件操作。"; return
            }
            self.restarting = true; self.starting = true
            _ = self.generation.next(); self.probing = false; self.render()
            let operation = self.configurationGeneration
            self.starter.restart(self.configuration, pid: pid) { [weak self] result in
                DispatchQueue.main.async {
                    guard let self, self.configurationGeneration == operation else { return }
                    if result?.ready == true, let newPID = result?.pid {
                        self.client.diagnostics(self.configuration) { [weak self] report in
                            DispatchQueue.main.async {
                                guard let self, self.configurationGeneration == operation else { return }
                                let verified = report?.host.pid == newPID && report?.running_backend == report?.disk_backend
                                    && report?.frontend.verified == true
                                let recovery = (result?.recoverable ?? 0) > 0 ? " 旧录音可在运行管理 → 录音恢复中查看。" : ""
                                self.feedback.stringValue = (verified
                                    ? "后台已重启，新版本和网页资源已核对。已有页面可在保存后刷新。"
                                    : "后台已重启，版本尚未核对。请查看服务状态中的版本诊断。") + recovery
                                self.finishServiceOperation()
                            }
                        }
                    } else {
                        self.finishServiceOperation()
                        self.showRestartFailure(result?.blockers ?? [])
                    }
                }
            }
        }
    }

    private func finishServiceOperation() {
        restarting = false; starting = false; startupDeadline = nil
        render(); probe()
    }

    private func showRestartFailure(_ blockers: [String]) {
        let messages = [
            "capture_active": "仍在录音或写入音频。请回到开始录音的工作台结束录音。",
            "capture_unverified": "录音状态待确认。请打开录音恢复核对。",
            "jobs_active": "后台任务仍在运行或排队。",
            "service_unverified": "后台实例身份已变化或无法核对。请重新检查连接。",
            "stop_timeout": "原后台尚未确认退出。请重新检查服务状态。",
            "start_timeout": "已请求启动，后台尚未确认就绪。请重新检查服务状态。",
        ]
        let alert = NSAlert()
        alert.messageText = "后台重启尚未完成"
        let details = blockers.compactMap { messages[$0] }
        alert.informativeText = details.isEmpty ? "请在服务状态中重新检查，或查看本机服务日志。" : details.joined(separator: "\n")
        alert.addButton(withTitle: "关闭")
        let recovery = blockers.contains("capture_unverified")
        let activeCapture = blockers.contains("capture_active")
        let jobs = blockers.contains("jobs_active")
        if recovery { alert.addButton(withTitle: "查看录音恢复") }
        else if activeCapture && observation.active(at: Date())?.source == "native" { alert.addButton(withTitle: "返回本场") }
        else if jobs { alert.addButton(withTitle: "查看后台任务") }
        alert.beginSheetModal(for: window) { [weak self] choice in
            guard choice == .alertSecondButtonReturn, let self, self.state.canOpen else { return }
            if activeCapture && !recovery && self.observation.active(at: Date())?.source == "native" {
                self.returnToCurrentRecording(); return
            }
            guard var target = URLComponents(url: self.configuration.workspaceURL, resolvingAgainstBaseURL: false) else { return }
            target.fragment = recovery ? "management/recovery" : "management/tasks"
            if let url = target.url { NSWorkspace.shared.open(url) }
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
        let serviceItem = menu.addItem(withTitle: "服务状态", action: nil, keyEquivalent: "")
        let services = NSMenu(title: "服务状态")
        services.autoenablesItems = false; services.delegate = self; serviceMenu = services
        serviceItem.submenu = services
        menuServiceState = services.addItem(withTitle: "正在检查后台", action: nil, keyEquivalent: "")
        menuServiceState?.isEnabled = false
        menuVersion = services.addItem(withTitle: "版本尚未核对", action: nil, keyEquivalent: "")
        menuVersion?.isEnabled = false
        services.addItem(.separator())
        menuStart = services.addItem(withTitle: "启动后台", action: #selector(manageService), keyEquivalent: "")
        menuRefresh = services.addItem(withTitle: "重新检查", action: #selector(refresh), keyEquivalent: "")
        let diagnostics = services.addItem(withTitle: "版本诊断…", action: #selector(showDiagnostics), keyEquivalent: "")
        [menuStart, menuRefresh, diagnostics].forEach { $0?.target = self }
        menu.addItem(.separator())
        let quit = menu.addItem(withTitle: "退出听记宿主（服务继续运行）", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"); quit.target = NSApp
        menu.delegate = self; item.menu = menu; render()
    }
    func menuWillOpen(_ menu: NSMenu) {
        probe()
        guard menu === serviceMenu else { return }
        menuVersion?.title = "版本尚未核对"
        guard state.canOpen, !starting else { return }
        let operation = configurationGeneration
        client.diagnostics(configuration) { [weak self] report in
            DispatchQueue.main.async {
                guard let self, self.configurationGeneration == operation, let report, !self.starting else { return }
                let current = report.running_backend == report.disk_backend
                self.menuVersion?.title = current ? "后台版本匹配 · " + report.running_backend.prefix(8)
                    : "本机已有新版，重启后生效"
            }
        }
    }
    @objc private func showServiceMenu(_ sender: NSButton) {
        serviceMenu?.popUp(positioning: nil, at: NSPoint(x: 0, y: sender.bounds.maxY), in: sender)
    }
    func windowShouldClose(_ sender: NSWindow) -> Bool { settings?.isPresented != true }
    @objc private func showConfiguration() {
        guard !starting, configuration.portableRuntime == nil else { return }
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
