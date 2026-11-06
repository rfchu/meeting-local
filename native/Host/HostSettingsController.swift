import AppKit

final class HostSettingsController: NSObject, NSTextFieldDelegate {
    private let defaults: HostConfiguration
    private let load: HostConfigurationLoad
    private let preferences: HostPreferences
    private let onSave: (HostConfigurationLoad) -> Void
    private let onCancel: () -> Void
    private let requiresWorkspaceConfirmation: (HostConfiguration) -> Bool
    private let canApply: (HostConfiguration) -> Bool
    private let confirmation = NSButton(checkboxWithTitle: "已保存内嵌工作区修改并完成相关会中操作，确认切换连接", target: nil, action: nil)
    private let client = ServiceClient()
    private var generation = ProbeGeneration()
    private var fields: [NSTextField] = []
    private let message = NSTextField(wrappingLabelWithString: "")
    private var panel: NSPanel!
    private weak var parent: NSWindow?
    init(defaults: HostConfiguration, load: HostConfigurationLoad, preferences: HostPreferences, requiresWorkspaceConfirmation: @escaping (HostConfiguration) -> Bool, canApply: @escaping (HostConfiguration) -> Bool, onCancel: @escaping () -> Void, onSave: @escaping (HostConfigurationLoad) -> Void) {
        self.defaults = defaults; self.load = load; self.preferences = preferences; self.onSave = onSave; self.onCancel = onCancel; self.requiresWorkspaceConfirmation = requiresWorkspaceConfirmation; self.canApply = canApply
    }
    var isPresented: Bool { panel?.isVisible ?? false }
    var hasUnsavedChanges: Bool { (try? draft()) != load.configuration }
    func present(on parent: NSWindow) {
        self.parent = parent
        panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 710, height: 480), styleMask: [.titled], backing: .buffered, defer: false)
        panel.title = "连接设置"
        let root = NSStackView(); root.orientation = .vertical; root.alignment = .leading; root.spacing = 12; root.translatesAutoresizingMaskIntoConstraints = false
        let content = panel.contentView!; content.addSubview(root)
        NSLayoutConstraint.activate([root.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 26), root.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -26), root.topAnchor.constraint(equalTo: content.topAnchor, constant: 22)])
        let title = NSTextField(labelWithString: "连接本机工作空间"); title.font = .systemFont(ofSize: 18, weight: .semibold); root.addArrangedSubview(title)
        let hint = NSTextField(wrappingLabelWithString: "保存只切换宿主连接，不启动、停止服务，也不移动会议文件。模型配置仍在工作台管理。")
        root.addArrangedSubview(hint); hint.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true
        for (index, name) in ["项目目录", "数据目录", "启动时备份目录", "本机端口"].enumerated() {
            let row = NSStackView(); row.orientation = .horizontal; row.spacing = 12
            let label = NSTextField(labelWithString: name); label.widthAnchor.constraint(equalToConstant: 112).isActive = true
            let field = NSTextField(); field.delegate = self; field.setAccessibilityLabel(name); field.setAccessibilityIdentifier("host-setting-\(index)")
            fields.append(field); row.addArrangedSubview(label); row.addArrangedSubview(field); root.addArrangedSubview(row)
            row.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true
        }
        fill(load.configuration)
        message.font = .systemFont(ofSize: 12); message.textColor = .secondaryLabelColor
        message.stringValue = load.problem ? "已保存配置无法使用，当前连接已暂停。可填入默认值后明确保存；可读取的损坏内容会保留副本。" : "只连接 127.0.0.1。保存后会重新核对项目和数据目录。"
        root.addArrangedSubview(message); message.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true
        root.addArrangedSubview(confirmation); confirmation.isHidden = true
        let row = NSStackView(); row.orientation = .horizontal; row.spacing = 10
        for (name, action) in [("填入构建默认", #selector(restoreDefaults)), ("测试连接", #selector(testConnection)), ("取消", #selector(cancel)), ("保存并切换", #selector(save))] {
            let button = NSButton(title: name, target: self, action: action); button.bezelStyle = .rounded; row.addArrangedSubview(button)
        }
        root.addArrangedSubview(row); parent.beginSheet(panel)
    }
    private func fill(_ value: HostConfiguration) {
        for (field, text) in zip(fields, [value.projectRoot, value.dataRoot, value.backupsRoot, String(value.port)]) { field.stringValue = text }
    }
    private func draft() throws -> HostConfiguration {
        guard let port = Int(fields[3].stringValue.trimmingCharacters(in: .whitespaces)) else { throw HostFailure.invalidConfiguration }
        return try HostConfiguration(projectRoot: fields[0].stringValue, dataRoot: fields[1].stringValue, backupsRoot: fields[2].stringValue, port: port)
    }
    func controlTextDidChange(_ obj: Notification) { confirmation.state = .off; confirmation.isHidden = !((try? draft()).map(requiresWorkspaceConfirmation) ?? false); _ = generation.next(); message.stringValue = "配置尚未保存，可测试连接或保存后切换。" }
    @objc private func restoreDefaults() { _ = generation.next(); fill(defaults); confirmation.state = .off; confirmation.isHidden = !requiresWorkspaceConfirmation(defaults); message.stringValue = "已填入本次构建默认值，点击保存后生效。" }
    @objc private func testConnection() {
        do {
            let value = try draft(); let token = generation.next(); message.stringValue = "正在测试此配置…"
            client.probe(value) { [weak self] result in
                DispatchQueue.main.async {
                    guard let self, self.generation.accepts(token), (try? self.draft()) == value else { return }
                    self.message.stringValue = result.title + "。测试不会保存配置或启停服务。"
                }
            }
        } catch { message.stringValue = "请填写绝对目录与 1024～65535 的整数端口。" }
    }
    @objc private func save() {
        do {
            let value = try draft()
            guard canApply(value) else { message.stringValue = "请先完成或取消内嵌工作区的文件选择与下载。"; return }
            guard !requiresWorkspaceConfirmation(value) || confirmation.state == .on else { confirmation.isHidden = false; message.stringValue = "切换前请保存内嵌页面并完成会中操作，再勾选确认。"; return }
            let result = try preferences.save(value, expectedToken: load.token, preservePrevious: load.problem)
            _ = generation.next(); client.close(); parent?.endSheet(panel); panel.close(); onSave(result)
        } catch { message.stringValue = (error as? HostPreferenceFailure)?.message ?? "配置无效；请检查绝对目录与端口范围。" }
    }
    @objc private func cancel() { _ = generation.next(); client.close(); parent?.endSheet(panel); panel.close(); onCancel() }
    func close() { _ = generation.next(); client.close(); if let panel, panel.isVisible { parent?.endSheet(panel); panel.close() } }
}
