import { useEffect, useState } from "react";
import { api } from "../../api";
import type { RecordingQuality } from "../../types";
import RecordingQualityReview from "./RecordingQualityReview";
export default function MeetingRecordingQuality({
  id,
  onSeek,
}: {
  id: string;
  onSeek: (seconds: number) => void;
}) {
  const [quality, setQuality] = useState<RecordingQuality | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    setQuality(null);
    setFailed(false);
    void api.recordingQuality(id).then(
      (value) => {
        if (live) setQuality(value);
      },
      () => {
        if (live) setFailed(true);
      },
    );
    return () => {
      live = false;
    };
  }, [id, attempt]);
  if (failed)
    return (
      <p className="muted">
        录音异常核对暂不可用{" "}
        <button className="text-btn" onClick={() => setAttempt(attempt + 1)}>
          重试
        </button>
      </p>
    );
  return quality ? (
    <RecordingQualityReview quality={quality} onSeek={onSeek} compact />
  ) : null;
}
