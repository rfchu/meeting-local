import CaptureControlPanel from "./CaptureControlPanel";
import { clock } from "../../utils";
import type { RuntimeOverview, ManagementPage } from "./types";
import {
  IconAlertTriangle,
  IconCheck,
  IconClock,
  IconCoffee,
  IconCpu,
  IconMic,
  IconSettings,
  IconZap,
} from "../../styles/icons";

export default function RuntimeOverviewPage({
  data,
  onPage,
}: {
  data: RuntimeOverview;
  onPage: (page: ManagementPage) => void;
}) {
  const readyEngines = data.engines.filter((e) => e.ready).length;
  const totalEngines = data.engines.length;
  const pendingJobs =
    (data.job_counts.running || 0) + (data.job_counts.queued || 0);

  const hasIssues =
    data.issues.length > 0 ||
    !data.storage.writable ||
    !data.dependencies.ffmpeg;

  return (
    <div className="runtime-overview-container">
      {/* 4 大核心 KPI 指标卡 */}
      <div className="management-cards">
        <article
          className="management-card-kpi clickable"
          onClick={() => onPage("overview")}
          title="点击查看会话详情"
        >
          <div className="kpi-header">
            <span className="kpi-icon">
              <IconMic size={16} />
            </span>
            <span
              className={`kpi-status-badge ${
                data.sessions.length ? "active" : "idle"
              }`}
            >
              {data.sessions.length ? "● 采集中" : "○ 就绪待命"}
            </span>
          </div>
          <div className="kpi-body">
            <span className="kpi-label">实时识别会话</span>
            <div className="kpi-metric">
              <strong>{data.sessions.length}</strong>
              <small>场进行中</small>
            </div>
            <p className="kpi-desc">包含运行、暂停与收尾中的流式会话</p>
          </div>
        </article>

        <article
          className="management-card-kpi clickable"
          onClick={() => onPage("models")}
          title="点击管理语音识别引擎"
        >
          <div className="kpi-header">
            <span className="kpi-icon">
              <IconZap size={16} />
            </span>
            <span
              className={`kpi-status-badge ${
                readyEngines === totalEngines ? "ready" : "warning"
              }`}
            >
              {readyEngines === totalEngines ? "✓ 全部就绪" : "部分待就绪"}
            </span>
          </div>
          <div className="kpi-body">
            <span className="kpi-label">离线语音识别 (ASR)</span>
            <div className="kpi-metric">
              <strong>
                {readyEngines}
                <span className="metric-divider">/</span>
                {totalEngines}
              </strong>
              <small>引擎就绪</small>
            </div>
            <p className="kpi-desc">Qwen3-ASR (1.7B) · Whisper large</p>
          </div>
        </article>

        <article
          className="management-card-kpi clickable"
          onClick={() => onPage("models")}
          title="点击配置大模型服务商"
        >
          <div className="kpi-header">
            <span className="kpi-icon">
              <IconCpu size={16} />
            </span>
            <span
              className={`kpi-status-badge ${
                data.llm.profiles.length > 0 ? "ready" : "idle"
              }`}
            >
              {data.llm.profiles.length > 0 ? "已接入" : "未配置"}
            </span>
          </div>
          <div className="kpi-body">
            <span className="kpi-label">语言模型 (LLM) 服务</span>
            <div className="kpi-metric">
              <strong>{data.llm.profiles.length}</strong>
              <small>个服务商</small>
            </div>
            <p className="kpi-desc">LM Studio · Ollama · 本地/云端提供商</p>
          </div>
        </article>

        <article
          className="management-card-kpi clickable"
          onClick={() => onPage("tasks")}
          title="点击查看任务队列与进度"
        >
          <div className="kpi-header">
            <span className="kpi-icon">
              <IconClock size={16} />
            </span>
            <span
              className={`kpi-status-badge ${
                pendingJobs > 0 ? "active" : "idle"
              }`}
            >
              {pendingJobs > 0 ? "● 排队处理中" : "○ 队列空闲"}
            </span>
          </div>
          <div className="kpi-body">
            <span className="kpi-label">后台作业流水线</span>
            <div className="kpi-metric">
              <strong>{pendingJobs}</strong>
              <small>个待完成</small>
            </div>
            <p className="kpi-desc">音频转写、AI 纪要与分析作业排队</p>
          </div>
        </article>
      </div>

      {/* 系统运行异常警报条 */}
      {hasIssues && (
        <section className="management-alert-box" role="alert">
          <div className="alert-box-header">
            <span className="alert-box-icon">
              <IconAlertTriangle size={16} />
            </span>
            <div className="alert-box-content">
              <h4>检测到运行环境存在需要注意的事项</h4>
              {data.issues.map((issue) => (
                <p key={issue}>{issue}</p>
              ))}
              {!data.storage.writable && (
                <p>数据存储目录不可写，请检查本地目录访问权限。</p>
              )}
              {!data.dependencies.ffmpeg && (
                <p>系统缺少 FFmpeg 依赖，音频转码和重叠处理能力受限。</p>
              )}
            </div>
          </div>
          <button
            type="button"
            className="alert-box-btn"
            onClick={() => onPage("environment")}
          >
            检查并配置环境 ➔
          </button>
        </section>
      )}

      {/* 识别控制与守护状态 */}
      <CaptureControlPanel />

      {/* 实时识别会话流 */}
      <section className="management-section">
        <div className="section-title-row">
          <div>
            <h3>
              <IconMic size={15} /> 实时识别流会话
            </h3>
            <p className="section-desc">当前连接到本地守护进程的实时转写会话</p>
          </div>
        </div>
        {!data.sessions.length ? (
          <div className="management-empty-state">
            <span className="empty-icon">
              <IconCoffee size={22} />
            </span>
            <p>当前暂无活跃的实时转写会话</p>
            <small>在录音工作台开始录音后，此处将呈现分轨与确认进度</small>
          </div>
        ) : (
          <div className="management-session-list">
            {data.sessions.map((s) => (
              <div className="management-session-item" key={s.recording_id}>
                <div className="session-left">
                  <span className="session-engine-tag">
                    {s.engine === "qwen" ? "Qwen-ASR" : "Whisper"}
                  </span>
                  <span
                    className={`session-status-pill ${
                      s.status === "paused"
                        ? "paused"
                        : s.status === "draining"
                          ? "draining"
                          : "recording"
                    }`}
                  >
                    {s.status === "paused"
                      ? "已暂停"
                      : s.status === "draining"
                        ? "收尾中"
                        : "正在识别"}
                  </span>
                  <span className="session-clock">
                    已确认至 {clock(s.confirmed_until)}
                  </span>
                </div>
                <div className="session-right">
                  <code
                    className="session-id-pill"
                    title={`会话 ID: ${s.recording_id}`}
                  >
                    #{s.recording_id.slice(0, 8)}
                  </code>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* 后台批处理任务 */}
      <section className="management-section">
        <div className="section-title-row">
          <div>
            <h3>
              <IconSettings size={15} /> 后台批处理作业
            </h3>
            <p className="section-desc">
              离线转写、AI 纪要提炼与向量索引任务流
            </p>
          </div>
          {data.jobs.length > 0 && (
            <button
              type="button"
              className="section-action-btn"
              onClick={() => onPage("tasks")}
            >
              进入任务管理中心 ➔
            </button>
          )}
        </div>
        {!data.jobs.length ? (
          <div className="management-empty-state">
            <span className="empty-icon">
              <IconCheck size={20} />
            </span>
            <p>暂无待处理或运行中的后台任务</p>
            <small>所有会议的转写与 AI 总结均已完成处理</small>
          </div>
        ) : (
          <div className="management-job-list">
            {data.jobs.map((j) => {
              const kindLabel =
                (
                  {
                    transcription: "音频转写",
                    analysis: "会议分析",
                    minutes: "AI 会议纪要",
                    retrieval_index: "语义索引",
                    retrieval_answer: "引用问答",
                    terminology: "术语提取",
                    note_expansion: "简记整理",
                  } as Record<string, string>
                )[j.kind] || "后台作业";

              const pct = Math.round((j.progress || 0) * 100);

              return (
                <div className="management-job-item" key={j.id}>
                  <div className="job-info-row">
                    <span className="job-kind-badge">{kindLabel}</span>
                    <strong className="job-stage-text">
                      {j.state === "queued"
                        ? "排队等待中…"
                        : j.stage || "正在处理…"}
                    </strong>
                    <span className="job-pct">{pct}%</span>
                  </div>
                  <div className="job-progress-track">
                    <div
                      className={`job-progress-fill ${j.state === "queued" ? "queued" : "active"}`}
                      style={{ width: `${Math.max(5, pct)}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
