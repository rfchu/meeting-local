import AppKit

final class CompanionController: NSObject, NSWindowDelegate {
    private let panel: NSPanel
    private let status = NSTextField(labelWithString: "正在查看录音状态…")
    private let content = NSTextField(wrappingLabelWithString: "")
    private let translation = NSTextField(wrappingLabelWithString: "")
    private let modes = NSSegmentedControl(labels: CompanionMode.allCases.map(\.title), trackingMode: .selectOne, target: nil, action: nil)
    private let client: ServiceClient
    private let returnButton = NSButton(title: "返回本场", target: nil, action: nil)
    private var observation = CompanionObservation()
    var returnToRecording: (String) -> Void = { _ in }
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
        content.font = .systemFont(ofSize: 13)
        content.maximumNumberOfLines = 6
        translation.font = .systemFont(ofSize: 12)
        translation.textColor = .secondaryLabelColor
        translation.maximumNumberOfLines = 5
        returnButton.target = self; returnButton.action = #selector(openRecording)
        returnButton.bezelStyle = .rounded; returnButton.isHidden = true
        let stack = NSStackView(views: [modes, status, content, translation, returnButton])
        stack.orientation = .vertical; stack.alignment = .leading; stack.spacing = 8
        stack.translatesAutoresizingMaskIntoConstraints = false
        let view = NSView(); panel.contentView = view; view.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.topAnchor.constraint(equalTo: view.topAnchor, constant: 12),
            stack.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 14),
            stack.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -14),
            stack.bottomAnchor.constraint(equalTo: view.bottomAnchor, constant: -12),
            content.widthAnchor.constraint(equalTo: stack.widthAnchor),
            translation.widthAnchor.constraint(equalTo: stack.widthAnchor)
        ])
    }

    func show() {
        guard !panel.isVisible else { panel.orderFrontRegardless(); return }
        recording = nil; observation = CompanionObservation(); returnButton.isHidden = true; modes.selectedSegment = 0
        status.stringValue = "正在查看录音状态…"
        clearContent()
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
        clearContent(); resize(); poll()
    }

    private func poll() {
        guard panel.isVisible else { return }
        if observation.latest != nil && !observation.isCurrent(at: Date()) { showUnavailable() }
        guard !pending else { return }
        let token = generation.next(); pending = true
        client.companion(configuration, mode: mode, recording: recording) { [weak self] snapshot in
            DispatchQueue.main.async {
                guard let self, self.generation.accepts(token), self.panel.isVisible else { return }
                self.pending = false; self.observation.receive(snapshot)
                if self.observation.isCurrent(at: Date()), let snapshot { self.render(snapshot) }
                else { self.showUnavailable() }
            }
        }
    }

    @objc private func openRecording() {
        guard let rid = observation.target(at: Date()) else { showUnavailable(); poll(); return }
        returnToRecording(rid)
    }

    private func showUnavailable() {
        status.stringValue = "暂时无法确认录音状态"
        if let last = observation.latest {
            let time = Date(timeIntervalSince1970: last.observed_at).formatted(date: .omitted, time: .standard)
        status.stringValue += " · 最近核对 " + time
        }
        clearContent(); returnButton.isHidden = true
        resize()
    }

    private func render(_ snapshot: CompanionSnapshot) {
        let labels = ["idle":"当前没有录音", "multiple":"有多个录音，请在工作台查看", "recording":"正在录音",
                      "missing":"找不到原录音会话", "paused":"录音已暂停", "starting":"正在准备录音", "unknown":"录音状态待确认",
                      "interrupted":"录音已中断", "saved":"录音已保存", "saving":"正在保存录音"]
        recording = recording ?? snapshot.recording_id
        let issues = [snapshot.storage == "failed" ? "保存失败" : nil,
                          snapshot.asr == "failed" ? "识别失败" : nil].compactMap { $0 }
        status.stringValue = ([labels[snapshot.phase] ?? "录音状态待确认"] + issues).joined(separator: " · ")
        clearContent()
        if snapshot.recording_id != nil {
            switch mode {
            case .status:
                break
            case .bilingual:
                content.stringValue = snapshot.lines.first ?? "等待已确认文字"
                translation.stringValue = snapshot.lines.dropFirst().first ?? ""
            case .highlights:
                content.stringValue = snapshot.lines.isEmpty ? "暂无会中重点" :
                    snapshot.lines.prefix(3).map { "• " + $0 }.joined(separator: "\n")
            }
        }
        returnButton.isHidden = snapshot.recording_id == nil
        resize()
    }

    private func clearContent() {
        content.stringValue = ""; content.isHidden = true
        translation.stringValue = ""; translation.isHidden = true
    }

    private func resize() {
        content.isHidden = content.stringValue.isEmpty
        translation.isHidden = translation.stringValue.isEmpty
        let width: CGFloat = 370
        let textHeight = [content, translation].reduce(CGFloat.zero) { total, field in
            guard !field.isHidden else { return total }
            let size = field.cell?.cellSize(forBounds: NSRect(x: 0, y: 0, width: width - 28, height: 1000)) ?? .zero
            let lineHeight = (field.font?.pointSize ?? 13) + 4
            return total + min(size.height, lineHeight * CGFloat(field.maximumNumberOfLines)) + 8
        }
        let frame = panel.frame
        panel.setContentSize(NSSize(width: width, height: 78 + textHeight + (returnButton.isHidden ? 0 : 30)))
        let visible = panel.screen?.visibleFrame
        let top = visible.map { max(frame.maxY, $0.minY + panel.frame.height) } ?? frame.maxY
        panel.setFrameTopLeftPoint(NSPoint(x: frame.minX, y: top))
    }
}
