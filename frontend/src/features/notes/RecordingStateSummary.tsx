import type { RecordingRuntime } from "../../types";
import { recordingStatus } from "./recordingStatus";
import RecordingControlNotice from "./RecordingControlNotice";
export default function RecordingStateSummary({
  id,
  runtime,
  unavailable = false,
}: {
  id: string;
  runtime?: RecordingRuntime;
  unavailable?: boolean;
}) {
  const view = recordingStatus(runtime, "", unavailable || !runtime);
  return (
    <div className="recording-state-summary" aria-label="统一录音状态">
      <strong>{view.label}</strong>
      <p>
        {view.input || "采集状态待核对"} · {view.storage}
      </p>
      <p>{view.asr || "识别状态待核对"}</p>
      {view.savedPosition && (
        <p>
          {unavailable ? "上次核对：" : ""}
          {view.savedPosition}
        </p>
      )}
      <RecordingControlNotice
        id={id}
        runtime={runtime}
        unavailable={unavailable}
      />
    </div>
  );
}
