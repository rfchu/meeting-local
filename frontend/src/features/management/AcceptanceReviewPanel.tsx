import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
export interface AcceptanceAssessment {
  evidence_fingerprint: string;
  automatic_hardware_certification: boolean;
  cases: {
    case: string;
    label: string;
    eligible: boolean;
    state: string;
    reasons: string[];
  }[];
}
const labels: Record<string, string> = {
  passed: "人工核对通过",
  failed: "未通过",
  stale: "证据或版本已变，需复核",
  needs_review: "待回听核对",
  insufficient_evidence: "证据不足",
};
export default function AcceptanceReviewPanel({ id }: { id: string }) {
  const query = useQuery({
    queryKey: ["recording-acceptance", id],
    queryFn: () => api.recordingAcceptance(id),
    retry: false,
    gcTime: 0,
  });
  const client = useQueryClient();
  const [selected, setSelected] = useState("short_capture");
  const [real, setReal] = useState(false);
  const [listened, setListened] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const item = query.data?.cases.find((c) => c.case === selected);
  async function save(verdict: "passed" | "failed") {
    if (!query.data || busy) return;
    setBusy(true);
    setMessage("");
    try {
      await api.reviewRecordingAcceptance(id, {
        case: selected,
        evidence_fingerprint: query.data.evidence_fingerprint,
        verdict,
        real_device: real,
        audio_reviewed: listened,
      });
      setMessage("已保存本项人工验收；其他场景仍需分别验证。");
      setReal(false);
      setListened(false);
      await query.refetch();
      await client.invalidateQueries({ queryKey: ["release-readiness"] });
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "保存未确认，请重新核对。");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div aria-label="设备验收核对">
      <p>
        只核对这次观察对应的场景。请回听原录音；文件增长、计时结束和人工标记都不自动证明声音完整。
      </p>
      {query.isError && (
        <p role="alert">
          验收证据暂不可用。
          <button onClick={() => void query.refetch()}>重新读取</button>
        </p>
      )}
      {query.data && (
        <>
          <label>
            验收场景{" "}
            <select
              value={selected}
              disabled={busy}
              onChange={(e) => {
                setSelected(e.target.value);
                setReal(false);
                setListened(false);
                setMessage("");
              }}
            >
              {query.data.cases.map((c) => (
                <option key={c.case} value={c.case}>
                  {c.label} · {labels[c.state] || "待核对"}
                </option>
              ))}
            </select>
          </label>
          {item?.reasons.map((reason) => (
            <p key={reason}>{reason}</p>
          ))}
          <label>
            <input
              type="checkbox"
              checked={real}
              disabled={busy}
              onChange={(e) => setReal(e.target.checked)}
            />
            我确认本项来自真实设备及所述现场操作
          </label>
          <label>
            <input
              type="checkbox"
              checked={listened}
              disabled={busy}
              onChange={(e) => setListened(e.target.checked)}
            />
            我已回听相关音轨，核对本场景前后的预期声音
          </label>
          <div className="quality-review-controls">
            <button
              disabled={
                busy ||
                query.isError ||
                query.isFetching ||
                !item?.eligible ||
                !real ||
                !listened
              }
              onClick={() => void save("passed")}
            >
              记录本项通过
            </button>
            <button
              disabled={busy || query.isError || query.isFetching}
              onClick={() => void save("failed")}
            >
              记录本项未通过
            </button>
          </div>
          <p className="muted">
            结论绑定当前观察证据和运行版本，更新后需复核；不代表独立发行认证。
          </p>
        </>
      )}
      {message && <p role="status">{message}</p>}
    </div>
  );
}
