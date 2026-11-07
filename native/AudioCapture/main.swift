import Foundation
import AppKit
import AVFoundation
import ScreenCaptureKit
import CoreMedia
import CoreGraphics
import CoreAudio

func hostSeconds() -> Double { CMTimeGetSeconds(CMClockGetTime(CMClockGetHostTimeClock())) }
func jsonLine(_ value: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: value) {
        FileHandle.standardOutput.write(data); FileHandle.standardOutput.write(Data([10]))
    }
}

final class Capture: NSObject, SCStreamOutput, SCStreamDelegate {
    let folder: URL
    let microphone: Bool
    let incarnation: String
    let queue = DispatchQueue(label: "cn.seeknow.tingji.capture.audio")
    var stream: SCStream?
    var files: [String: FileHandle] = [:]
    var converters: [String: AVAudioConverter] = [:]
    var formats: [String: AVAudioFormat] = [:]
    var frames: [String: Int64] = [:]
    var levels: [String: [Double]] = [:]
    var lastLevelAt: [String: Double] = [:]
    var lastSignalAt: [String: Double] = [:]
    var signalGraceAt = hostSeconds()
    var timer: DispatchSourceTimer?
    let diagnostics = CaptureDiagnostics()
    var selectedMicrophoneID: String?
    var defaultInput: AudioDeviceID?
    var defaultOutput: AudioDeviceID?
    var deviceListeners: [(AudioObjectPropertyAddress, AudioObjectPropertyListenerBlock)] = []
    var deviceNotifications: [NSObjectProtocol] = []
    var state = "starting"
    var errorMessage = ""
    var origin = hostSeconds()
    var pausedAt: Double?
    var pausedTotal: Double = 0
    var elapsed: Double = 0
    let outputFormat = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16000, channels: 1, interleaved: true)!

    init(folder: URL, microphone: Bool, incarnation: String) { self.folder = folder; self.microphone = microphone; self.incarnation = incarnation }

    func defaultDevice(_ selector: AudioObjectPropertySelector) -> AudioDeviceID? {
        var address = AudioObjectPropertyAddress(mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        var device = AudioDeviceID(0); var size = UInt32(MemoryLayout<AudioDeviceID>.size)
        guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &device) == noErr else { return nil }
        return device
    }
    func readDeviceDefaults(countChanges: Bool) {
        let input = defaultDevice(kAudioHardwarePropertyDefaultInputDevice)
        let output = defaultDevice(kAudioHardwarePropertyDefaultOutputDevice)
        if countChanges {
            if let input, let previous = defaultInput, input != previous { diagnostics.defaultInputChanges += 1 }
            if let output, let previous = defaultOutput, output != previous { diagnostics.defaultOutputChanges += 1 }
        }
        if let input { defaultInput = input } else { diagnostics.deviceQueryFailures += 1 }
        if let output { defaultOutput = output } else { diagnostics.deviceQueryFailures += 1 }
        if let selectedMicrophoneID {
            if let currentID = AVCaptureDevice.default(for: .audio)?.uniqueID { diagnostics.microphoneMatchesDefault = currentID == selectedMicrophoneID }
            else { diagnostics.microphoneMatchesDefault = nil }
        }
    }
    func watchDevices() {
        readDeviceDefaults(countChanges: false)
        for selector in [kAudioHardwarePropertyDefaultInputDevice, kAudioHardwarePropertyDefaultOutputDevice] {
            var address = AudioObjectPropertyAddress(mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
            let listener: AudioObjectPropertyListenerBlock = { [weak self] _, _ in
                guard let self, self.state != "stopped", self.state != "failed" else { return }
                self.readDeviceDefaults(countChanges: true); self.report()
            }
            if AudioObjectAddPropertyListenerBlock(AudioObjectID(kAudioObjectSystemObject), &address, queue, listener) == noErr {
                deviceListeners.append((address, listener))
            } else { diagnostics.listenerFailures += 1 }
        }
        for notification in [AVCaptureDevice.wasConnectedNotification, AVCaptureDevice.wasDisconnectedNotification] {
            let token = NotificationCenter.default.addObserver(forName: notification, object: nil, queue: nil) { [weak self] event in
                guard let device = event.object as? AVCaptureDevice, device.hasMediaType(.audio) else { return }
                self?.queue.async { [weak self] in
                    guard let self, self.state != "stopped", self.state != "failed" else { return }
                    if notification == AVCaptureDevice.wasConnectedNotification { self.diagnostics.connections += 1 }
                    else { self.diagnostics.disconnections += 1 }
                    self.readDeviceDefaults(countChanges: true); self.report()
                }
            }
            deviceNotifications.append(token)
        }
    }
    func stopWatchingDevices() {
        for (var address, listener) in deviceListeners {
            AudioObjectRemovePropertyListenerBlock(AudioObjectID(kAudioObjectSystemObject), &address, queue, listener)
        }
        deviceListeners.removeAll()
        for token in deviceNotifications { NotificationCenter.default.removeObserver(token) }
        deviceNotifications.removeAll()
    }

    func report() {
        if state == "recording" { elapsed = max(0, hostSeconds() - origin - pausedTotal) }
        let report: [String: Any] = ["state": state, "duration": elapsed, "error": errorMessage, "ownership_protocol_version":1,"capture_incarnation":incarnation,
            "system_frames": frames["system"] ?? 0, "microphone_frames": frames["microphone"] ?? 0,
            "system_levels": levels["system"] ?? [], "microphone_levels": levels["microphone"] ?? [],
            "diagnostics": diagnostics.payload(), "sample_rate": 16000, "updated": Date().timeIntervalSince1970,
            "buffer_age": Dictionary(uniqueKeysWithValues: files.keys.map { ($0, max(0, hostSeconds() - (lastLevelAt[$0] ?? signalGraceAt))) }),
            "silence_seconds": Dictionary(uniqueKeysWithValues: files.keys.map { ($0, max(0, hostSeconds() - (lastSignalAt[$0] ?? signalGraceAt))) })]
        do {
            let data = try JSONSerialization.data(withJSONObject: report)
            try data.write(to: folder.appendingPathComponent("capture.json"), options: .atomic)
        } catch { jsonLine(["error": "无法保存采集状态：\(error.localizedDescription)"]) }
    }

    func start() async throws {
        queue.sync { report() }
        guard CGPreflightScreenCaptureAccess() || CGRequestScreenCaptureAccess() else {
            throw NSError(domain: "TingjiCapture", code: 1, userInfo: [NSLocalizedDescriptionKey: "请在系统设置 → 隐私与安全性 → 屏幕与系统音频录制中允许听记采集器，然后重新开始"])
        }
        if microphone {
            let allowed = await AVCaptureDevice.requestAccess(for: .audio)
            guard allowed else { throw NSError(domain: "TingjiCapture", code: 2, userInfo: [NSLocalizedDescriptionKey: "麦克风未授权，请在系统设置中允许听记采集器访问麦克风"] ) }
        }
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        guard let display = content.displays.first else {
            throw NSError(domain: "TingjiCapture", code: 3, userInfo: [NSLocalizedDescriptionKey: "没有可用显示器"])
        }
        let filter = SCContentFilter(display: display, excludingWindows: [])
        let configuration = SCStreamConfiguration()
        configuration.width = 2; configuration.height = 2
        configuration.minimumFrameInterval = CMTime(value: 1, timescale: 1)
        configuration.queueDepth = 3; configuration.showsCursor = false
        configuration.capturesAudio = true; configuration.excludesCurrentProcessAudio = true
        configuration.sampleRate = 48000; configuration.channelCount = 2
        if #available(macOS 15.0, *) {
            configuration.captureMicrophone = microphone
            selectedMicrophoneID = microphone ? AVCaptureDevice.default(for: .audio)?.uniqueID : nil
            configuration.microphoneCaptureDeviceID = selectedMicrophoneID
        }
        let capture = SCStream(filter: filter, configuration: configuration, delegate: self)
        // Receive minimal video buffers for stream compatibility; discard them immediately.
        try capture.addStreamOutput(self, type: .screen, sampleHandlerQueue: queue)
        try capture.addStreamOutput(self, type: .audio, sampleHandlerQueue: queue)
        if #available(macOS 15.0, *), microphone {
            try capture.addStreamOutput(self, type: .microphone, sampleHandlerQueue: queue)
        }
        for name in microphone ? ["system", "microphone"] : ["system"] {
            let url = folder.appendingPathComponent(name + ".pcm")
            FileManager.default.createFile(atPath: url.path, contents: nil)
            files[name] = try FileHandle(forWritingTo: url)
        }
        stream = capture; origin = hostSeconds(); signalGraceAt = origin
        queue.sync { watchDevices(); state = "recording"; report() }
        try await capture.startCapture()
        queue.sync {
            let timer = DispatchSource.makeTimerSource(queue: queue)
            timer.schedule(deadline: .now(), repeating: 1)
            timer.setEventHandler { [weak self] in
                guard let self else { return }
                do { for file in self.files.values { try file.synchronize() }; self.report() }
                catch { self.diagnostics.failureStage = "storage"; self.fail("磁盘保存失败：\(error.localizedDescription)") }
            }
            self.timer = timer; timer.resume()
        }
    }

    func fail(_ message: String) {
        guard state != "failed" && state != "stopped" else { return }
        errorMessage = message; state = "failed"; report()
        Task { await finish(state: "failed"); exit(1) }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        queue.async { guard self.state != "failed", self.state != "stopped" else { return }; self.diagnostics.failureStage = "capture_stream"; self.diagnostics.streamErrorCode = (error as NSError).code; self.fail("采集中断：\(error.localizedDescription)") }
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sample: CMSampleBuffer, of type: SCStreamOutputType) {
        guard state == "recording" else { return }
        let name: String
        if type == .audio { name = "system" }
        else if #available(macOS 15.0, *), type == .microphone { name = "microphone" }
        else { return }
        diagnostics.received(name)
        guard sample.isValid, let description = sample.formatDescription else { diagnostics.dropped(name); return }
        let format = AVAudioFormat(cmAudioFormatDescription: description)
        guard let source = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(sample.numSamples)) else { diagnostics.dropped(name); return }
        source.frameLength = AVAudioFrameCount(sample.numSamples)
        do {
            try sample.copyPCMData(fromRange: 0..<sample.numSamples, into: source.mutableAudioBufferList)
            if formats[name] != format {
                if formats[name] != nil { diagnostics.changedFormat(name) }
                formats[name] = format; converters[name] = AVAudioConverter(from: format, to: outputFormat)
                converters[name]?.downmix = true
            }
            guard let converter = converters[name],
                let output = AVAudioPCMBuffer(pcmFormat: outputFormat, frameCapacity: AVAudioFrameCount(Double(sample.numSamples) * 16000 / format.sampleRate) + 128) else { diagnostics.dropped(name); return }
            var provided = false; var conversionError: NSError?
            converter.convert(to: output, error: &conversionError) { _, status in
                if provided { status.pointee = .noDataNow; return nil }
                provided = true; status.pointee = .haveData; return source
            }
            if let conversionError { throw conversionError }
            guard output.frameLength > 0 else { diagnostics.emptyOutput(name); return }
            guard let bytes = output.audioBufferList.pointee.mBuffers.mData, let file = files[name] else { diagnostics.dropped(name); return }
            let timestamp = CMTimeGetSeconds(sample.presentationTimeStamp)
            guard timestamp.isFinite else { diagnostics.dropped(name); return }
            let position = max(frames[name] ?? 0, Int64(max(0, (timestamp - origin - pausedTotal) * 16000)))
            guard position < 1_000_000_000 else { diagnostics.failureStage = "timeline_limit"; fail("录音超过单次采集大小限制，请停止并保存"); return }
            do {
                try file.seek(toOffset: UInt64(position * 2))
                try file.write(contentsOf: Data(bytes: bytes, count: Int(output.frameLength) * 2))
            } catch { diagnostics.failureStage = "storage"; fail("磁盘保存失败：\(error.localizedDescription)"); return }
            diagnostics.wrote(name, previous: frames[name] ?? 0, position: position)
            frames[name] = position + Int64(output.frameLength)
            let now = hostSeconds()
            if now - (lastLevelAt[name] ?? 0) >= 0.1, let samples = output.int16ChannelData?[0] {
                var sum: Double = 0
                for index in 0..<Int(output.frameLength) { let value = Double(samples[index]) / 32768; sum += value * value }
                var history = levels[name] ?? []
                let rms = sqrt(sum / Double(output.frameLength))
                history.append(rms)
                if rms >= 0.002 { lastSignalAt[name] = now }
                if history.count > 72 { history.removeFirst(history.count - 72) }
                levels[name] = history; lastLevelAt[name] = now
            }
        } catch { diagnostics.dropped(name); diagnostics.failureStage = "audio_conversion"; fail(error.localizedDescription) }
    }

    func pause() { queue.sync {
        guard state == "recording" else { return }
        elapsed = max(0, hostSeconds() - origin - pausedTotal); pausedAt = hostSeconds(); state = "paused"; report()
        levels.removeAll(); report()
    } }
    func resume() { queue.sync {
        guard state == "paused", let pausedAt else { return }
        pausedTotal += hostSeconds() - pausedAt; self.pausedAt = nil
        signalGraceAt = hostSeconds(); lastSignalAt.removeAll(); lastLevelAt.removeAll()
        state = "recording"; report()
    } }
    func finish(state finalState: String = "stopped") async {
        try? await stream?.stopCapture()
        queue.sync {
            if state == "recording" { elapsed = max(0, hostSeconds() - origin - pausedTotal) }
            timer?.cancel(); timer = nil; stopWatchingDevices()
            for file in files.values { try? file.synchronize(); try? file.close() }
            files.removeAll(); state = finalState; report()
        }
    }
}

