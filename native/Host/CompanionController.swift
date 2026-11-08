import AppKit

final class ShorthandTextField: NSTextField, NSTextFieldDelegate {
    var onSubmit: ((String, Bool) -> Void)?
    var submitting = false
    var onEscape: (() -> Void)?

    private func submit(_ text: String, synthesize: Bool) -> Bool {
        if submitting { return true }
        let clean = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty else { return false }
        onSubmit?(clean, synthesize)
        return true
    }

    override func performKeyEquivalent(with event: NSEvent) -> Bool {
        let editor = currentEditor() as? NSTextView
        if editor?.hasMarkedText() == true { return super.performKeyEquivalent(with: event) }
        if (event.keyCode == 36 || event.keyCode == 76) && event.modifierFlags.contains(.command) {
            return submit(editor?.string ?? stringValue, synthesize: true)
        }
        return super.performKeyEquivalent(with: event)
    }

    func control(_ control: NSControl, textView: NSTextView, doCommandBy command: Selector) -> Bool {
        guard !textView.hasMarkedText() else { return false }
        if command == #selector(NSResponder.insertNewline(_:)) {
            return submit(textView.string, synthesize: NSApp.currentEvent?.modifierFlags.contains(.command) == true)
        }
        if command == #selector(NSResponder.cancelOperation(_:)) {
            onEscape?()
            return true
        }
        return false
    }
}

private final class CompanionMeter: NSView {
    var samples: [Double] = [] { didSet { needsDisplay = true } }
    var color: NSColor = .systemBlue
    override var intrinsicContentSize: NSSize { NSSize(width: 88, height: 16) }

    override func draw(_ dirtyRect: NSRect) {
        color.setFill()
        let step = bounds.width / 24
        for (index, sample) in samples.suffix(24).enumerated() {
            let height = max(2, min(bounds.height, sqrt(sample) * bounds.height * 3))
            NSBezierPath(roundedRect: NSRect(x: CGFloat(index) * step, y: (bounds.height - height) / 2,
                                            width: max(1, step - 1), height: height),
                         xRadius: 1, yRadius: 1).fill()
        }
    }
}

