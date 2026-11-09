import { browserCaptureAllowed } from "../../preferences/nativeHost";
import RecordingReadiness from "../onboarding/RecordingReadiness";
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
  | "acceptReadiness"
  | "onModels"
  | "termsOpen"
  | "setTermsOpen"
  | "runningSessions"
  | "attachRunning"
> & {
  readinessOpen: boolean;
  setReadinessOpen: (v: boolean) => void;
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
  readinessOpen,
  setReadinessOpen,
  acceptReadiness,
  onModels,
  setTermsOpen,
  runningSessions = [],
  attachRunning,
  onStart,
  onClose,
}: RecordingSetupViewProps) {
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
      <div className="setup-topbar">
        <div className="setup-topbar-left">
          <button
            type="button"
            className="ghost-back-btn"
            onClick={onClose}
            title="保存草稿并返回"
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
      </div>

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

      <div className="setup-body">
        <div className="setup-grid">
          {/* 左栏：会议背景与核心议程 */}
          <div className="setup-card setup-card-main">
            <div className="setup-card-header">
              <h3>📝 会议背景与讨论诉求</h3>
              <p>提前填写会议主题与讨论重点，帮助会中抓取关键决策及会后生成精准纪要。</p>
            </div>

            <div className="setup-field">
              <label htmlFor="setup-title">会议主题</label>
              <input
                id="setup-title"
                className="setup-title-input"
                value={brief.title}
                maxLength={200}
                onChange={(e) => setBrief({ ...brief, title: e.target.value })}
                placeholder="输入会议主题（如：2026 Q4 产品规划周会、技术方案评审...）"
                autoFocus
              />
            </div>

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

            <div className="setup-field">
              <label htmlFor="setup-purpose">本次希望达成什么 / 核心目标</label>
              <textarea
                id="setup-purpose"
                className="setup-textarea"
                rows={2}
                value={brief.purpose}
                maxLength={1000}
                onChange={(e) => setBrief({ ...brief, purpose: e.target.value })}
                placeholder="填写本场会议的目标、期望结论或成果..."
              />
            </div>

            <div className="setup-field">
              <label htmlFor="setup-agenda">议程与讨论背景</label>
              <textarea
                id="setup-agenda"
                className="setup-textarea"
                rows={3}
                value={brief.agenda}
                maxLength={3000}
                onChange={(e) => setBrief({ ...brief, agenda: e.target.value })}
                placeholder="可粘贴会议议程要点、讨论背景或材料提纲..."
              />
            </div>

            <div className="setup-field">
              <label htmlFor="setup-questions">希望确认的问题</label>
              <textarea
                id="setup-questions"
                className="setup-textarea"
                rows={2}
                value={brief.questions}
                maxLength={2000}
                onChange={(e) =>
                  setBrief({ ...brief, questions: e.target.value })
                }
                placeholder="需在会上重点讨论并拍板的问题清单..."
              />
            </div>

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
                关联后将自动带入上场会议未完成的跟进项与上下文。
              </small>
            </div>

            <details className="setup-details">
              <summary>
                💼 面试场景准备（可选）
                {brief.interview?.role ? ` · ${brief.interview.role}` : ""}
              </summary>
              <div className="setup-details-content">
                <div className="setup-field">
                  <label htmlFor="setup-candidate">候选人姓名／代称</label>
                  <input
                    id="setup-candidate"
                    className="setup-input"
                    value={brief.interview?.candidate || ""}
                    maxLength={200}
                    onChange={(e) =>
                      setBrief({
                        ...brief,
                        interview: {
                          role: "",
                          round: "",
                          interviewers: "",
                          focus: "",
                          questions: "",
                          job_description: "",
                          ...brief.interview,
                          candidate: e.target.value,
                        },
                      })
                    }
                    placeholder="候选人称呼"
                  />
                </div>
                <div className="setup-field">
                  <label htmlFor="setup-role">应聘岗位</label>
                  <input
                    id="setup-role"
                    className="setup-input"
                    value={brief.interview?.role || ""}
                    maxLength={300}
                    onChange={(e) =>
                      setBrief({
                        ...brief,
                        interview: {
                          candidate: "",
                          round: "",
                          interviewers: "",
                          focus: "",
                          questions: "",
                          job_description: "",
                          ...brief.interview,
                          role: e.target.value,
                        },
                      })
                    }
                    placeholder="如：资深前端工程师"
                  />
                </div>
                <div className="setup-field">
                  <label htmlFor="setup-focus">本轮重点考察</label>
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
                    placeholder="如：架构设计能力、实际业务产出、团队协同..."
                  />
                </div>
              </div>
            </details>
          </div>

          {/* 右栏：收音音源与 AI 交付方案 */}
          <div className="setup-sidebar">
            {/* 卡片 1: 会议场景与收音音源 */}
            <div className="setup-card">
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

              {/* 实时试音电平 */}
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
              </div>
            </div>

            {/* 卡片 2: 语言、识别与会后交付 */}
            <div className="setup-card">
              <div className="setup-card-header">
                <h3>🤖 识别与会后交付</h3>
                <p>配置实时识别引擎与会后产物策略。</p>
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
            </div>

            {/* 卡片 3: 辅助与就绪检查 */}
            <div className="setup-card">
              <div className="setup-card-header">
                <h3>🛠️ 辅助与就绪检查</h3>
              </div>
              <div className="setup-readiness-summary">
                <div className="setup-check-item">
                  <span className="check-icon">
                    {nativeCapability?.available ? "✓" : "⚠️"}
                  </span>
                  <span>
                    {nativeCapability?.available
                      ? "本机系统采集服务就绪"
                      : "需系统声音或麦克风授权"}
                  </span>
                </div>
                <div className="setup-check-item">
                  <span className="check-icon">
                    {preflight?.storage.writable !== false ? "✓" : "⚠️"}
                  </span>
                  <span>
                    {preflight?.storage.writable !== false
                      ? "本地磁盘写入空间正常"
                      : "本地磁盘暂不可写"}
                  </span>
                </div>
              </div>

              <div className="setup-actions-row">
                <button
                  type="button"
                  className="setup-sub-btn"
                  onClick={() => setTermsOpen(true)}
                >
                  📖 个人术语库
                </button>
                <button
                  type="button"
                  className="setup-sub-btn"
                  onClick={() => setReadinessOpen(!readinessOpen)}
                >
                  🔍 环境自检 {readinessOpen ? "▲" : "▼"}
                </button>
              </div>

              {readinessOpen && (
                <div className="setup-readiness-details">
                  <RecordingReadiness
                    nativeCapability={nativeCapability}
                    source={captureSource}
                    engine={draftEngine}
                    onReadiness={acceptReadiness}
                    onConfigure={() => {}}
                    onModels={onModels}
                  />
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
