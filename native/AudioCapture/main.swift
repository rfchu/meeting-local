import Foundation
import AppKit
import AVFoundation
import ScreenCaptureKit
import CoreMedia
import CoreGraphics

func hostSeconds() -> Double { CMTimeGetSeconds(CMClockGetTime(CMClockGetHostTimeClock())) }
func jsonLine(_ value: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: value) {
        FileHandle.standardOutput.write(data); FileHandle.standardOutput.write(Data([10]))
    }
}

final class Capture: NSObject, SCStreamOutput, SCStreamDelegate {
    let folder: URL
    let microphone: Bool
    let queue = DispatchQueue(label: "cn.seeknow.tingji.capture.audio")
    var stream: SCStream?
    var files: [String: FileHandle] = [:]
    var converters: [String: AVAudioConverter] = [:]
    var formats: [String: AVAudioFormat] = [:]
    var frames: [String: Int64] = [:]
    var levels: [String: [Double]] = [:]
    var lastLevelAt: [String: Double] = [:]
    var timer: DispatchSourceTimer?
    var state = "starting"
    var errorMessage = ""
    var origin = hostSeconds()
    var pausedAt: Double?
    var pausedTotal: Double = 0
    var elapsed: Double = 0
    let outputFormat = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16000, channels: 1, interleaved: true)!

    init(folder: URL, microphone: Bool) { self.folder = folder; self.microphone = microphone }

    func report() {
        if state == "recording" { elapsed = max(0, hostSeconds() - origin - pausedTotal) }
        let report: [String: Any] = ["state": state, "duration": elapsed, "error": errorMessage,
            "system_frames": frames["system"] ?? 0, "microphone_frames": frames["microphone"] ?? 0,
            "system_levels": levels["system"] ?? [], "microphone_levels": levels["microphone"] ?? [],
            "sample_rate": 16000, "updated": Date().timeIntervalSince1970]
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
            configuration.microphoneCaptureDeviceID = AVCaptureDevice.default(for: .audio)?.uniqueID
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
        stream = capture; origin = hostSeconds()
        queue.sync { state = "recording"; report() }
        try await capture.startCapture()
        queue.sync {
            let timer = DispatchSource.makeTimerSource(queue: queue)
            timer.schedule(deadline: .now(), repeating: 1)
            timer.setEventHandler { [weak self] in
                guard let self else { return }
                do { for file in self.files.values { try file.synchronize() }; self.report() }
                catch { self.fail("磁盘保存失败：\(error.localizedDescription)") }
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
        queue.async { self.fail("采集中断：\(error.localizedDescription)") }
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sample: CMSampleBuffer, of type: SCStreamOutputType) {
        guard state == "recording", sample.isValid, let description = sample.formatDescription else { return }
        let name: String
        if type == .audio { name = "system" }
        else if #available(macOS 15.0, *), type == .microphone { name = "microphone" }
        else { return }
        let format = AVAudioFormat(cmAudioFormatDescription: description)
        guard let source = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(sample.numSamples)) else { return }
        source.frameLength = AVAudioFrameCount(sample.numSamples)
        do {
            try sample.copyPCMData(fromRange: 0..<sample.numSamples, into: source.mutableAudioBufferList)
            if formats[name] != format {
                formats[name] = format; converters[name] = AVAudioConverter(from: format, to: outputFormat)
                converters[name]?.downmix = true
            }
            guard let converter = converters[name],
                let output = AVAudioPCMBuffer(pcmFormat: outputFormat, frameCapacity: AVAudioFrameCount(Double(sample.numSamples) * 16000 / format.sampleRate) + 128) else { return }
            var provided = false; var conversionError: NSError?
            converter.convert(to: output, error: &conversionError) { _, status in
                if provided { status.pointee = .noDataNow; return nil }
                provided = true; status.pointee = .haveData; return source
            }
            if let conversionError { throw conversionError }
            guard output.frameLength > 0, let bytes = output.audioBufferList.pointee.mBuffers.mData, let file = files[name] else { return }
            let timestamp = CMTimeGetSeconds(sample.presentationTimeStamp)
            guard timestamp.isFinite else { return }
            let position = max(frames[name] ?? 0, Int64(max(0, (timestamp - origin - pausedTotal) * 16000)))
            guard position < 1_000_000_000 else { throw NSError(domain: "TingjiCapture", code: 4, userInfo: [NSLocalizedDescriptionKey: "录音超过单次采集大小限制，请停止并保存"] ) }
            try file.seek(toOffset: UInt64(position * 2))
            try file.write(contentsOf: Data(bytes: bytes, count: Int(output.frameLength) * 2))
            frames[name] = position + Int64(output.frameLength)
            let now = hostSeconds()
            if now - (lastLevelAt[name] ?? 0) >= 0.1, let samples = output.int16ChannelData?[0] {
                var sum: Double = 0
                for index in 0..<Int(output.frameLength) { let value = Double(samples[index]) / 32768; sum += value * value }
                var history = levels[name] ?? []
                history.append(sqrt(sum / Double(output.frameLength)))
                if history.count > 72 { history.removeFirst(history.count - 72) }
                levels[name] = history; lastLevelAt[name] = now
            }
        } catch { fail(error.localizedDescription) }
    }

    func pause() { queue.sync {
        guard state == "recording" else { return }
        elapsed = max(0, hostSeconds() - origin - pausedTotal); pausedAt = hostSeconds(); state = "paused"; report()
        levels.removeAll(); report()
    } }
    func resume() { queue.sync {
        guard state == "paused", let pausedAt else { return }
        pausedTotal += hostSeconds() - pausedAt; self.pausedAt = nil; state = "recording"; report()
    } }
    func finish(state finalState: String = "stopped") async {
        try? await stream?.stopCapture()
        queue.sync {
            if state == "recording" { elapsed = max(0, hostSeconds() - origin - pausedTotal) }
            timer?.cancel(); timer = nil
            for file in files.values { try? file.synchronize(); try? file.close() }
            files.removeAll(); state = finalState; report()
        }
    }
}

let arguments = CommandLine.arguments
if arguments.contains("--probe") {
    jsonLine(["screen_permission": CGPreflightScreenCaptureAccess(),
              "microphone_permission": AVCaptureDevice.authorizationStatus(for: .audio) == .authorized,
              "supported": ProcessInfo.processInfo.operatingSystemVersion.majorVersion >= 15])
    exit(0)
}
guard let option = arguments.firstIndex(of: "--output"), arguments.indices.contains(option + 1) else {
    jsonLine(["error": "需要指定 --output"]); exit(2)
}
let application = NSApplication.shared
application.setActivationPolicy(.accessory)
let capture = Capture(folder: URL(fileURLWithPath: arguments[option + 1]), microphone: arguments.contains("--microphone"))
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
        capture.queue.sync { capture.errorMessage = error.localizedDescription; capture.state = "failed"; capture.report() }
        exit(1)
    }
}
RunLoop.main.run()
