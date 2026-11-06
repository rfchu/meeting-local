import AppKit

final class HostAppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private let configuration: HostConfiguration
    private let client = ServiceClient()
    private let starter = ServiceStarter()
    private var generation = ProbeGeneration()
    private var probing = false
    private var starting = false
    private var startupDeadline: Date?
    private var timer: Timer?
    private var state = ConnectionState.checking
    private var window: NSWindow!
    private let status = NSTextField(labelWithString: "正在核对本机服务")
    private let detail = NSTextField(wrappingLabelWithString: "")
    private let feedback = NSTextField(wrappingLabelWithString: "")
    private let openButton = NSButton(title: "打开会议工作台", target: nil, action: nil)
    private let startButton = NSButton(title: "启动本机服务", target: nil, action: nil)
    private let refreshButton = NSButton(title: "重新检查", target: nil, action: nil)

    init(configuration: HostConfiguration) { self.configuration = configuration; super.init() }
    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu(); buildWindow(); probe()
        timer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in self?.probe() }
        NSApp.activate(ignoringOtherApps: true)
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationWillTerminate(_ notification: Notification) {
        timer?.invalidate(); client.close()
        // Closing this UI never sends a stop, restart or recording command to the service.
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        window.makeKeyAndOrderFront(nil); return true
    }
    private func label(_ text: String, size: CGFloat = 13, color: NSColor = .secondaryLabelColor) -> NSTextField {
        let value = NSTextField(wrappingLabelWithString: text)
        value.font = .systemFont(ofSize: size); value.textColor = color; return value
    }
    private func buildMenu() {
        let menu = NSMenu(); let app = NSMenuItem(); let submenu = NSMenu()
        submenu.addItem(withTitle: "关于听记宿主", action: #selector(about), keyEquivalent: "")
        submenu.addItem(.separator())
        submenu.addItem(withTitle: "退出听记宿主", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
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
        let controls = NSStackView(views: [openButton, startButton, refreshButton]); controls.orientation = .horizontal; controls.spacing = 10
        openButton.target = self; openButton.action = #selector(openWorkspace); openButton.bezelStyle = .rounded; openButton.keyEquivalent = "\r"
        openButton.setAccessibilityIdentifier("host-open-workspace")
        startButton.target = self; startButton.action = #selector(startService); startButton.bezelStyle = .rounded
        startButton.setAccessibilityIdentifier("host-start-service")
        refreshButton.target = self; refreshButton.action = #selector(refresh); refreshButton.bezelStyle = .rounded
        root.addArrangedSubview(controls)
        feedback.font = .systemFont(ofSize: 12); feedback.textColor = .secondaryLabelColor
        feedback.setAccessibilityIdentifier("host-open-feedback"); root.addArrangedSubview(feedback)
        feedback.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true
        root.addArrangedSubview(label("会议编辑、录音和模型配置继续在浏览器工作台完成。", size: 13))
        let note = label("退出此窗口不会停止本机服务。浏览器录音由录音页面持有，原生音轨由 API 服务管理；本轮尚不支持 API 重启后接管采集。", size: 12)
        root.addArrangedSubview(note); note.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true
        let config = NSButton(title: "查看连接配置…", target: self, action: #selector(showConfiguration)); config.bezelStyle = .inline; root.addArrangedSubview(config)
        render(); window.center(); window.makeKeyAndOrderFront(nil)
    }
    private func render() {
        status.stringValue = starting ? "正在请求启动，等待连接确认…" : state.title
        status.textColor = state.canOpen ? .systemGreen : .labelColor
        openButton.isEnabled = state.canOpen && !starting && !probing
        startButton.isEnabled = state.canStart && !starting && !probing
        refreshButton.isEnabled = !probing && !starting
        switch state {
        case let .connected(pid, mode): detail.stringValue = "\(configuration.workspaceURL.absoluteString)\n服务 PID \(pid) · \(mode) · 工作空间匹配"
        case .differentWorkspace: detail.stringValue = "端口上已有其他工作空间的听记服务。请核对连接配置；不会启动第二个实例。"
        case .incompatible: detail.stringValue = "端口响应无法通过宿主协议核对。请更新对应服务或检查端口；不会自动替换。"
        case .unavailable: detail.stringValue = "\(configuration.workspaceURL.absoluteString)\n可重新检查，或明确启动此配置对应的本机服务。"
        case .checking: detail.stringValue = "仅连接 127.0.0.1，正在核对协议、项目和数据目录。"
        }
    }
    private func probe() {
        guard !probing else { return }
        probing = true; let token = generation.next(); render()
        client.probe(configuration) { [weak self] result in
            DispatchQueue.main.async {
                guard let self, self.generation.accepts(token) else { return }
                self.probing = false; self.state = result
                if result.canOpen || result == .differentWorkspace || result == .incompatible || (self.startupDeadline.map { Date() >= $0 } ?? false) {
                    self.starting = false; self.startupDeadline = nil
                }
                self.render()
            }
        }
    }
    @objc private func refresh() { probe() }
    @objc private func openWorkspace() {
        guard state.canOpen, !starting, !probing else { return }
        // Verify again at the click; a stale green indicator cannot open a different workspace.
        probing = true; let token = generation.next(); render()
        client.probe(configuration) { [weak self] result in
            DispatchQueue.main.async {
                guard let self, self.generation.accepts(token) else { return }
                self.probing = false; self.state = result; self.render()
                if result.canOpen {
                    let opened = NSWorkspace.shared.open(self.configuration.workspaceURL)
                    self.feedback.stringValue = opened ? "已请求系统默认浏览器打开工作台。" : "未能打开默认浏览器。请在浏览器访问上方本机地址。"
                } else { self.feedback.stringValue = "连接状态已变化，未打开工作台；请重新检查。" }
            }
        }
    }
    @objc private func startService() {
        guard state.canStart, !starting, !probing else { return }
        starting = true; startupDeadline = Date().addingTimeInterval(50); _ = generation.next(); probing = false; render()
        starter.start(configuration) { [weak self] success in
            DispatchQueue.main.async {
                guard let self else { return }
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
    @objc private func showConfiguration() {
        let alert = NSAlert(); alert.messageText = "本次连接配置"
        alert.informativeText = "工作台：\(configuration.workspaceURL.absoluteString)\n项目：\(configuration.projectRoot)\n数据：\(configuration.dataRoot)\n备份：\(configuration.backupsRoot)\n\n此构建固定上述目录；移动项目或切换工作空间后请重新构建。"
        alert.beginSheetModal(for: window)
    }
    @objc private func about() {
        let alert = NSAlert(); alert.messageText = "听记宿主 · E1"
        alert.informativeText = "本机连接与启动底座。关闭宿主不停止服务；未包含采集所有权迁移、菜单栏常驻、内嵌工作区或自动更新。"
        alert.beginSheetModal(for: window)
    }
}
