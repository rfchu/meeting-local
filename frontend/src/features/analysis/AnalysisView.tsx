import type { Environment, Meeting } from "../../types";
import { download } from "../../api";
import { resolveAnalysisStaleNotice } from "../minutes/presentation";
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
  onSeek: (
    time: number,
    track?: import("../../types").Segment["source_track"],
  ) => void;
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
  const staleNotice = resolveAnalysisStaleNotice({
    artifact,
    meetingRevision: meeting.revision,
    transcriptDirty,
  });
  const canGenerateAnalysis = Boolean(
    meeting.revision && model && environment?.analysis.ready && !running,
  );
  const byId = new Map(meeting.segments.map((item) => [item.id, item]));
  const showJobCard =
    !!job && (running || ["failed", "cancelled"].includes(job.state));
  return (
    <div id="analysisView">
      <div className="analysis-top">
        <p className="analysis-subtitle muted">
          内部复盘 · 对照原文核对讨论、决定、待办和未决问题。
        </p>
        <div className="analysis-toolbar">
          <button
            className="primary"
            disabled={!canGenerateAnalysis}
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
            {profile?.name || model || "配置分析模型"} · 运行与模型管理
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
      </div>
      {showJobCard && job && (
        <div id="analysisJob">
          <div className="job-head">
            <span>{job.stage}</span>
            <button
              className="text-btn"
              onClick={() => onJobAction(job.id, running ? "cancel" : "retry")}
            >
              {running ? "取消任务" : "从检查点重试"}
            </button>
          </div>
          <progress max={1} value={job.progress} />
          {job.error ? <pre id="analysisError">{job.error}</pre> : null}
        </div>
      )}
      {staleNotice && (
        <div className="analysis-notice stale-compact-notice" role="status">
          <span className="stale-notice-text">{staleNotice.message}</span>
          <button
            type="button"
            className="notice-action-btn stale-regen-btn"
            disabled={!canGenerateAnalysis}
            onClick={onGenerate}
          >
            {running ? "正在生成…" : staleNotice.actionLabel}
          </button>
        </div>
      )}
      {!artifact ? (
        <div className="analysis-empty-dashboard">
          <div className="analysis-empty-card">
            <div className="analysis-empty-badge">深度研判与洞察</div>
            <h3>尚未运行会议深度分析</h3>
            <p className="analysis-empty-desc">
              基于当前完整文字稿提炼会议概述、关键讨论、已定事项、待办与待确认问题，每条结论均关联原文片段，点击时间戳即可定位回听。
            </p>
            <div className="analysis-value-grid">
              <div className="analysis-value-item">
                <strong>会议概述与关键讨论</strong>
                <span>提炼核心内容与重要讨论点，关联原文出处</span>
              </div>
              <div className="analysis-value-item">
                <strong>已定事项与待办清单</strong>
                <span>整理明确决议、负责人与截止时间</span>
              </div>
              <div className="analysis-value-item">
                <strong>待确认问题与原声回听</strong>
                <span>汇总待澄清事项，点击引用时间定位原声片段</span>
              </div>
            </div>
            {environment?.analysis.ready && meeting.revision ? (
              <button
                className="primary analysis-start-btn"
                disabled={running}
                onClick={onGenerate}
              >
                {running ? "正在研判中…" : "立即生成会议深度研判"}
              </button>
            ) : (
              <p className="muted">
                {!meeting.revision
                  ? "请先完成录音转写，再生成深度研判。"
                  : "请先在「运行与模型管理」中配置可用的分析模型。"}
              </p>
            )}
          </div>
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
                            onClick={() =>
                              onSeek(segment!.start, segment!.source_track)
                            }
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
