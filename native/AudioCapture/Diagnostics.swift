import Foundation

// Only counts and enums leave this object; device identities remain in Capture.
final class CaptureDiagnostics {
    var defaultInputChanges = 0
    var defaultOutputChanges = 0
    var connections = 0
    var disconnections = 0
    var listenerFailures = 0
    var deviceQueryFailures = 0
    var microphoneMatchesDefault: Bool?
    var failureStage = "none"
    var streamErrorCode: Int?
    var inputBuffers: [String: Int] = [:]
    var emptyOutputs: [String: Int] = [:]
    var droppedBuffers: [String: Int] = [:]
    var formatChanges: [String: Int] = [:]
    var gapFrames: [String: Int64] = [:]

    func received(_ track: String) { inputBuffers[track, default: 0] += 1 }
    func emptyOutput(_ track: String) { emptyOutputs[track, default: 0] += 1 }
    func dropped(_ track: String) { droppedBuffers[track, default: 0] += 1 }
    func changedFormat(_ track: String) { formatChanges[track, default: 0] += 1 }
    func wrote(_ track: String, previous: Int64, position: Int64) {
        gapFrames[track, default: 0] += max(0, position - previous)
    }
    func payload() -> [String: Any] {
        let tracks = Dictionary(uniqueKeysWithValues: ["system", "microphone"].map { name in
            (name, ["input_buffers": Int64(inputBuffers[name] ?? 0), "dropped_buffers": Int64(droppedBuffers[name] ?? 0),
                    "empty_outputs": Int64(emptyOutputs[name] ?? 0), "format_changes": Int64(formatChanges[name] ?? 0), "gap_frames": gapFrames[name] ?? 0])
        })
        return ["version": 1, "helper_build": 3, "default_input_changes": defaultInputChanges, "default_output_changes": defaultOutputChanges,
                "audio_device_connections": connections, "audio_device_disconnections": disconnections,
                "listener_failures": listenerFailures, "device_query_failures": deviceQueryFailures, "configured_microphone_is_default": microphoneMatchesDefault as Any? ?? NSNull(),
                "failure_stage": failureStage, "stream_error_code": streamErrorCode as Any? ?? NSNull(), "tracks": tracks]
    }
}