let arguments = CommandLine.arguments
if arguments.contains("--diagnostics-self-test") {
    let sample = CaptureDiagnostics()
    sample.received("system"); sample.received("system"); sample.dropped("system"); sample.emptyOutput("system")
    sample.changedFormat("system")
    sample.wrote("system", previous: 16000, position: 20000)
    sample.wrote("system", previous: 24000, position: 23000)
    sample.defaultInputChanges = 1; sample.microphoneMatchesDefault = false
    jsonLine(sample.payload()); exit(0)
}
if arguments.contains("--probe") {
    jsonLine(["screen_permission": CGPreflightScreenCaptureAccess(),
              "microphone_permission": AVCaptureDevice.authorizationStatus(for: .audio) == .authorized,
              "supported": ProcessInfo.processInfo.operatingSystemVersion.majorVersion >= 15, "diagnostics_version": 1, "helper_build": 4, "ownership_protocol_version": 1, "artifact_guard_version": 1])
    exit(0)
}
guard let option = arguments.firstIndex(of: "--output"), arguments.indices.contains(option + 1) else {
    jsonLine(["error": "需要指定 --output"]); exit(2)
}
guard let identityOption = arguments.firstIndex(of:"--incarnation"), arguments.indices.contains(identityOption+1) else { jsonLine(["error":"缺少采集会话身份，请从工作台启动"]); exit(2) }
let artifactLease: CaptureArtifactLease
do { artifactLease = try CaptureArtifactLease(executable:CommandLine.arguments[0]) }
catch { jsonLine(["error":"采集器更新锁无法取得，未打开设备"]); exit(4) }
let ownerLease: CaptureOwnerLease
do { ownerLease = try CaptureOwnerLease(folder:URL(fileURLWithPath:arguments[option+1]),incarnation:arguments[identityOption+1]) }
catch { jsonLine(["error":"采集所有权无法取得，未打开设备"]); exit(3) }
let application = NSApplication.shared
application.setActivationPolicy(.accessory)
let capture = Capture(folder: URL(fileURLWithPath: arguments[option + 1]), microphone: arguments.contains("--microphone"), incarnation:ownerLease.incarnation)
Task {
    do {
        try await capture.start()
        DispatchQueue.global().async {
            while let command = readLine() {
                switch command {
                case "pause": capture.pause()
                case "resume": capture.resume()
                case "stop": Task { await capture.finish(); exit(0) }; return
                default: break
                }
            }
            Task { await capture.finish(); exit(0) }
        }
    } catch {
        capture.queue.sync { capture.diagnostics.failureStage = "startup"; capture.errorMessage = error.localizedDescription; capture.state = "failed"; capture.report() }
        exit(1)
    }
}
withExtendedLifetime((ownerLease,artifactLease)) { RunLoop.main.run() }
