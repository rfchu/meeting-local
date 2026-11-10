import AppKit
import Carbon.HIToolbox

final class HostAppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate, NSMenuItemValidation {
    private var configuration: HostConfiguration
    private let defaults: HostConfiguration
    private let preferences: HostPreferences
    private var loaded: HostConfigurationLoad
    private var settings: HostSettingsController?
    private var companion: CompanionController?
    private var workspace: WorkspaceController?
    private var menuBrowser: NSMenuItem?
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
    private let client = ServiceClient()
    private let starter = ServiceStarter()
    private var generation = ProbeGeneration()
    private var probing = false
    private var opening = false
    private var openOnConnection = true
    private var attemptedStartup = false
    private var starting = false
    private var startupDeadline: Date?
    private var configurationGeneration = 0
    private var timer: Timer?
    private var companionHotkey: EventHotKeyRef?
    private var companionHotkeyHandler: EventHandlerRef?
    private var state = ConnectionState.checking

    init(defaults: HostConfiguration, loaded: HostConfigurationLoad, preferences: HostPreferences) {
        self.configuration = loaded.configuration; self.defaults = defaults; self.loaded = loaded; self.preferences = preferences; super.init()
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        openOnConnection = true
        buildMenu(); buildStatusItem(); setupHotkey()
        activationObserver = DistributedNotificationCenter.default().addObserver(forName: HostInstanceLock.activationName(preferences: preferences), object: nil, queue: .main) { [weak self] _ in self?.reopenWorkspace() }
        timer = Timer(timeInterval: 5, repeats: true) { [weak self] _ in self?.probe() }
        if let timer { RunLoop.main.add(timer, forMode: .common) }
        NSApp.activate(ignoringOtherApps: true)
        probe()
        if configuration.portableRuntime != nil || state.canOpen {
            openVerifiedWorkspace(recording: nil, findCurrent: true)
        }
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard workspace != nil || (settings?.isPresented == true && settings?.hasUnsavedChanges == true) else { return .terminateNow }
        let alert = NSAlert(); alert.messageText = "退出听记？"
        alert.informativeText = "请先保存内嵌页面和连接设置，并完成上传、下载及会中操作。退出会关闭页面，但不会停止 API 服务或其原生采集。"
        alert.addButton(withTitle: "取消"); alert.addButton(withTitle: "放弃修改并退出")
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
    private func buildMenu() {
        let menu = NSMenu(); let app = NSMenuItem(); let submenu = NSMenu()
        submenu.addItem(withTitle: "关于听记", action: #selector(about), keyEquivalent: "")
        let config = submenu.addItem(withTitle: "连接设置…", action: #selector(showConfiguration), keyEquivalent: ","); config.target = self
        submenu.addItem(withTitle: "打开伴随窗口", action: #selector(showCompanion), keyEquivalent: "").target = self
        submenu.addItem(withTitle: "打开浏览器工作台", action: #selector(openBrowserWorkspace), keyEquivalent: "").target = self
        submenu.addItem(.separator())
        submenu.addItem(withTitle: "重新载入工作区…", action: #selector(reloadWorkspace), keyEquivalent: "r").target = self
        submenu.addItem(withTitle: "显示工作区", action: #selector(showWorkspace), keyEquivalent: "0").target = self
        submenu.addItem(.separator())
        let quit = submenu.addItem(withTitle: "退出听记", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"); quit.target = NSApp
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
            return state.canOpen && !loaded.problem && !starting && !opening
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

    private func render() {
        let statusString = loaded.problem ? "连接配置需要修复" : restarting ? "正在重启后台…" : starting ? "正在请求启动，等待连接确认…" : state.title
        let canOpen = state.canOpen && !loaded.problem && !starting && !opening && settings?.isPresented != true
        let canManage = !loaded.problem && !starting && !opening && settings?.isPresented != true
            && (try? ServiceStartPlan(configuration)) != nil
        menuStart?.title = state.canOpen ? "重启后台…" : "启动后台"
        menuStart?.isEnabled = canManage && (state.canStart || state.canOpen)
        menuServiceState?.title = statusString
        menuRefresh?.isEnabled = !probing && !starting && !loaded.problem
        let recordingStatus = observation.statusTitle(at: Date())
        let recording = state.canOpen ? observation.active(at: Date()) : nil
        menuStatus?.title = state.canOpen && !loaded.problem ? recordingStatus : statusString
        menuReturn?.title = recording?.source == "browser" ? "返回本场 · 浏览器提示" : "返回本场"
        menuReturn?.isEnabled = recording != nil && canOpen
        statusItem?.button?.title = recording?.phase == "recording" ? " 听记 · 录音中" :
            recording?.phase == "paused" ? " 听记 · 已暂停" : " 听记"
        menuBrowser?.isEnabled = canOpen
        menuOpen?.isEnabled = true
        menuSettings?.isEnabled = !starting
        statusItem?.button?.toolTip = "听记 · " + statusString
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

    private func presentAlert(_ alert: NSAlert, completionHandler: ((NSApplication.ModalResponse) -> Void)? = nil) {
        NSApp.activate(ignoringOtherApps: true)
        if let target = workspace?.window, target.isVisible, target.attachedSheet == nil {
            alert.beginSheetModal(for: target, completionHandler: completionHandler)
        } else {
            let response = alert.runModal()
            completionHandler?(response)
        }
    }

    @objc private func returnToCurrentRecording() {
        guard let expected = observation.active(at: Date()), state.canOpen else { return }
        let operation = configurationGeneration
        client.companion(configuration, mode: .status, recording: nil) { [weak self] snapshot in
            DispatchQueue.main.async {
                guard let self, self.configurationGeneration == operation else { return }
                self.observation.receive(snapshot)
                self.render()
                guard self.state.canOpen,
                      let current = self.observation.active(at: Date()),
                      current.recording_id == expected.recording_id, current.source == expected.source else { return }
                if current.source == "native" {
                    self.openVerifiedWorkspace(recording: current.recording_id)
                } else {
                    let alert = NSAlert()
                    alert.messageText = "回到浏览器中的本场录音"
                    alert.informativeText = "请切换到开始录音的原浏览器标签页，那里仍控制本次录音。"
                    self.presentAlert(alert)
                }
            }
        }
    }

    @objc private func showDiagnostics() {
        guard !loaded.problem else { return }
        let operation = configurationGeneration
        client.diagnostics(configuration) { [weak self] report in
            DispatchQueue.main.async {
                guard let self, self.configurationGeneration == operation else { return }
                let present: (String?) -> Void = { [weak self] entry in
                    guard let self, self.configurationGeneration == operation else { return }
                    let url = Bundle.main.url(forResource: "delivery", withExtension: "json")
                    let data = url.flatMap { try? Data(contentsOf: $0) }
                    let manifest = data.flatMap { try? JSONSerialization.jsonObject(with: $0) } as? [String: Any]
                    let alert = NSAlert()
                    alert.messageText = "桌面版本诊断"
                    alert.informativeText = report?.summary(boundBackend: manifest?["runtime_digest"] as? String, cleanBuild: (manifest?["source_dirty"] as? Bool).map { !$0 }, loadedEntry: entry)
                        ?? "当前后台无法提供版本诊断。请检查服务连接，旧版本后台需要更新后重启。"
                    self.presentAlert(alert)
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
        if let workspace {
            workspace.show()
            return
        }
        openOnConnection = true
        probe()
    }

    @objc private func showWorkspace() {
        if let workspace {
            workspace.show()
        } else if state.canOpen {
            openVerifiedWorkspace(recording: nil, findCurrent: true)
        } else {
            openOnConnection = true
            probe()
        }
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
    }
    @objc private func openBrowserWorkspace() {
        guard state.canOpen, !loaded.problem, !starting, !opening else { return }
        opening = true; probing = true; let token = generation.next(); render()
        if configuration.portableRuntime != nil {
            client.browserWorkspace(configuration) { [weak self] target in
                DispatchQueue.main.async {
                    guard let self, self.generation.accepts(token) else { return }
                    self.opening = false; self.probing = false; self.render()
                    if let target { _ = NSWorkspace.shared.open(target) }
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
        let alert = NSAlert()
        alert.messageText = "重启后台？"
        alert.informativeText = "请先保存工作台中的编辑。后台会检查录音和任务，重启期间工作台会短暂断开。"
        alert.addButton(withTitle: "取消"); alert.addButton(withTitle: "重启后台")
        presentAlert(alert) { [weak self] choice in
            guard choice == .alertSecondButtonReturn, let self, !self.starting else { return }
            guard self.workspace?.busy != true else { return }
            self.restarting = true; self.starting = true
            _ = self.generation.next(); self.probing = false; self.render()
            let operation = self.configurationGeneration
            self.starter.restart(self.configuration, pid: pid) { [weak self] result in
                DispatchQueue.main.async {
                    guard let self, self.configurationGeneration == operation else { return }
                    if result?.ready == true, result?.pid != nil {
                        self.client.diagnostics(self.configuration) { [weak self] report in
                            DispatchQueue.main.async {
                                guard let self, self.configurationGeneration == operation else { return }
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
        presentAlert(alert) { [weak self] choice in
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
                    self.presentAlert(alert)
                }
                self.probe()
            }
        }
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
        menuOpen = action("打开工作台", #selector(showWorkspace))
        menuReturn = action("返回本场", #selector(returnToCurrentRecording))
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
        let quit = menu.addItem(withTitle: "退出听记（服务继续运行）", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"); quit.target = NSApp
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
    @objc private func showConfiguration() {
        guard !starting, configuration.portableRuntime == nil else { return }
        if settings?.isPresented == true { return }
        if let workspace {
            workspace.show()
        } else if state.canOpen {
            openVerifiedWorkspace(recording: nil, findCurrent: true)
        }
        let latest = preferences.load(defaults: defaults)
        let initial = HostConfigurationLoad(configuration: latest.saved || latest.problem ? latest.configuration : configuration, token: latest.token, saved: latest.saved, problem: latest.problem)
        let controller = HostSettingsController(defaults: defaults, load: initial, preferences: preferences, requiresWorkspaceConfirmation: { [weak self] value in self?.workspace != nil && value != self?.configuration }, canApply: { [weak self] value in value == self?.configuration || self?.workspace?.busy != true }, onCancel: { [weak self] in self?.settings = nil; self?.render() }) { [weak self] value in
            guard let self else { return }
            if value.configuration != self.configuration { self.workspace?.dispose(); self.workspace = nil; self.companion?.close(); self.companion = nil }
            self.observation = CompanionObservation(); self.observing = false
            self.configurationGeneration += 1; self.loaded = value; self.configuration = value.configuration
            _ = self.generation.next(); self.opening = false; self.probing = false; self.state = .checking
            self.settings = nil; self.render(); self.probe()
        }
        settings = controller
        controller.present(on: workspace?.window)
        render()
    }
    @objc private func about() {
        NSApp.activate(ignoringOtherApps: true)
        var options: [NSApplication.AboutPanelOptionKey: Any] = [
            .applicationName: "听记",
            .applicationVersion: "0.4.0",
            .version: "4",
        ]
        let credits = NSMutableAttributedString(
            string: "本地智能会议工作台\n端侧原生双轨音频采集 · 实时语音识别 · 智能纪要整理与分析\n\n会议数据与音频完全保存在本机，服务与模型独立运行。"
        )
        options[.credits] = credits
        NSApp.orderFrontStandardAboutPanel(options: options)
    }
}