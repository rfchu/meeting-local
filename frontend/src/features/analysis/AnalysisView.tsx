import type { Environment, Meeting } from "../../types";
import { download } from "../../api";
import {
  clock,
  currentAnalysis,
  isRunning,
  latestAnalysis,
  latestJob,
} from "../../utils";

const labels = {
  summary: "会议概述",
  key_points: "关键讨论",
  decisions: "已定事项",
  action_items: "待办事项",
  open_questions: "待确认问题",
} as const;

interface Props {
  meeting: Meeting;
  environment?: Environment;
  model: string;
  onGenerate: () => void;
  onCompose: () => void;
  composing: boolean;
  onConfigure: () => void;
  onJobAction: (id: string, action: "cancel" | "retry") => void;
  onSeek: (time: number) => void;
  transcriptDirty: boolean;
}

export default function AnalysisView({
  meeting,
  environment,
  model,
  onGenerate,
  onCompose,
  composing,
  onConfigure,
  onJobAction,
  onSeek,
  transcriptDirty,
}: Props) {
  const job = latestJob(meeting, "analysis"),
    running = isRunning(job),
    current = currentAnalysis(meeting),
    artifact = current || latestAnalysis(meeting);
  const profile = environment?.analysis.profiles.find(
    (item) => item.id === model,
  );
  const stale =
    artifact && (transcriptDirty || artifact.revision !== meeting.revision);
  const byId = new Map(meeting.segments.map((item) => [item.id, item]));
  return (
    <div id="analysisView">
      <p className="muted">
        内部复盘 · 对照原文核对讨论、决定、待办和未决问题。
      </p>
      <div className="analysis-toolbar">
        <button
          className="primary"
          disabled={
            !meeting.revision ||
            !model ||
            !environment?.analysis.ready ||
            running
          }
          onClick={onGenerate}
        >
          {current ? "重新生成会议分析" : "生成会议分析"}
        </button>
        <button
          disabled={!current || transcriptDirty || composing || running}
          onClick={onCompose}
        >
          用这份分析生成纪要草稿
        </button>
        <button className="text-btn" onClick={onConfigure}>
          {profile?.name || model || "配置分析模型"} · 运行管理
        </button>
        <select
          id="analysisExport"
          aria-label="导出会议分析"
          value=""
          onChange={(e) => {
            if (e.target.value)
              download(
                `/meetings/${meeting.id}/analysis/export/${e.target.value}`,
              );
          }}
        >
          <option value="">导出分析 ↓</option>
          <option value="md">Markdown</option>
          <option value="json">结构化 JSON</option>
        </select>
      </div>
      {job && (
        <div id="analysisJob">
          <div className="job-head">
            <span>{job.stage}</span>
            {(running || ["failed", "cancelled"].includes(job.state)) && (
              <button
                className="text-btn"
                onClick={() =>
                  onJobAction(job.id, running ? "cancel" : "retry")
                }
              >
                {running ? "取消任务" : "从检查点重试"}
              </button>
            )}
          </div>
          <progress max={1} value={job.progress} />
          <pre id="analysisError">{job.error}</pre>
        </div>
      )}
      {stale && (
        <div className="analysis-notice">
          {transcriptDirty
            ? "文字稿正在修改；保存后当前会议分析将过期，请重新生成。"
            : `该分析基于 v${artifact.revision}，当前文字稿为 v${meeting.revision}，建议重新生成。`}
        </div>
      )}
      {!artifact ? (
        <div className="empty">
          <div>◎</div>
          <h3>还没有会议分析</h3>
          <p>
            {environment?.analysis.ready
              ? "生成后可核对摘要、决定和待办。"
              : "请先配置本地或云端分析模型。"}
          </p>
        </div>
      ) : (
        <div className="analysis-sections">
          <div className="analysis-meta">
            基于文字稿 v{artifact.revision} ·{" "}
            {String(artifact.provenance?.model || "本地模型")}
          </div>
          {Object.entries(labels).map(([key, label]) => (
            <section className="analysis-section" key={key}>
              <h3>{label}</h3>
              {(artifact.payload[key as keyof typeof labels] || []).length ===
                0 && <p className="analysis-empty">无明确内容</p>}
              {(artifact.payload[key as keyof typeof labels] || []).map(
                (item, index) => (
                  <article className="analysis-item" key={index}>
                    <p>{item.content}</p>
                    {(item.owner || item.due_date) && (
                      <div className="analysis-meta">
                        {item.owner && `负责人：${item.owner}`}
                        {item.owner && item.due_date && " · "}
                        {item.due_date && `截止：${item.due_date}`}
                      </div>
                    )}
                    <div className="citations">
                      {item.source_segment_ids
                        .map((id) => byId.get(id))
                        .filter(Boolean)
                        .map((segment) => (
                          <button
                            key={segment!.id}
                            className="citation"
                            title={`${segment!.speaker}：${segment!.text}`}
                            onClick={() => onSeek(segment!.start)}
                          >
                            {clock(segment!.start)}
                          </button>
                        ))}
                    </div>
                  </article>
                ),
              )}
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
