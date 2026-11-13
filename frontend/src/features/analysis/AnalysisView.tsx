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
            <div className="analysis-empty-badge">🧠 深度研判与洞察</div>
            <h3>尚未运行会议深度分析</h3>
            <p className="analysis-empty-desc">
              深度研判结合全场文字稿，提炼议题演进脉络、观点立场透视、核心决议与行动项，并支持毫秒级精准溯源原声。
            </p>
            <div className="analysis-value-grid">
              <div className="analysis-value-item">
                <strong>议题脉络追踪</strong>
                <span>对照原文推演讨论转折与共识形成</span>
              </div>
              <div className="analysis-value-item">
                <strong>观点立场透视</strong>
                <span>梳理各方诉求、核心关切与潜在分歧</span>
              </div>
              <div className="analysis-value-item">
                <strong>决议闭环核对</strong>
                <span>萃取确定事项、责任归属与待办计划</span>
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
