import { useQuery } from "@tanstack/react-query";
import { api } from "../../api";
export interface ResourceStatus {
  version: number;
  checked_at: number;
  process_local: boolean;
  closed: boolean;
  live_session_count?: number;
  deferred_truncated?: boolean;
  resources: { id: string; label: string; capacity: number; used: number }[];
  requests: {
    id: string;
    workload: string;
    state: string;
    reason: string;
    wait_seconds: number;
  }[];
}
const labels: Record<string, string> = {
  live_asr: "实时转写",
  speaker_review: "发言人辅助",
  transcription: "音频转写",
  analysis: "会议分析",
  minutes: "AI 纪要",
  note_expansion: "简记整理",
  terminology: "术语整理",
  index: "检索索引",
  qa: "引用问答",
  semantic_search: "语义检索",
  embedding_config: "向量模型配置",
  live_overview: "会中概览",
  live_correction: "实时纠错",
};
export default function ResourceStatusPanel() {
  const query = useQuery({
    queryKey: ["runtime-resources"],
    queryFn: api.runtimeResources,
    refetchInterval: 5000,
  });
  const data = query.data;
  if (query.error)
    return (
      <section className="management-section">
        <h3>计算资源</h3>
        <p role="alert">资源状态暂不可用，请重新核对。</p>
        <button onClick={() => void query.refetch()}>重新核对资源</button>
      </section>
    );
  if (!data) return <p role="status">正在核对计算资源…</p>;
  const waiting = data.requests.filter((r) => r.state !== "active");
  const active = data.requests.filter((r) => r.state === "active");
  return (
    <section className="management-section" aria-label="计算资源状态">
      <h3>计算资源</h3>
      <p role="status">
        {data.closed
          ? "资源协调器已关闭"
          : `${active.length} 项处理中 · ${data.deferred_truncated ? "至少 " : ""}${waiting.length} 项等待或让行`}
      </p>
      {!!data.live_session_count && (
        <p role="status">
          会中识别优先 · {data.live_session_count}{" "}
          场进行中；已执行的调用完成后在安全边界让行。
        </p>
      )}
      <p className="muted">
        录音独立保存；这里显示已接入任务的并发额度，不代表内存用量或外部模型状态。
      </p>
      {waiting.slice(0, 8).map((r) => (
        <div className="management-row" key={r.id}>
          <strong>{labels[r.workload] || "计算任务"}</strong>
          <span>{r.reason || "等待资源调度"}</span>
          <small>已等待 {Math.floor(r.wait_seconds)} 秒</small>
        </div>
      ))}
      {waiting.length > 8 && (
        <p className="muted">仅展示前 8 项等待，全部计数已包含在上方。</p>
      )}
      <details>
        <summary>查看并发额度与当前任务</summary>
        {data.resources.map((r) => (
          <div className="management-row" key={r.id}>
            <strong>{r.label}</strong>
            <span>
              {r.used} / {r.capacity}
            </span>
          </div>
        ))}
        {active.map((r) => (
          <p key={r.id}>{labels[r.workload] || "计算任务"} · 处理中</p>
        ))}
        <small>
          本次服务内的观测 ·{" "}
          {new Date(data.checked_at * 1000).toLocaleTimeString("zh-CN", {
            hour12: false,
          })}
          ；重启后重新记录。
        </small>
      </details>
    </section>
  );
}
