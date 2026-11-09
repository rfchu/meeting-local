import { useState, useEffect, useRef } from "react";
import { browserCaptureAllowed } from "../../preferences/nativeHost";
import { clock } from "../../utils";
import type { Environment } from "../../types";
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
  requesting?: boolean;
  onStart: () => void;
  onClose: () => void;
  onOpenSettings?: () => void;
  echoMode?: "standard" | "aec3";
  environment?: Environment;
  minutesModel?: string;
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
  requesting = false,
  onStart,
  onClose,
  onOpenSettings,
  echoMode = "standard",
  environment,
  minutesModel,
}: RecordingSetupViewProps) {
  const [meetingMode, setMeetingMode] = useState<"general" | "interview">(() =>
    brief.interview && (brief.interview.candidate || brief.interview.role)
      ? "interview"
      : "general",
  );

  const [agendaTab, setAgendaTab] = useState<"agenda" | "purpose" | "questions">(
    "agenda",
  );

  // 独立试音监听状态（Web Audio 驱动）
  const [testState, setTestState] = useState<"idle" | "testing" | "done">("idle");
  const [testLevel, setTestLevel] = useState(0);
  const [testCountdown, setTestCountdown] = useState(5);
  const testStreamRef = useRef<MediaStream | null>(null);
  const testCtxRef = useRef<AudioContext | null>(null);
  const testAnimRef = useRef<number | null>(null);
  const testTimerRef = useRef<number | null>(null);
  const peakLevelRef = useRef<number>(0);

  const stopMicTest = () => {
    if (testAnimRef.current) {
      cancelAnimationFrame(testAnimRef.current);
      testAnimRef.current = null;
    }
    if (testTimerRef.current) {
      window.clearInterval(testTimerRef.current);
      testTimerRef.current = null;
    }
    if (testStreamRef.current) {
      testStreamRef.current.getTracks().forEach((t) => t.stop());
      testStreamRef.current = null;
    }
    if (testCtxRef.current) {
      void testCtxRef.current.close().catch(() => {});
      testCtxRef.current = null;
    }
    setTestState("done");
    setTestLevel(0);
  };

  useEffect(() => {
    return () => {
      stopMicTest();
    };
  }, []);

  const startMicTest = async () => {
    stopMicTest();
    if (!navigator.mediaDevices?.getUserMedia) {
      alert("当前浏览器环境不支持麦克风采集");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      testStreamRef.current = stream;
      const AudioContextClass =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext: typeof AudioContext })
          .webkitAudioContext;
      const ctx = new AudioContextClass();
      testCtxRef.current = ctx;
      await ctx.resume();

      const sourceNode = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      analyser.smoothingTimeConstant = 0.3;
      sourceNode.connect(analyser);

      peakLevelRef.current = 0;
      setTestLevel(0);
      setTestCountdown(5);
      setTestState("testing");

      const dataArray = new Uint8Array(analyser.frequencyBinCount);
      const updateLevel = () => {
        analyser.getByteFrequencyData(dataArray);
        let sum = 0;
        for (let i = 0; i < dataArray.length; i++) {
          sum += dataArray[i];
        }
        const avg = sum / dataArray.length / 255;
        const currentLevel = Math.min(1, Math.sqrt(avg) * 1.8);
        setTestLevel(currentLevel);
        if (currentLevel > peakLevelRef.current) {
          peakLevelRef.current = currentLevel;
        }
        testAnimRef.current = requestAnimationFrame(updateLevel);
      };
      testAnimRef.current = requestAnimationFrame(updateLevel);

      let left = 5;
      testTimerRef.current = window.setInterval(() => {
        left -= 1;
        setTestCountdown(left);
        if (left <= 0) {
          stopMicTest();
        }
      }, 1000);
    } catch (err) {
      console.error("Mic test error:", err);
      alert("无法访问麦克风，请检查系统或浏览器麦克风权限。");
      setTestState("idle");
    }
  };

  const selectedMinutesProfile = environment?.analysis?.profiles?.find(
    (p) => p.id === minutesModel,
  );
  const isCloudMinutes =
    postGoal === "minutes" &&
    selectedMinutesProfile?.provider_type === "cloud";

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

  // 支持键盘快捷键 ⌘ + Enter / Ctrl + Enter 极速起录
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
        if (!startDisabled && !requesting) {
          event.preventDefault();
          onStart();
        }
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [startDisabled, requesting, onStart]);

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
            disabled={requesting}
            onClick={onClose}
            title={requesting ? "正在启动录音，暂不可返回" : "自动保存备会草稿并返回"}
          >
            ← 返回
          </button>
          <div className="setup-title-group">
            <h2>会前准备与就绪舱</h2>
            <span className="setup-status-pill">
              {requesting
                ? "正在启动录音通道…"
                : briefSaved
                  ? "✓ 备会内容已保存到本机"
                  : "正在保存草稿…"}
            </span>
          </div>
        </div>
        <div className="setup-topbar-right">
          <button
            type="button"
            className={`setup-start-btn ${requesting ? "loading" : ""}`}
            disabled={startDisabled || requesting}
            onClick={onStart}
            title={requesting ? "正在请求权限并启动录音…" : "快捷键: ⌘ + Enter"}
          >
            <span className={`setup-record-dot ${requesting ? "loading" : ""}`} />
            {requesting ? "正在启动录音…" : "开始实时录音"}
            {!requesting && <kbd className="setup-kbd-hint">⌘↵</kbd>}
          </button>
        </div>
      </header>

      {/* 恢复进行中的录音通知 */}
      {runningSessions.length > 0 && attachRunning && (
        <div
          className="live-session-resume"
          role="status"
          style={{ margin: "14px 32px 0" }}
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

      {/* 主体双栏工作台：杜绝使用全局污染的 aside / main 语义化标签 */}
      <div className="setup-body">
        <div className="setup-grid">
          {/* 左翼：会议背景与讨论诉求画布 */}
          <div className="setup-card setup-main-card">
            <div className="setup-card-header">
              <div className="setup-card-header-row">
                <div>
                  <div className="setup-eyebrow-tag">PRE-MEETING CONTEXT · 会议预设</div>
                  <h3>📝 会议背景与讨论诉求</h3>
                  <p>
                    提前填写会议主题与讨论重点，帮助会中抓取关键决策及会后生成精准纪要。
                  </p>
                </div>
                {/* 场景模式选择 */}
                <div
                  className="setup-mode-toggle"
                  role="radiogroup"
                  aria-label="会议场景类型"
                >
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
              <label htmlFor="setup-title">
                会议主题 <span className="setup-required-star">*</span>
              </label>
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

            {meetingMode === "general" ? (
              <>
                {/* 计划参会人 */}
                <div className="setup-field">
                  <label htmlFor="setup-participants">计划参会人（可选）</label>
                  <input
                    id="setup-participants"
                    className="setup-input"
                    value={brief.participants}
                    maxLength={1000}
                    onChange={(e) =>
                      setBrief({ ...brief, participants: e.target.value })
                    }
                    placeholder="参会人姓名，用逗号或空格隔开（辅助会中发言人识别与指代）"
                  />
                </div>

                {/* 讨论背景与提纲备忘：标签页式敏捷编辑器 */}
                <div className="setup-agenda-section">
                  <div className="setup-agenda-header">
                    <label>讨论背景与提纲备忘（可选）</label>
                    <div className="setup-agenda-tabs" role="tablist">
                      <button
                        type="button"
                        role="tab"
                        aria-selected={agendaTab === "agenda"}
                        className={`setup-agenda-tab-btn ${agendaTab === "agenda" ? "active" : ""}`}
                        onClick={() => setAgendaTab("agenda")}
                      >
                        📋 议程与背景材料
                        {brief.agenda?.trim() ? (
                          <span className="tab-has-content-dot" title="已填写内容" />
                        ) : null}
                      </button>
                      <button
                        type="button"
                        role="tab"
                        aria-selected={agendaTab === "purpose"}
                        className={`setup-agenda-tab-btn ${agendaTab === "purpose" ? "active" : ""}`}
                        onClick={() => setAgendaTab("purpose")}
                      >
                        🎯 预期目标与结论
                        {brief.purpose?.trim() ? (
                          <span className="tab-has-content-dot" title="已填写内容" />
                        ) : null}
                      </button>
                      <button
                        type="button"
                        role="tab"
                        aria-selected={agendaTab === "questions"}
                        className={`setup-agenda-tab-btn ${agendaTab === "questions" ? "active" : ""}`}
                        onClick={() => setAgendaTab("questions")}
                      >
                        ❓ 需拍板关注问题
                        {brief.questions?.trim() ? (
                          <span className="tab-has-content-dot" title="已填写内容" />
                        ) : null}
                      </button>
                    </div>
                  </div>

                  <div className="setup-agenda-tab-panel">
                    {agendaTab === "agenda" && (
                      <textarea
                        id="setup-agenda"
                        className="setup-textarea agenda-tab-textarea"
                        rows={4}
                        value={brief.agenda}
                        maxLength={3000}
                        onChange={(e) =>
                          setBrief({ ...brief, agenda: e.target.value })
                        }
                        placeholder="可粘贴会议议程、讨论提纲或背景材料，帮助 AI 精准分段与归纳..."
                      />
                    )}
                    {agendaTab === "purpose" && (
                      <textarea
                        id="setup-purpose"
                        className="setup-textarea agenda-tab-textarea"
                        rows={4}
                        value={brief.purpose}
                        maxLength={1000}
                        onChange={(e) =>
                          setBrief({ ...brief, purpose: e.target.value })
                        }
                        placeholder="填写本场会议希望达成的核心目标、预期交付结论或拍板事项..."
                      />
                    )}
                    {agendaTab === "questions" && (
                      <textarea
                        id="setup-questions"
                        className="setup-textarea agenda-tab-textarea"
                        rows={4}
                        value={brief.questions}
                        maxLength={2000}
                        onChange={(e) =>
                          setBrief({ ...brief, questions: e.target.value })
                        }
                        placeholder="列出会上需确认或解答的关键问题清单，AI 将在生成纪要时重点核对..."
                      />
                    )}
                  </div>
                </div>
              </>
            ) : (
              /* 面试专属字段：在选择面试模式时展开 */
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
                    rows={3}
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

            {/* 关联上一场会议 */}
            <div className="setup-field setup-prev-meeting-field">
              <label htmlFor="setup-prev-meeting">🔗 关联上下文（可选）</label>
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
          </div>

          {/* 右翼：采集控制舱与交付策略 */}
          <div className="setup-sidebar-card">
            {/* 卡片 1: 会议场景与收音音源 (2x2 紧凑矩阵 + 实时拟真电平) */}
            <div className="setup-card">
              <div className="setup-card-header">
                <div className="setup-card-title-row">
                  <span className="setup-card-icon">🎙️</span>
                  <div>
                    <h3>录音音源与即时试音</h3>
                    <p>选择参会形式，说话或播放音频即可确认音量正常。</p>
                  </div>
                </div>
              </div>

              {/* 2x2 紧凑收音卡片矩阵 */}
              <div className="setup-source-grid" role="radiogroup" aria-label="收音方案">
                <button
                  type="button"
                  className={`setup-source-card ${captureSource === "macos-both" ? "selected" : ""}`}
                  disabled={!nativeCapability?.available}
                  onClick={() => setCaptureSource("macos-both")}
                >
                  <div className="setup-source-card-top">
                    <span className="setup-source-icon">🎧</span>
                    <strong>混合会议</strong>
                    {captureSource === "macos-both" && (
                      <span className="source-check-badge">✓</span>
                    )}
                  </div>
                  <span className="setup-source-desc">电脑声音 ＋ 麦克风</span>
                  <small className="setup-source-sub">腾讯会议 / 飞书 / Zoom</small>
                </button>

                <button
                  type="button"
                  className={`setup-source-card ${captureSource === "macos-microphone" ? "selected" : ""}`}
                  disabled={
                    !nativeCapability?.available ||
                    !nativeCapability.microphone_only_supported
                  }
                  onClick={() => setCaptureSource("macos-microphone")}
                >
                  <div className="setup-source-card-top">
                    <span className="setup-source-icon">🎙️</span>
                    <strong>现场会议</strong>
                    {captureSource === "macos-microphone" && (
                      <span className="source-check-badge">✓</span>
                    )}
                  </div>
                  <span className="setup-source-desc">仅麦克风输入</span>
                  <small className="setup-source-sub">线下会议室现场交流</small>
                </button>

                <button
                  type="button"
                  className={`setup-source-card ${captureSource === "macos-system" ? "selected" : ""}`}
                  disabled={!nativeCapability?.available}
                  onClick={() => setCaptureSource("macos-system")}
                >
                  <div className="setup-source-card-top">
                    <span className="setup-source-icon">💻</span>
                    <strong>线上旁听</strong>
                    {captureSource === "macos-system" && (
                      <span className="source-check-badge">✓</span>
                    )}
                  </div>
                  <span className="setup-source-desc">仅电脑声音</span>
                  <small className="setup-source-sub">线上培训 / 直播 / 讲座</small>
                </button>

                {browserCaptureAllowed() ? (
                  <button
                    type="button"
                    className={`setup-source-card ${captureSource === "browser" ? "selected" : ""}`}
                    onClick={() => setCaptureSource("browser")}
                  >
                    <div className="setup-source-card-top">
                      <span className="setup-source-icon">🌐</span>
                      <strong>浏览器麦克风</strong>
                      {captureSource === "browser" && (
                        <span className="source-check-badge">✓</span>
                      )}
                    </div>
                    <span className="setup-source-desc">免插件采集</span>
                    <small className="setup-source-sub">Web API 兼容模式</small>
                  </button>
                ) : (
                  <div className="setup-source-card disabled-placeholder">
                    <div className="setup-source-card-top">
                      <span className="setup-source-icon">🔒</span>
                      <strong>系统收音就绪</strong>
                    </div>
                    <span className="setup-source-desc">原生高保真离线采集</span>
                    <small className="setup-source-sub">低延迟 · 纯本地</small>
                  </div>
                )}
              </div>

              {/* 高级音频配置入口（双轨回声消除、硬件权限） */}
              <div className="setup-source-advanced-row">
                <span className="setup-source-advanced-info">
                  {captureSource === "macos-both"
                    ? `双轨回声消除：${echoMode === "aec3" ? "WebRTC AEC3 声学消回声" : "标准处理"}`
                    : "高级音频：硬件检查、静音提醒与回声设置"}
                </span>
                {onOpenSettings && (
                  <button
                    type="button"
                    className="btn-setup-advanced-audio"
                    onClick={onOpenSettings}
                    title="配置双轨回声消除、系统音频权限与静音提醒"
                  >
                    ⚙️ 高级音频与回声设置
                  </button>
                )}
              </div>

              {/* 实时电平反馈条 */}
              <div className="setup-meters-strip">
                <div className="setup-meter-header">
                  <div className="setup-meter-header-info">
                    <span>🔊 实时试音监听</span>
                    <small>
                      {testState === "testing"
                        ? "正在试音，请对着麦克风说话观察电平跳动…"
                        : "点击右侧按钮测试麦克风音量与收音状态"}
                    </small>
                  </div>
                  <div className="setup-meter-header-actions">
                    {testState === "idle" && (
                      <button
                        type="button"
                        className="btn-mic-test"
                        onClick={() => void startMicTest()}
                      >
                        🎙️ 开始试音 (5秒)
                      </button>
                    )}
                    {testState === "testing" && (
                      <button
                        type="button"
                        className="btn-mic-test active"
                        onClick={stopMicTest}
                      >
                        ⏹️ 正在试音 ({testCountdown}s)
                      </button>
                    )}
                    {testState === "done" && (
                      <button
                        type="button"
                        className="btn-mic-test"
                        onClick={() => void startMicTest()}
                      >
                        🔄 重新试音
                      </button>
                    )}
                  </div>
                </div>
                <div className="setup-meter-tracks">
                  {tracks.map((track) => {
                    const isMic = track.label === "麦克风";
                    const isLiveRecording = track.levels.length > 0;

                    let level = 0;
                    let statusText = "未试音";
                    let isVoice = false;

                    if (isLiveRecording) {
                      const rawVal = track.levels.at(-1) || 0;
                      level = Math.min(1, Math.sqrt(Math.max(0, rawVal)) * 2.4);
                      isVoice = level > 0.05;
                      statusText = isVoice ? "有输入" : "就绪";
                    } else if (isMic) {
                      if (testState === "testing") {
                        level = testLevel;
                        isVoice = testLevel > 0.05;
                        statusText = isVoice ? "有输入" : "无信号";
                      } else if (testState === "done") {
                        const hadInput = peakLevelRef.current > 0.05;
                        statusText = hadInput ? "有输入" : "无信号";
                        isVoice = hadInput;
                        level = hadInput ? Math.min(1, peakLevelRef.current) : 0;
                      } else {
                        statusText = "未试音";
                        level = 0;
                        isVoice = false;
                      }
                    } else {
                      // 电脑声音 track
                      statusText = "未试音";
                      level = 0;
                      isVoice = false;
                    }

                    return (
                      <div className="setup-meter-item" key={track.label}>
                        <span className="meter-label">{track.label}</span>
                        <div className="meter-bar-container">
                          <div
                            className={`meter-bar-fill ${isVoice ? "active" : ""}`}
                            style={{ width: `${Math.round(level * 100)}%` }}
                          />
                        </div>
                        <span
                          className={`meter-val-text ${
                            isVoice
                              ? "active"
                              : testState === "done" && isMic && !isVoice
                                ? "muted"
                                : "untested"
                          }`}
                        >
                          {statusText}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>

            {/* 卡片 2: 语言、识别引擎与会后交付 */}
            <div className="setup-card">
              <div className="setup-card-header">
                <div className="setup-card-title-row">
                  <span className="setup-card-icon">🤖</span>
                  <div>
                    <h3>识别引擎与交付策略</h3>
                    <p>配置识别引擎、个人专有词库与会后整理规则。</p>
                  </div>
                </div>
              </div>

              {/* 语言与引擎并排 */}
              <div className="setup-form-row">
                <div className="setup-field flex-1">
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

                <div className="setup-field flex-1">
                  <label htmlFor="setup-engine">识别引擎</label>
                  <select
                    id="setup-engine"
                    className="setup-select"
                    value={draftEngine}
                    onChange={(e) =>
                      setDraftEngine(e.target.value as typeof draftEngine)
                    }
                  >
                    <option value="qwen">千问 Qwen-ASR (推荐)</option>
                    <option value="whisper">Whisper large-v3-turbo</option>
                  </select>
                </div>
              </div>

              {/* 个人术语库：专有名词与词库增强 */}
              <div className="setup-terms-card">
                <div className="setup-terms-info">
                  <span className="setup-terms-icon">📖</span>
                  <div className="setup-terms-text">
                    <div className="setup-terms-headline">
                      <strong>个人术语库</strong>
                      {recognitionTerms.trim() ? (
                        <span className="terms-active-badge">已启用专有词</span>
                      ) : (
                        <span className="terms-inactive-badge">未配置</span>
                      )}
                    </div>
                    <small>
                      {recognitionTerms.trim()
                        ? "已加载自定义词条，辅助专有名词与人名精准识别"
                        : "针对行业术语、人名或代码缩写提前配置专有词"}
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

              {/* 整理交付目标 */}
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
                    ✨ 文字稿 ＋ AI 纪要
                  </button>
                  <button
                    type="button"
                    className={`setup-pill ${postGoal === "transcript" && completionMode !== "none" ? "active" : ""}`}
                    onClick={() => {
                      setPostGoal("transcript");
                      setCompletionMode("full");
                    }}
                  >
                    📝 仅生成文字稿
                  </button>
                  <button
                    type="button"
                    className={`setup-pill ${completionMode === "none" ? "active" : ""}`}
                    onClick={() => {
                      setCompletionMode("none");
                    }}
                  >
                    💾 仅保存录音
                  </button>
                </div>
              </div>

              {/* 纪要选项与输出语言 */}
              <div className="setup-sub-options-row">
                <label className="setup-checkbox-row">
                  <input
                    type="checkbox"
                    checked={autoProcess}
                    onChange={(e) => setAutoProcess(e.target.checked)}
                  />
                  <span>录音结束后自动开启整理</span>
                </label>
                <div className="setup-output-lang-picker">
                  <label htmlFor="setup-output-lang">纪要语言：</label>
                  <select
                    id="setup-output-lang"
                    className="setup-select-inline"
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
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* 底部轻量就绪状态条 */}
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
          {isCloudMinutes ? (
            <span
              className="setup-footer-badge setup-privacy-badge cloud-notice"
              title="录音与实时识别纯本地离线处理；会后纪要经由您核对确认后发送至所选云端模型"
            >
              🛡️ 录音转写本地离线 · 会后纪要经确认由云端处理（{selectedMinutesProfile?.name || selectedMinutesProfile?.model || "云端模型"}）
            </span>
          ) : (
            <span className="setup-footer-badge setup-privacy-badge">
              🔒 纯本地离线处理 · 无云端数据传输
            </span>
          )}
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