final class CompanionController: NSObject, NSWindowDelegate {
    private let panel: NSPanel
    private let status = NSImageView()
    private let content = NSTextField(wrappingLabelWithString: "")
    private let translation = NSTextField(wrappingLabelWithString: "")
    private let elapsed = NSTextField(labelWithString: "")
    private let systemMeter = CompanionMeter()
    private let microphoneMeter = CompanionMeter()
    private let inputField = ShorthandTextField()
    private var pendingRequest: (recording: String, text: String, synthesize: Bool, id: String)?
    private let inputFeedback = NSTextField(labelWithString: "")
    private let modes = NSSegmentedControl(labels: CompanionMode.allCases.map(\.title), trackingMode: .selectOne, target: nil, action: nil)
    private let client: ServiceClient
    private let returnButton = NSButton()
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
        panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 380, height: 140),
                        styleMask: [.titled, .closable, .utilityWindow, .nonactivatingPanel], backing: .buffered, defer: false)
        super.init()
        panel.title = "听记 · 伴随轻岛 (⌃⌥N)"
        panel.isReleasedWhenClosed = false; panel.delegate = self
        panel.level = .floating; panel.hidesOnDeactivate = false; panel.becomesKeyOnlyIfNeeded = true
        panel.sharingType = .none
        panel.collectionBehavior = [.canJoinAllSpaces, .canJoinAllApplications, .fullScreenAuxiliary, .ignoresCycle]
        modes.target = self; modes.action = #selector(changeMode)
        setStatus("正在查看录音状态…", symbol: "ellipsis.circle")
        content.font = .systemFont(ofSize: 13)
        content.maximumNumberOfLines = 6
        translation.font = .systemFont(ofSize: 12)
        translation.textColor = .secondaryLabelColor
        translation.maximumNumberOfLines = 5

        inputField.placeholderString = "速记 (Enter 纯文本, ⌘+Enter 智能扩充)..."
        inputField.font = .systemFont(ofSize: 12)
        inputField.delegate = inputField
        inputFeedback.font = .systemFont(ofSize: 11)
        inputFeedback.textColor = .secondaryLabelColor

        inputField.onSubmit = { [weak self] text, synthesize in
            guard let self else { return }
            guard let rid = self.observation.active(at: Date())?.recording_id else {
                self.inputFeedback.stringValue = "请先开启或连接录音"
                return
            }
            if self.pendingRequest?.recording != rid || self.pendingRequest?.text != text || self.pendingRequest?.synthesize != synthesize {
                self.pendingRequest = (rid, text, synthesize, UUID().uuidString)
            }
            guard let request = self.pendingRequest else { return }
            self.inputField.submitting = true
            self.inputFeedback.stringValue = "正在保存速记…"
            self.client.postShorthand(self.configuration, recording: rid, text: text, synthesize: synthesize, clientID: request.id) { [weak self] ok in
                DispatchQueue.main.async {
                    guard let self else { return }
                    self.inputField.submitting = false
                    if ok {
                        if (self.inputField.currentEditor()?.string ?? self.inputField.stringValue).trimmingCharacters(in: .whitespacesAndNewlines) == text {
                            self.inputField.currentEditor()?.string = ""
                            self.inputField.stringValue = ""
                        }
                        self.pendingRequest = nil
                    }
                    self.inputFeedback.stringValue = ok ? (synthesize ? "已提交扩充" : "已记录") : "提交失败，原文保留，可按回车重试"

                }
            }
        }
        inputField.onEscape = { [weak self] in
            self?.close()
        }

        returnButton.target = self; returnButton.action = #selector(openRecording)
        returnButton.image = NSImage(systemSymbolName: "arrow.up.right.square", accessibilityDescription: "返回本场")
        returnButton.setAccessibilityLabel("返回本场")
        returnButton.toolTip = "返回本场"
        returnButton.isBordered = false
        returnButton.isHidden = true
        let spacer = NSView()
        let header = NSStackView(views: [modes, spacer, status, returnButton])
        header.spacing = 8
        header.alignment = .centerY
        status.widthAnchor.constraint(equalToConstant: 18).isActive = true
        status.heightAnchor.constraint(equalToConstant: 18).isActive = true
        returnButton.widthAnchor.constraint(equalToConstant: 28).isActive = true
        returnButton.heightAnchor.constraint(equalToConstant: 28).isActive = true
        elapsed.font = .monospacedDigitSystemFont(ofSize: 11, weight: .regular)
        systemMeter.setAccessibilityElement(true)
        systemMeter.setAccessibilityLabel("电脑声音")
        microphoneMeter.setAccessibilityElement(true)
        microphoneMeter.setAccessibilityLabel("麦克风")
        microphoneMeter.color = .systemGreen
        let signals = NSStackView(views: [elapsed, systemMeter, microphoneMeter])
        signals.spacing = 8
        let stack = NSStackView(views: [header, signals, content, translation, inputField, inputFeedback])
        stack.orientation = .vertical; stack.alignment = .leading; stack.spacing = 6
        stack.translatesAutoresizingMaskIntoConstraints = false
        let view = NSView(); panel.contentView = view; view.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.topAnchor.constraint(equalTo: view.topAnchor, constant: 10),
            stack.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 12),
            stack.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -12),
            stack.bottomAnchor.constraint(equalTo: view.bottomAnchor, constant: -10),
            header.widthAnchor.constraint(equalTo: stack.widthAnchor),
            content.widthAnchor.constraint(equalTo: stack.widthAnchor),
            translation.widthAnchor.constraint(equalTo: stack.widthAnchor),
            inputField.widthAnchor.constraint(equalTo: stack.widthAnchor),
            inputFeedback.widthAnchor.constraint(equalTo: stack.widthAnchor)
        ])
    }

    func toggle() {
        if panel.isVisible {
            close()
        } else {
            show()
        }
    }

    func show() {
        guard !panel.isVisible else { panel.orderFrontRegardless(); return }
        recording = nil; observation = CompanionObservation(); returnButton.isHidden = true; modes.selectedSegment = 0
        setStatus("正在查看录音状态…", symbol: "ellipsis.circle")
        clearContent(); updateSignals(nil)
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
        var message = "暂时无法确认录音状态"
        if let last = observation.latest {
            let time = Date(timeIntervalSince1970: last.observed_at).formatted(date: .omitted, time: .standard)
            message += " · 最近核对 " + time
        }
        setStatus(message, symbol: "exclamationmark.circle", color: .systemOrange)
        updateSignals(nil)
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
        let symbols = ["recording": "record.circle.fill", "paused": "pause.circle", "saved": "checkmark.circle",
                       "idle": "circle", "starting": "ellipsis.circle", "saving": "ellipsis.circle"]
        let message = ([labels[snapshot.phase] ?? "录音状态待确认"] + issues).joined(separator: " · ")
        let symbol = issues.isEmpty ? symbols[snapshot.phase] ?? "exclamationmark.circle" : "exclamationmark.circle"
        let color: NSColor = symbol == "record.circle.fill" ? .systemRed :
            symbol == "exclamationmark.circle" ? .systemOrange : .secondaryLabelColor
        setStatus(message, symbol: symbol, color: color)
        updateSignals(snapshot)
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

    private func updateSignals(_ snapshot: CompanionSnapshot?) {
        let active = snapshot.map { ["recording", "paused"].contains($0.phase) } ?? false
        let seconds = snapshot?.duration_seconds ?? 0
        elapsed.isHidden = !active
        if active, seconds.isFinite, seconds >= 0, seconds < 1000000 {
            let total = Int(seconds)
            elapsed.stringValue = String(format: "%02d:%02d", total / 60, total % 60)
        } else { elapsed.stringValue = "" }
        systemMeter.samples = snapshot?.system_levels ?? []
        microphoneMeter.samples = snapshot?.microphone_levels ?? []
        systemMeter.isHidden = systemMeter.samples.isEmpty
        microphoneMeter.isHidden = microphoneMeter.samples.isEmpty
    }

    private func setStatus(_ message: String, symbol: String, color: NSColor = .secondaryLabelColor) {
        status.image = NSImage(systemSymbolName: symbol, accessibilityDescription: message)
        status.contentTintColor = color
        status.setAccessibilityLabel(message)
        status.toolTip = message
    }

    private func clearContent() {
        content.stringValue = ""; content.isHidden = true
        translation.stringValue = ""; translation.isHidden = true
    }

    private func resize() {
        content.isHidden = content.stringValue.isEmpty
        translation.isHidden = translation.stringValue.isEmpty
        let width: CGFloat = 380
        let textHeight = [content, translation].reduce(CGFloat.zero) { total, field in
            guard !field.isHidden else { return total }
            let size = field.cell?.cellSize(forBounds: NSRect(x: 0, y: 0, width: width - 28, height: 1000)) ?? .zero
            let lineHeight = (field.font?.pointSize ?? 13) + 4
            return total + min(size.height, lineHeight * CGFloat(field.maximumNumberOfLines)) + 8
        }
        let frame = panel.frame
        let signalExtra: CGFloat = elapsed.isHidden ? 0 : 22
        let inputExtra: CGFloat = inputFeedback.stringValue.isEmpty ? 48 : 66
        panel.setContentSize(NSSize(width: width, height: 48 + textHeight + inputExtra + signalExtra))
        let visible = panel.screen?.visibleFrame
        let top = visible.map { max(frame.maxY, $0.minY + panel.frame.height) } ?? frame.maxY
        panel.setFrameTopLeftPoint(NSPoint(x: frame.minX, y: top))
    }
}
