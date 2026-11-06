import { useEffect, useState } from "react";
import { api } from "../../api";
import type { RecordingQuality, RecordingReviewStatus } from "../../types";
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
  const [view, setView] = useState<"pending" | "all">("pending");
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [saveError, setSaveError] = useState("");
  const review = async (
    item: RecordingQuality["items"][number],
    status: RecordingReviewStatus,
  ) => {
    if (busy || loading) return;
    setBusy(true);
    setSaveError("");
    try {
      await api.reviewRecordingQuality(
        id,
        item.id,
        status,
        item.review_version || 0,
      );
      setOffset(0);
      setAttempt((v) => v + 1);
    } catch (error) {
      setSaveError((error as Error).message);
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    let live = true;
    setLoading(true);
    setFailed(false);
    void api.recordingQuality(id, view, offset).then(
      (value) => {
        if (live) {
          setQuality(value);
          setLoading(false);
        }
      },
      () => {
        if (live) {
          setFailed(true);
          setLoading(false);
        }
      },
    );
    return () => {
      live = false;
    };
  }, [id, attempt, view, offset]);
  useEffect(() => {
    if (!quality?.items.some((item) => !item.closed_at) || busy) return;
    const timer = window.setTimeout(() => setAttempt((v) => v + 1), 5000);
    return () => window.clearTimeout(timer);
  }, [quality, busy]);
  if (failed)
    return (
      <p className="muted">
        录音异常核对暂不可用{" "}
        <button className="text-btn" onClick={() => setAttempt(attempt + 1)}>
          重试
        </button>
      </p>
    );
  if (!quality) return null;
  return (
    <>
      <RecordingQualityReview
        quality={quality}
        onSeek={onSeek}
        compact
        onReview={(item, status) => void review(item, status)}
        busy={busy || loading}
        controls={
          quality.available &&
          quality.total_count > 0 && (
            <div className="quality-review-controls">
              <button
                disabled={busy || loading || view === "pending"}
                onClick={() => {
                  setView("pending");
                  setOffset(0);
                }}
              >
                待核对
              </button>
              <button
                disabled={busy || loading || view === "all"}
                onClick={() => {
                  setView("all");
                  setOffset(0);
                }}
              >
                全部记录
              </button>
              <button
                disabled={busy || loading || offset === 0}
                onClick={() => setOffset((v) => Math.max(0, v - 20))}
              >
                上一页
              </button>
              <button
                disabled={
                  busy ||
                  loading ||
                  offset + quality.items.length >=
                    (view === "pending"
                      ? (quality.pending_count ?? quality.total_count)
                      : (quality.retained_count ?? quality.total_count))
                }
                onClick={() => setOffset((v) => v + 20)}
              >
                下一页
              </button>
              {busy ? (
                <span role="status">正在保存核对结论…</span>
              ) : loading ? (
                <span role="status">正在更新…</span>
              ) : null}
            </div>
          )
        }
      />
      {saveError && (
        <p role="alert">
          {saveError}{" "}
          <button
            className="text-btn"
            disabled={busy}
            onClick={() => {
              setSaveError("");
              setAttempt((v) => v + 1);
            }}
          >
            刷新核对结果
          </button>
        </p>
      )}
    </>
  );
}
