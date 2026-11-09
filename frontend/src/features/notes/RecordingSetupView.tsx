import { useState } from "react";
import { browserCaptureAllowed } from "../../preferences/nativeHost";
import { clock } from "../../utils";
import type { RecordingViewModel } from "./useRecordingController";

export type RecordingSetupViewProps = Pick<
  RecordingViewModel,
  | "brief"
  | "setBrief"
  | "briefSaved"
  | "briefReady"
  | "meetings"
  | "captureSource"
  | "setCaptureSource"
  | "nativeCapability"
  | "draftEngine"
  | "setDraftEngine"
  | "engineName"
  | "postGoal"
  | "setPostGoal"
  | "completionMode"
  | "setCompletionMode"
  | "autoProcess"
  | "setAutoProcess"
  | "preflight"
  | "engines"
  | "microphoneLevels"
  | "systemLevels"
  | "onModels"
  | "termsOpen"
  | "setTermsOpen"
  | "recognitionTerms"
  | "runningSessions"
  | "attachRunning"
> & {
  onStart: () => void;
  onClose: () => void;
};

export default function RecordingSetupView({
  brief,
  setBrief,
  briefSaved,
  briefReady,
  meetings,
  captureSource,
  setCaptureSource,
  nativeCapability,
  draftEngine,
  setDraftEngine,
  postGoal,
  setPostGoal,
  completionMode,
  setCompletionMode,
  autoProcess,
  setAutoProcess,
  preflight,
  microphoneLevels,
  systemLevels,
  onModels,
  setTermsOpen,
  recognitionTerms = "",
  runningSessions = [],
  attachRunning,
  onStart,
  onClose,
}: RecordingSetupViewProps) {
  const [meetingMode, setMeetingMode] = useState<"general" | "interview">(() =>
    brief.interview && (brief.interview.candidate || brief.interview.role)
      ? "interview"
      : "general",
  );

  const startDisabled =
    !briefReady ||
    preflight?.storage.writable === false ||
    (captureSource === "browser" &&
      (!browserCaptureAllowed() ||
        typeof MediaRecorder === "undefined" ||
        !navigator.mediaDevices?.getUserMedia)) ||
    (captureSource !== "browser" &&
      (!nativeCapability?.available ||
        (captureSource === "macos-microphone" &&
          !nativeCapability.microphone_only_supported) ||
        (!!nativeCapability?.ownership &&
          nativeCapability.ownership.lease !== "free")));

  const tracks = [
    ...(captureSource !== "browser" && captureSource !== "macos-microphone"
      ? [{ label: "电脑声音", levels: systemLevels }]
      : []),
    ...(captureSource !== "macos-system"
      ? [{ label: "麦克风", levels: microphoneLevels }]
      : []),
  ];

  return (
    <div className="recording-setup-view">
      {/* 顶部主操作栏 */}
      <header className="setup-topbar">
        <div className="setup-topbar-left">
          <button
            type="button"
            className="ghost-back-btn"
            onClick={onClose}
            title="自动保存备会草稿并返回"
          >
            ← 返回
          </button>
          <div className="setup-title-group">
            <h2>会议准备与预设</h2>
            <span className="setup-status-pill">
              {briefSaved ? "✓ 备会内容已保存到本机" : "正在保存草稿…"}
            </span>
          </div>
        </div>
        <div className="setup-topbar-right">
          <button
            type="button"
            className="setup-start-btn"
            disabled={startDisabled}
            onClick={onStart}
          >
            ● 开始实时录音
          </button>
        </div>
      </header>

      {/* 恢复进行中的录音通知 */}
      {runningSessions.length > 0 && attachRunning && (
        <div
          className="live-session-resume"
          role="status"
          style={{ margin: "16px 32px 0" }}
        >
          <span>
            本机原生录音仍在运行；继续查看时会核对控制权，必要时请明确恢复控制。
          </span>
          {runningSessions.map((entry) => (
            <button
              key={entry.recording.id}
              onClick={() => void attachRunning(entry)}
            >
              继续查看 {clock(entry.recording.duration)}
            </button>
          ))}
        </div>
      )}

      {/* 主体双栏工作台 */}
      <main className="setup-body">
        <div className="setup-grid">
          {/* 左栏：会议背景与核心议程画布 */}
          <section className="setup-main-card">
            <div className="setup-card-header">
              <div className="setup-card-header-row">
                <div>
                  <h3>📝 会议背景与讨论诉求</h3>
                  <p>提前填写会议主题与讨论重点，帮助会中抓取关键决策及会后生成精准纪要。</p>
                </div>
                {/* 场景模式选择 */}
                <div className="setup-mode-toggle" role="radiogroup" aria-label="会议场景类型">
                  <button
                    type="button"
                    className={`setup-mode-btn ${meetingMode === "general" ? "active" : ""}`}
                    onClick={() => setMeetingMode("general")}
                  >
                    📌 常规业务会议
                  </button>
                  <button
                    type="button"
                    className={`setup-mode-btn ${meetingMode === "interview" ? "active" : ""}`}
                    onClick={() => {
                      setMeetingMode("interview");
                      if (!brief.interview) {
                        setBrief({
                          ...brief,
                          interview: {
                            candidate: "",
                            role: "",
                            round: "",
                            interviewers: "",
                            questions: "",
                            job_description: "",
                            focus: "",
                          },
                        });
                      }
                    }}
                  >
                    💼 招聘面试考察
                  </button>
                </div>
              </div>
            </div>

            {/* 会议主题 */}
            <div className="setup-field">
              <label htmlFor="setup-title">会议主题</label>
              <input
                id="setup-title"
                className="setup-title-input"
                value={brief.title}
                maxLength={200}
                onChange={(e) => setBrief({ ...brief, title: e.target.value })}
                placeholder="输入会议主题（如：2026 Q4 产品规划评审、业务架构方案交流...）"
                autoFocus
              />
            </div>

            {/* 面试专属字段：在选择面试模式时直接展开 */}
            {meetingMode === "interview" && (
              <div className="setup-interview-panel">
                <div className="setup-interview-banner">
                  💼 招聘面试专属预设 · 系统将自动按面试官问题、候选人回答与能力维度整理纪要
                </div>
                <div className="setup-form-row">
                  <div className="setup-field flex-1">
                    <label htmlFor="setup-candidate">候选人姓名</label>
                    <input
                      id="setup-candidate"
                      className="setup-input"
                      value={brief.interview?.candidate || ""}
                      maxLength={100}
                      onChange={(e) =>
                        setBrief({
                          ...brief,
                          interview: {
                            role: "",
                            round: "",
                            interviewers: "",
                            questions: "",
                            job_description: "",
                            focus: "",
                            ...brief.interview,
                            candidate: e.target.value,
                          },
                        })
                      }
                      placeholder="候选人姓名"
                    />
                  </div>
                  <div className="setup-field flex-1">
                    <label htmlFor="setup-job-role">应聘岗位</label>
                    <input
                      id="setup-job-role"
                      className="setup-input"
                      value={brief.interview?.role || ""}
                      maxLength={100}
                      onChange={(e) =>
                        setBrief({
                          ...brief,
                          interview: {
                            candidate: "",
                            round: "",
                            interviewers: "",
                            questions: "",
                            job_description: "",
                            focus: "",
                            ...brief.interview,
                            role: e.target.value,
                          },
                        })
                      }
                      placeholder="如：资深前端工程师"
                    />
                  </div>
                </div>
                <div className="setup-field">
                  <label htmlFor="setup-focus">本轮重点考察能力与要求</label>
                  <textarea
                    id="setup-focus"
                    className="setup-textarea"
                    rows={2}
                    value={brief.interview?.focus || ""}
                    maxLength={1000}
                    onChange={(e) =>
                      setBrief({
                        ...brief,
                        interview: {
                          candidate: "",
                          role: "",
                          round: "",
                          interviewers: "",
                          questions: "",
                          job_description: "",
                          ...brief.interview,
                          focus: e.target.value,
                        },
                      })
                    }
                    placeholder="如：技术深度、项目架构设计能力、实际业务产出及跨团队协作..."
                  />
                </div>
              </div>
            )}

            {/* 计划参会人 */}
            <div className="setup-field">
              <label htmlFor="setup-participants">计划参会人</label>
              <input
                id="setup-participants"
                className="setup-input"
                value={brief.participants}
                maxLength={1000}
                onChange={(e) =>
                  setBrief({ ...brief, participants: e.target.value })
                }
                placeholder="参会人姓名，用逗号或空格隔开（辅助后续发言人识别与指代）"
              />
            </div>

            {/* 核心目标 / 预期结论 */}
            <div className="setup-field">
              <label htmlFor="setup-purpose">本次希望达成什么 / 核心目标</label>
              <textarea
                id="setup-purpose"
                className="setup-textarea"
                rows={2}
                value={brief.purpose}
                maxLength={1000}
                onChange={(e) => setBrief({ ...brief, purpose: e.target.value })}
                placeholder="填写本场会议的目标、拍板事项或预期交付结论..."
              />
            </div>

            {/* 议程要点 */}
            <div className="setup-field">
              <label htmlFor="setup-agenda">议程要点与背景材料</label>
              <textarea
                id="setup-agenda"
                className="setup-textarea"
                rows={3}
                value={brief.agenda}
                maxLength={3000}
                onChange={(e) => setBrief({ ...brief, agenda: e.target.value })}
                placeholder="可粘贴会议议程、讨论提纲或背景材料..."
              />
            </div>

            {/* 关注问题 */}
            <div className="setup-field">
              <label htmlFor="setup-questions">需拍板或重点关注的问题</label>
              <textarea
                id="setup-questions"
                className="setup-textarea"
                rows={2}
                value={brief.questions}
                maxLength={2000}
                onChange={(e) =>
                  setBrief({ ...brief, questions: e.target.value })
                }
                placeholder="列出会上需要拍板、确认方案或追问的问题清单..."
              />
            </div>

            {/* 关联上一场会议 */}
            <div className="setup-field">
              <label htmlFor="setup-prev-meeting">关联上一场会议（可选）</label>
              <select
                id="setup-prev-meeting"
                className="setup-select"
                value={brief.previous_meeting_id || ""}
                onChange={(e) =>
                  setBrief({
                    ...brief,
                    previous_meeting_id: e.target.value || null,
                  })
                }
              >
                <option value="">不关联（独立新会议）</option>
                {meetings.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.title} ({m.created.slice(0, 10)})
                  </option>
                ))}
              </select>
              <small className="setup-hint">
                关联后将自动带入上一场会议的待办事项与未决问题，建立上下文闭环。
              </small>
            </div>
          </section>

          {/* 右栏：采集控制舱与交付策略 */}
          <aside className="setup-sidebar">
            {/* 卡片 1: 会议场景与收音音源 */}
            <section className="setup-card">
              <div className="setup-card-header">
                <h3>🎙️ 录音音源与试音</h3>
                <p>根据参会形式选择最适合的收音方案。</p>
              </div>

              <div className="setup-source-picker">
                <label
                  className={`setup-source-option ${captureSource === "macos-both" ? "selected" : ""}`}
                >
                  <input
                    type="radio"
                    name="captureSource"
                    value="macos-both"
                    checked={captureSource === "macos-both"}
                    disabled={!nativeCapability?.available}
                    onChange={() => setCaptureSource("macos-both")}
                  />
                  <div className="setup-source-text">
                    <strong>混合会议</strong>
                    <small>电脑声音 + 麦克风（线上开会＋本机发言）</small>
                  </div>
                </label>

                <label
                  className={`setup-source-option ${captureSource === "macos-microphone" ? "selected" : ""}`}
                >
                  <input
                    type="radio"
                    name="captureSource"
                    value="macos-microphone"
                    checked={captureSource === "macos-microphone"}
                    disabled={
                      !nativeCapability?.available ||
                      !nativeCapability.microphone_only_supported
                    }
                    onChange={() => setCaptureSource("macos-microphone")}
                  />
                  <div className="setup-source-text">
                    <strong>现场会议</strong>
                    <small>仅麦克风（线下会议室现场面对面交流）</small>
                  </div>
                </label>

                <label
                  className={`setup-source-option ${captureSource === "macos-system" ? "selected" : ""}`}
                >
                  <input
                    type="radio"
                    name="captureSource"
                    value="macos-system"
                    checked={captureSource === "macos-system"}
                    disabled={!nativeCapability?.available}
                    onChange={() => setCaptureSource("macos-system")}
                  />
                  <div className="setup-source-text">
                    <strong>线上会议</strong>
                    <small>仅电脑声音（旁听会议、培训或讲座）</small>
                  </div>
                </label>

                {browserCaptureAllowed() && (
                  <label
                    className={`setup-source-option ${captureSource === "browser" ? "selected" : ""}`}
                  >
                    <input
                      type="radio"
                      name="captureSource"
                      value="browser"
                      checked={captureSource === "browser"}
                      onChange={() => setCaptureSource("browser")}
                    />
                    <div className="setup-source-text">
                      <strong>浏览器麦克风</strong>
                      <small>免系统采集插件（兼容模式）</small>
                    </div>
                  </label>
                )}
              </div>

              {/* 实时试音电平跳动条 */}
              <div className="setup-meters-box">
                <div className="setup-meter-title">🔊 实时音量试音</div>
                {tracks.map((track) => (
                  <div className="setup-meter-row" key={track.label}>
                    <span>{track.label}</span>
                    <meter
                      aria-label={`${track.label}试音音量`}
                      min={0}
                      max={1}
                      value={Math.min(
                        1,
                        Math.sqrt(Math.max(0, track.levels.at(-1) || 0)) * 2.4,
                      )}
                    />
                  </div>
                ))}
                <small className="setup-meters-hint">
                  说话或播放声音时观察条带跳动，确认收音正常。
                </small>
              </div>
            </section>

            {/* 卡片 2: 语言、识别引擎与会后交付 */}
            <section className="setup-card">
              <div className="setup-card-header">
                <h3>🤖 识别与会后交付</h3>
                <p>配置识别引擎、专有词库与整理策略。</p>
              </div>

              <div className="setup-field">
                <label htmlFor="setup-source-lang">原文语言</label>
                <select
                  id="setup-source-lang"
                  className="setup-select"
                  value={brief.source_language || "auto"}
                  onChange={(e) =>
                    setBrief({
                      ...brief,
                      source_language: e.target.value as typeof brief.source_language,
                    })
                  }
                >
                  <option value="auto">自动识别（中英）</option>
                  <option value="zh">中文</option>
                  <option value="en">英文</option>
                  <option value="ja">日语</option>
                </select>
              </div>

              <div className="setup-field">
                <label htmlFor="setup-engine">识别引擎</label>
                <select
                  id="setup-engine"
                  className="setup-select"
                  value={draftEngine}
                  onChange={(e) =>
                    setDraftEngine(e.target.value as typeof draftEngine)
                  }
                >
                  <option value="qwen">千问 Qwen-ASR-1.7B</option>
                  <option value="whisper">Whisper large-v3-turbo</option>
                </select>
              </div>

              {/* 个人术语库：专有名词与词库增强 */}
              <div className="setup-terms-card">
                <div className="setup-terms-info">
                  <span className="setup-terms-icon">📖</span>
                  <div className="setup-terms-text">
                    <strong>个人术语库</strong>
                    <small>
                      {recognitionTerms.trim()
                        ? "已配置专用词条，辅助专有名词与人名精准识别"
                        : "针对行业术语、人名或代码缩写补充专有名词"}
                    </small>
                  </div>
                </div>
                <button
                  type="button"
                  className="setup-terms-btn"
                  onClick={() => setTermsOpen(true)}
                >
                  管理词库
                </button>
              </div>

              <div className="setup-field">
                <label htmlFor="setup-output-lang">纪要输出语言</label>
                <select
                  id="setup-output-lang"
                  className="setup-select"
                  value={brief.output_language || "zh"}
                  onChange={(e) =>
                    setBrief({
                      ...brief,
                      output_language: e.target.value as typeof brief.output_language,
                    })
                  }
                >
                  <option value="zh">中文</option>
                  <option value="en">英文</option>
                </select>
              </div>

              <div className="setup-field">
                <label>会后整理目标</label>
                <div className="setup-pill-group">
                  <button
                    type="button"
                    className={`setup-pill ${postGoal === "minutes" && completionMode !== "none" ? "active" : ""}`}
                    onClick={() => {
                      setPostGoal("minutes");
                      setCompletionMode("quick");
                    }}
                  >
                    文字稿 ＋ AI 纪要
                  </button>
                  <button
                    type="button"
                    className={`setup-pill ${postGoal === "transcript" && completionMode !== "none" ? "active" : ""}`}
                    onClick={() => {
                      setPostGoal("transcript");
                      setCompletionMode("full");
                    }}
                  >
                    仅生成文字稿
                  </button>
                  <button
                    type="button"
                    className={`setup-pill ${completionMode === "none" ? "active" : ""}`}
                    onClick={() => {
                      setCompletionMode("none");
                    }}
                  >
                    仅保存录音
                  </button>
                </div>
              </div>

              <label className="setup-checkbox-row">
                <input
                  type="checkbox"
                  checked={autoProcess}
                  onChange={(e) => setAutoProcess(e.target.checked)}
                />
                <span>结束后自动进入会后整理</span>
              </label>
            </section>
          </aside>
        </div>
      </main>

      {/* 底部轻量就绪状态条：给用户充分安全感，不再占卡片空间 */}
      <footer className="setup-footer-bar">
        <div className="setup-footer-status">
          <span className="setup-footer-badge">
            <span className="footer-dot-green" />
            {nativeCapability?.available ? "本机音频服务就绪" : "需麦克风/系统声音授权"}
          </span>
          <span className="setup-footer-badge">
            <span className="footer-dot-green" />
            实时识别已配置（{draftEngine === "qwen" ? "千问 Qwen-ASR" : "Whisper"}）
          </span>
          <span className="setup-footer-badge">
            <span className="footer-dot-green" />
            {preflight?.storage.writable !== false ? "本地存储空间充足" : "存储不可写"}
          </span>
          <span className="setup-footer-badge setup-privacy-badge">
            🔒 纯本地离线处理 · 无云端数据传输
          </span>
        </div>
        <div className="setup-footer-right">
          <button
            type="button"
            className="setup-mgmt-link"
            onClick={onModels}
            title="查看完整依赖、模型及环境自检"
          >
            系统与环境管理 ↗
          </button>
        </div>
      </footer>
    </div>
  );
}
