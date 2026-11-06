import type { RecordingRuntime } from "../../types";

/** Presentation coordination only. Never issues capture or inference commands. */
export function coordinateRecording(
  runtime: RecordingRuntime | undefined,
  local: string,
  unavailable: boolean,
) {
  const unsupported =
    runtime?.schema_version != null && runtime.schema_version !== 1;
  const unresolvedFailure =
    local === "failed" &&
    ["recording", "paused", "starting"].includes(runtime?.phase || "");
  const phaseMismatch =
    ["recording", "paused"].includes(local) &&
    ["recording", "paused"].includes(runtime?.phase || "") &&
    local !== runtime?.phase;
  const unknown =
    unavailable || unsupported || unresolvedFailure || phaseMismatch;
  const phase =
    local === "finished"
      ? "saved"
      : unknown
        ? "unknown"
        : runtime?.phase || (local === "failed" ? "interrupted" : local);
  const capture =
    local === "finished"
      ? "stopped"
      : unknown
        ? "unknown"
        : runtime?.capture.state;
  return { phase, capture, unavailable: unavailable || unsupported };
}
