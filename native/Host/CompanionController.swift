import AppKit

final class CompanionController: NSObject, NSWindowDelegate {
    private let panel: NSPanel
    private let status = NSTextField(labelWithString: "正在查看录音状态…")
    private let content = NSTextField(wrappingLabelWithString: "")
    private let modes = NSSegmentedControl(labels: CompanionMode.allCases.map(\.title), trackingMode: .selectOne, target: nil, action: nil)
    private let client: ServiceClient
    private let configuration: HostConfiguration
    private var timer: Timer?
    private var generation = ProbeGeneration()
    private var pending = false
    private var recording: String?
    private var mode: CompanionMode { CompanionMode.allCases[max(0, modes.selectedSegment)] }

    init(configuration: HostConfiguration, client: ServiceClient) {
        self.configuration = configuration; self.client = client
        panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 370, height: 100),
                        styleMask: [.titled, .closable, .utilityWindow, .nonactivatingPanel], backing: .buffered, defer: false)
        super.init()
        panel.title = "听记 · 伴随窗口"
        panel.isReleasedWhenClosed = false; panel.delegate = self
        panel.level = .floating; panel.hidesOnDeactivate = false; panel.becomesKeyOnlyIfNeeded = true
        panel.sharingType = .none
        panel.collectionBehavior = [.moveToActiveSpace, .fullScreenAuxiliary]
        modes.target = self; modes.action = #selector(changeMode)
        status.font = .systemFont(ofSize: 13, weight: .semibold)
        content.textColor = .secondaryLabelColor; content.maximumNumberOfLines = 8
        let stack = NSStackView(views: [modes, status, content])
        stack.orientation = .vertical; stack.alignment = .leading; stack.spacing = 8
        stack.translatesAutoresizingMaskIntoConstraints = false
        let view = NSView(); panel.contentView = view; view.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.topAnchor.constraint(equalTo: view.topAnchor, constant: 12),
            stack.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 14),
            stack.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -14),
            stack.bottomAnchor.constraint(equalTo: view.bottomAnchor, constant: -12),
            content.widthAnchor.constraint(equalTo: stack.widthAnchor)
        ])
    }

    func show() {
        guard !panel.isVisible else { panel.orderFrontRegardless(); return }
        recording = nil; modes.selectedSegment = 0
        status.stringValue = "正在查看录音状态…"; content.stringValue = ""; content.isHidden = true
        panel.setContentSize(NSSize(width: 370, height: 78))
        if let screen = NSScreen.screens.first(where: { $0.frame.contains(NSEvent.mouseLocation) }) ?? NSScreen.main {
            let frame = screen.visibleFrame
            panel.setFrameTopLeftPoint(NSPoint(x: frame.maxX - 386, y: frame.maxY - 16))
        }
        panel.orderFrontRegardless()
        timer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in self?.poll() }
        poll()
    }

    func close() { panel.close() }
    func windowWillClose(_ notification: Notification) {
        timer?.invalidate(); timer = nil; _ = generation.next(); pending = false
    }

    @objc private func changeMode() {
        _ = generation.next(); pending = false
        content.stringValue = ""; resize(); poll()
    }

    private func poll() {
        guard panel.isVisible, !pending else { return }
        let token = generation.next(); pending = true
        client.companion(configuration, mode: mode, recording: recording) { [weak self] snapshot in
            DispatchQueue.main.async {
                guard let self, self.generation.accepts(token), self.panel.isVisible else { return }
                self.pending = false; self.render(snapshot)
            }
        }
    }

    private func render(_ snapshot: CompanionSnapshot?) {
        let labels = ["idle":"当前没有录音", "multiple":"有多个录音，请在工作台查看", "recording":"正在录音",
                      "paused":"录音已暂停", "starting":"正在准备录音", "unknown":"录音状态待确认",
                      "interrupted":"录音已中断", "saved":"录音已保存", "saving":"正在保存录音"]
        if let snapshot {
            recording = recording ?? snapshot.recording_id
            let issues = [snapshot.storage == "failed" ? "保存失败" : nil,
                          snapshot.asr == "failed" ? "识别失败" : nil].compactMap { $0 }
            status.stringValue = ([labels[snapshot.phase] ?? "录音状态待确认"] + issues).joined(separator: " · ")
            let empty = mode == .bilingual ? "等待已确认文字" : "暂无可显示的会中重点"
            content.stringValue = mode == .status || snapshot.recording_id == nil ? "" :
                snapshot.lines.isEmpty ? empty : snapshot.lines.joined(separator: "\n\n")
        } else {
            status.stringValue = "暂时无法读取录音状态"; content.stringValue = ""
        }
        resize()
    }

    private func resize() {
        content.isHidden = content.stringValue.isEmpty
        let frame = panel.frame
        panel.setContentSize(NSSize(width: 370, height: content.isHidden ? 78 : 218))
        panel.setFrameTopLeftPoint(NSPoint(x: frame.minX, y: frame.maxY))
    }
}
