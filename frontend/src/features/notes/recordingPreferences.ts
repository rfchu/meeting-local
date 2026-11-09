import type { SourceLanguage, OutputLanguage } from "../../types";
import type { CaptureSource } from "../onboarding/readiness";
export type EchoMode = "standard" | "aec3";
export type CompletionMode = "quick" | "full" | "none";
export interface RecordingPreferences {
  sourceLanguage?: SourceLanguage;
  outputLanguage?: OutputLanguage;
  version: 1;
  contextEnabled: boolean;
  prefixEnabled: boolean;
  recognitionTerms: string;
  correctionEnabled: boolean;
  correctionProfileId: string;
  keepAwake: boolean;
  silenceReminder: boolean;
  autoProcess: boolean;
  postGoal: "transcript" | "minutes";
  captureSource: CaptureSource;
  draftEngine: "whisper" | "qwen";
  echoMode: EchoMode;
  streamEnabled: boolean;
  responsiveMode: boolean;
  completionMode: CompletionMode;
}
export const RECORDING_PREFERENCES_KEY = "tingji.recording.preferences.v1";
export const DEFAULT_RECORDING_PREFERENCES: RecordingPreferences = {
  version: 1,
  sourceLanguage: "auto",
  outputLanguage: "zh",
  contextEnabled: true,
  prefixEnabled: true,
  recognitionTerms: "",
  correctionEnabled: false,
  correctionProfileId: "",
  keepAwake: true,
  silenceReminder: true,
  autoProcess: true,
  postGoal: "transcript",
  captureSource: "browser",
  draftEngine: "whisper",
  echoMode: "standard",
  streamEnabled: true,
  responsiveMode: true,
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
      sourceLanguage: ["auto", "zh", "en", "ja"].includes(value.sourceLanguage)
        ? value.sourceLanguage
        : "auto",
      outputLanguage: value.outputLanguage === "en" ? "en" : "zh",
      prefixEnabled:
        typeof value.prefixEnabled === "boolean" ? value.prefixEnabled : true,
      contextEnabled:
        typeof value.contextEnabled === "boolean" ? value.contextEnabled : true,
      recognitionTerms:
        typeof value.recognitionTerms === "string"
          ? value.recognitionTerms.slice(0, 1000)
          : "",
      correctionEnabled: value.correctionEnabled === true,
      correctionProfileId:
        typeof value.correctionProfileId === "string"
          ? value.correctionProfileId
          : "",
      postGoal: value.postGoal === "minutes" ? "minutes" : "transcript",
      autoProcess:
        typeof value.autoProcess === "boolean" ? value.autoProcess : true,
      keepAwake: typeof value.keepAwake === "boolean" ? value.keepAwake : true,
      silenceReminder:
        typeof value.silenceReminder === "boolean"
          ? value.silenceReminder
          : true,
      captureSource: ["browser", "macos-system", "macos-both", "macos-microphone"].includes(
        value.captureSource,
      )
        ? value.captureSource
        : "browser",
      draftEngine: value.draftEngine === "qwen" ? "qwen" : "whisper",
      echoMode: value.echoMode === "aec3" ? "aec3" : "standard",
      streamEnabled:
        typeof value.streamEnabled === "boolean" ? value.streamEnabled : true,
      responsiveMode:
        typeof value.responsiveMode === "boolean" ? value.responsiveMode : true,
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
