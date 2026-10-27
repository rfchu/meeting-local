export type CompletionMode = "quick" | "full" | "none";
export interface RecordingPreferences {
  version: 1;
  keepAwake: boolean;
  autoProcess: boolean;
  captureSource: "browser" | "macos-system" | "macos-both";
  draftEngine: "whisper" | "qwen";
  streamEnabled: boolean;
  responsiveMode: boolean;
  completionMode: CompletionMode;
}
export const RECORDING_PREFERENCES_KEY = "tingji.recording.preferences.v1";
export const DEFAULT_RECORDING_PREFERENCES: RecordingPreferences = {
  version: 1,
  keepAwake: true,
  autoProcess: true,
  captureSource: "browser",
  draftEngine: "whisper",
  streamEnabled: true,
  responsiveMode: false,
  completionMode: "quick",
};
export function readRecordingPreferences(
  storage: Pick<Storage, "getItem">,
): RecordingPreferences {
  try {
    const value = JSON.parse(
      storage.getItem(RECORDING_PREFERENCES_KEY) || "null",
    );
    if (!value || value.version !== 1)
      return { ...DEFAULT_RECORDING_PREFERENCES };
    return {
      ...DEFAULT_RECORDING_PREFERENCES,
      autoProcess:
        typeof value.autoProcess === "boolean" ? value.autoProcess : true,
      keepAwake: typeof value.keepAwake === "boolean" ? value.keepAwake : true,
      captureSource: ["browser", "macos-system", "macos-both"].includes(
        value.captureSource,
      )
        ? value.captureSource
        : "browser",
      draftEngine: value.draftEngine === "qwen" ? "qwen" : "whisper",
      streamEnabled:
        typeof value.streamEnabled === "boolean" ? value.streamEnabled : true,
      responsiveMode:
        typeof value.responsiveMode === "boolean"
          ? value.responsiveMode
          : false,
      completionMode: ["quick", "full", "none"].includes(value.completionMode)
        ? value.completionMode
        : "quick",
    };
  } catch {
    return { ...DEFAULT_RECORDING_PREFERENCES };
  }
}
export function saveRecordingPreferences(
  storage: Pick<Storage, "setItem">,
  value: RecordingPreferences,
): boolean {
  try {
    storage.setItem(RECORDING_PREFERENCES_KEY, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}
export function transcriptionOptions(
  mode: CompletionMode,
  draftEngine: string,
  formalEngine: string,
  diarization: boolean,
  numSpeakers: number,
) {
  if (mode === "none") return null;
  return {
    engine: mode === "quick" ? draftEngine : formalEngine,
    completion_mode: mode,
    diarization: mode === "quick" ? false : diarization,
    num_speakers: numSpeakers,
  };
}
