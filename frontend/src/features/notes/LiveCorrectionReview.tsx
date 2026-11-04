import { useEffect, useState } from "react";
import Modal from "../../components/Modal";
import { api } from "../../api";
import type { LiveCorrection, LiveDraftSegment } from "../../types";

export default function LiveCorrectionReview({
  recordingId,
  canDecide = false,
  onClose,
  onChange,
  drafts = [],
  profileId = "",
}: {
  recordingId: string;
  canDecide?: boolean;
  onClose: () => void;
  onChange?: (values: LiveCorrection[]) => void;
  drafts?: LiveDraftSegment[];
  profileId?: string;
}) {
  const [values, setValues] = useState<LiveCorrection[]>([]),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const load = async () => {
    const result = await api.liveCorrections(recordingId);
    setValues(result);
    onChange?.(result);
  };
  useEffect(() => {
    void load().catch(() => setError("纠错记录暂时不可用"));
  }, [recordingId]);
  const decide = async (
    id: string,
    state: "accepted" | "rejected" | "suggested",
  ) => {
    setBusy(true);
    setError("");
    try {
      await api.decideLiveCorrection(recordingId, id, state);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const retry = async () => {
    setBusy(true);
    setError("");
    try {
      for (const draft of drafts.filter(
        (s) =>
          s.live_range_id &&
          !values.some(
            (v) =>
              v.range_id === s.live_range_id &&
              v.segment_index === (s.live_segment_index || 0) &&
              v.profile_id === profileId,
          ),
      )) {
        await api.proposeLiveCorrection(
          recordingId,
          draft.live_range_id!,
          draft.live_segment_index || 0,
          profileId,
        );
      }
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      ariaLabel="实时纠错记录"
      onClose={onClose}
      className="live-correction-review"
    >
      <div className="search-heading">
        <div>
          <h2>实时纠错记录</h2>
          <p>
            原始识别始终保留。建议经确认后用于实时文字及快速完成；精细转写会重新识别音频。
          </p>
        </div>
        <button onClick={onClose}>关闭</button>
      </div>
      {!canDecide && (
        <p>会议已保存，以下为历史记录。需要调整时请在文字稿中校对。</p>
      )}
      {error && <p role="alert">{error}</p>}
      {canDecide && profileId && (
        <button disabled={busy} onClick={() => void retry()}>
          {busy ? "校对中…" : "重试未完成校对"}
        </button>
      )}
      {!values.length && (
        <p>暂无纠错记录。开启智能纠错后，对已保存的千问片段进行本地校对。</p>
      )}
      {values.map((v) => (
        <article className="live-correction-item" key={v.id}>
          <header>
            <strong>
              {
                {
                  suggested: "待确认",
                  accepted: "已采用",
                  rejected: "已忽略",
                  unchanged: "未发现需修改内容",
                }[v.state]
              }
            </strong>
            <small>{v.model}</small>
          </header>
          <p className="live-correction-original">
            原始识别：{v.original_text}
          </p>
          {v.edits.map((edit, i) => (
            <div key={i}>
              <p>
                <del>{edit.original}</del> → <ins>{edit.replacement}</ins>
              </p>
              <small>{edit.reason}</small>
            </div>
          ))}
          {canDecide && v.edits.length > 0 && (
            <div className="live-correction-actions">
              {v.state === "suggested" && (
                <>
                  <button
                    disabled={busy}
                    onClick={() => void decide(v.id, "accepted")}
                  >
                    确认采用
                  </button>
                  <button
                    disabled={busy}
                    onClick={() => void decide(v.id, "rejected")}
                  >
                    忽略
                  </button>
                </>
              )}
              {v.state !== "suggested" && (
                <button
                  disabled={busy}
                  onClick={() => void decide(v.id, "suggested")}
                >
                  {v.state === "accepted" ? "撤销采用" : "重新审阅"}
                </button>
              )}
            </div>
          )}
        </article>
      ))}
    </Modal>
  );
}
