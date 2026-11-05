import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import type { ManagementPage } from "./RuntimeManagement";
export interface RuntimeTask {
  id: string;
  meeting_id: string;
  meeting_title: string | null;
  kind: string;
  kind_label: string;
  state: string;
  stage: string;
  progress: number;
  created: string;
  waiting_reason: string;
  queue_position: number | null;
  model: string;
  revision: number | null;
  can_cancel: boolean;
  can_retry: boolean;
  retry_blocked: string;
  exiting: boolean;
  recorded_retries: number;
  failure: {
    code: string;
    summary: string;
    advice: string;
    target: string;
  } | null;
}
export interface RuntimeTasks {
  items: RuntimeTask[];
  counts: Record<string, number>;
  limit: number;
  live_overview: {
    recording_id: string;
    state: string;
    enabled: number;
    failures: number;
    published: boolean;
    failure: RuntimeTask["failure"];
  }[];
}
const states: Record<string, string> = {
  queued: "排队中",
  running: "处理中",
  failed: "失败",
  cancelled: "已取消",
  completed: "已完成",
};
export default function TasksPanel({
  onPage,
  onMeeting,
  onRecording,
  onSearch,
}: {
  onPage: (p: ManagementPage) => void;
  onMeeting: (id: string) => void;
  onRecording: () => void;
  onSearch: () => void;
}) {
  const [filter, setFilter] = useState<"active" | "failed" | "all">("active"),
    [busy, setBusy] = useState(""),
    [error, setError] = useState("");
  const client = useQueryClient();
  const query = useQuery({
    queryKey: ["runtime-tasks", filter],
    queryFn: () => api.runtimeTasks(filter),
    refetchInterval: 5000,
  });
  const overview = (query.data?.live_overview || []).filter(
    (v) => filter !== "active" || v.enabled,
  );
  async function action(task: RuntimeTask, kind: "cancel" | "retry") {
    setBusy(task.id);
    setError("");
    try {
      await api.jobAction(task.id, kind);
      await client.invalidateQueries({ queryKey: ["runtime-tasks"] });
      void client.invalidateQueries({ queryKey: ["runtime-overview"] });
    } catch (e) {
      setError((e as Error).message);
      void query.refetch();
    } finally {
      setBusy("");
    }
  }
  return (
    <section className="management-tasks">
      <div className="management-task-toolbar">
        <div role="group" aria-label="任务筛选">
          {(
            [
              ["active", "进行中"],
              ["failed", "失败与已取消"],
              ["all", "全部"],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              aria-pressed={filter === key}
              onClick={() => setFilter(key)}
            >
              {label}
            </button>
          ))}
        </div>
        <a
          href="/api/runtime/diagnostics"
          download="tingji-runtime-diagnostics.json"
        >
          导出脱敏诊断
        </a>
      </div>
      <p className="muted">
        重试沿用原任务绑定的模型和资料版本。取消先发出停止请求，进程退出后才可重试。
      </p>
      {(error || query.error) && (
        <p role="alert" className="management-error">
          {error || query.error?.message}
        </p>
      )}
      {query.isPending && <p role="status">正在读取任务…</p>}
      {query.data && !query.data.items.length && (
        <div className="management-section">
          <p className="muted">
            {filter === "active"
              ? "暂无后台任务。录音与会中概览独立运行。"
              : "此筛选下暂无任务记录。"}
          </p>
        </div>
      )}
      {query.data?.items.map((task) => (
        <article className="management-section management-task" key={task.id}>
          <div className="management-task-heading">
            <div>
              <h3>{task.kind_label}</h3>
              <span>{task.meeting_title || "本机工作空间"}</span>
            </div>
            <strong className={`task-state ${task.state}`}>
              {task.exiting ? "停止处理中" : states[task.state] || task.state}
            </strong>
          </div>
          <p>
            {task.waiting_reason || task.stage || states[task.state]}
            {task.state === "queued" && task.queue_position
              ? ` · 队列第 ${task.queue_position} 项`
              : ""}
          </p>
          {["queued", "running"].includes(task.state) && (
            <progress
              aria-label={`${task.kind_label}进度`}
              max={1}
              value={Math.max(0, Math.min(1, task.progress || 0))}
            />
          )}
          {task.failure && (
            <div className="management-task-failure">
              <strong>{task.failure.summary}</strong>
              <p>{task.failure.advice}</p>
            </div>
          )}
          <div className="management-task-actions">
            {task.can_cancel && (
              <button
                disabled={!!busy}
                onClick={() => void action(task, "cancel")}
              >
                取消任务
              </button>
            )}
            {["failed", "cancelled"].includes(task.state) && (
              <button
                disabled={!!busy || !task.can_retry}
                title={task.retry_blocked}
                onClick={() => void action(task, "retry")}
              >
                重试任务
              </button>
            )}
            {task.failure &&
              ["models", "environment", "index"].includes(
                task.failure.target,
              ) && (
                <button
                  onClick={() => onPage(task.failure!.target as ManagementPage)}
                >
                  检查
                  {task.failure.target === "models"
                    ? "模型配置"
                    : task.failure.target === "index"
                      ? "语义索引"
                      : "运行环境"}
                </button>
              )}
            {!!task.meeting_id && (
              <button
                className="text-btn"
                onClick={() => onMeeting(task.meeting_id)}
              >
                打开会议
              </button>
            )}
          </div>
          {["failed", "cancelled"].includes(task.state) && !task.can_retry && (
            <p className="muted">{task.retry_blocked}</p>
          )}
          <details className="management-task-details">
            <summary>任务信息</summary>
            <p>
              创建于{" "}
              {new Date(task.created.replace(" ", "T") + "Z").toLocaleString(
                "zh-CN",
                { hour12: false },
              )}{" "}
              · 本版记录重试 {task.recorded_retries} 次
            </p>
            {task.model && <p>绑定模型：{task.model}</p>}
            {task.revision != null && <p>绑定原文版本：{task.revision}</p>}
            <code>{task.id}</code>
          </details>
        </article>
      ))}
      {!!overview.length && (
        <details className="management-section">
          <summary>会中概览状态与近期失败 · {overview.length} 条</summary>
          {overview.map((v) => (
            <div className="management-row" key={v.recording_id}>
              <span>
                概览{v.enabled ? "已开启" : "已停止"} · 连续失败 {v.failures} 次
                {v.failure ? ` · ${v.failure.summary}` : ""}
              </span>
              <small>{v.recording_id.slice(0, 8)}</small>
              <button
                onClick={() =>
                  v.published ? onMeeting(v.recording_id) : onRecording()
                }
              >
                {v.published ? "返回会议查看" : "返回录音现场"}
              </button>
            </div>
          ))}
        </details>
      )}
    </section>
  );
}
