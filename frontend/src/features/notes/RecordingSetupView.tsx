import { useState, useEffect } from "react";
import { browserCaptureAllowed } from "../../preferences/nativeHost";
import AudioCheck from "../onboarding/AudioCheck";
import { clock } from "../../utils";
import type { Environment } from "../../types";
import type { RecordingViewModel } from "./useRecordingController";
import {
  IconBook,
  IconBriefcase,
  IconClipboard,
  IconCpu,
  IconEdit,
  IconFileText,
  IconGlobe,
  IconHeadphones,
  IconHelpCircle,
  IconLaptop,
  IconLink,
  IconLock,
  IconMic,
  IconPin,
  IconSave,
  IconShield,
  IconSliders,
  IconSparkles,
  IconTarget,
} from "../../styles/icons";

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
  | "setEchoMode"
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
  setEchoMode,
  engines,
  onModels,
  setTermsOpen,
  recognitionTerms = "",
  runningSessions = [],
  attachRunning,
  requesting = false,
  onStart,
  onClose,
  echoMode = "standard",
  environment,
  minutesModel,
}: RecordingSetupViewProps) {
  const [meetingMode, setMeetingMode] = useState<"general" | "interview">(() =>
    brief.interview && (brief.interview.candidate || brief.interview.role)
      ? "interview"
      : "general",
  );

  const [agendaTab, setAgendaTab] = useState<
    "agenda" | "purpose" | "questions"
  >("agenda");

  const [testingAudio, setTestingAudio] = useState(false);

  const selectedMinutesProfile = environment?.analysis?.profiles?.find(
    (p) => p.id === minutesModel,
  );
  const isCloudMinutes =
    postGoal === "minutes" &&
    completionMode !== "none" &&
    autoProcess &&
    selectedMinutesProfile?.provider_type === "cloud";

  const startDisabled =
    testingAudio ||
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
            title={
              requesting ? "正在启动录音，暂不可返回" : "自动保存备会草稿并返回"
            }
          >
            ← 返回
          </button>
          <div className="setup-title-group">
            <h2>会前准备与就绪舱</h2>
            <span className="setup-status-pill">
              {requesting
                ? "正在启动录音通道…"
                : briefSaved
                  ? "✓ 备会已保存"
                  : "正在保存草稿…"}
            </span>
            <span
              className={`setup-readiness-chip${isCloudMinutes ? " cloud" : ""}`}
              title={
                isCloudMinutes
                  ? "录音与实时识别纯本地离线处理；会后纪要经由您核对确认后发送至所选云端模型"
                  : "纯本地离线处理 · 无云端数据传输"
              }
            >
              {isCloudMinutes ? (
                <>
                  <IconShield size={12} /> 录音离线 · 纪要云端（
                  {selectedMinutesProfile?.name ||
                    selectedMinutesProfile?.model ||
                    "云端模型"}
                  ）
                </>
              ) : (
                <>
                  <IconLock size={12} /> 纯本地离线
                </>
              )}
              {preflight?.storage.writable === false
                ? " · 存储异常"
                : !nativeCapability?.available
                  ? " · 需音频授权"
                  : ""}
            </span>
          </div>
        </div>
        <div className="setup-topbar-right">
          <button
            type="button"
            className="setup-mgmt-link"
            onClick={onModels}
            title="查看完整依赖、模型及环境自检"
          >
            运行与模型管理
          </button>
          <button
            type="button"
            className={`setup-start-btn ${requesting ? "loading" : ""}`}
            disabled={startDisabled || requesting}
            onClick={onStart}
            title={requesting ? "正在请求权限并启动录音…" : "快捷键: ⌘ + Enter"}
          >
            <span
              className={`setup-record-dot ${requesting ? "loading" : ""}`}
            />
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
                  <div className="setup-eyebrow-tag">
                    PRE-MEETING CONTEXT · 会议预设
                  </div>
                  <h3>
                    <IconEdit size={16} /> 会议背景与讨论诉求
                  </h3>
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
                    <IconPin size={14} /> 常规业务会议
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
                    <IconBriefcase size={14} /> 招聘面试考察
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
                        <IconClipboard size={13} /> 议程与背景材料
                        {brief.agenda?.trim() ? (
                          <span
                            className="tab-has-content-dot"
                            title="已填写内容"
                          />
                        ) : null}
                      </button>
                      <button
                        type="button"
                        role="tab"
                        aria-selected={agendaTab === "purpose"}
                        className={`setup-agenda-tab-btn ${agendaTab === "purpose" ? "active" : ""}`}
                        onClick={() => setAgendaTab("purpose")}
                      >
                        <IconTarget size={13} /> 预期目标与结论
                        {brief.purpose?.trim() ? (
                          <span
                            className="tab-has-content-dot"
                            title="已填写内容"
                          />
                        ) : null}
                      </button>
                      <button
                        type="button"
                        role="tab"
                        aria-selected={agendaTab === "questions"}
                        className={`setup-agenda-tab-btn ${agendaTab === "questions" ? "active" : ""}`}
                        onClick={() => setAgendaTab("questions")}
                      >
                        <IconHelpCircle size={13} /> 需拍板关注问题
                        {brief.questions?.trim() ? (
                          <span
                            className="tab-has-content-dot"
                            title="已填写内容"
                          />
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
                  <IconBriefcase size={14} /> 招聘面试专属预设 ·
                  系统将自动按面试官问题、候选人回答与能力维度整理纪要
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
              <label htmlFor="setup-prev-meeting">
                <IconLink size={13} /> 关联上下文（可选）
              </label>
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
                  <span className="setup-card-icon">
                    <IconMic size={16} />
                  </span>
                  <div>
                    <h3>录音音源与即时试音</h3>
                    <p>选择参会形式，可按当前音源试录并回听。</p>
                  </div>
                </div>
              </div>

              {/* 2x2 紧凑收音卡片矩阵 */}
              <div
                className="setup-source-grid"
                role="radiogroup"
                aria-label="收音方案"
              >
                <button
                  type="button"
                  className={`setup-source-card ${captureSource === "macos-both" ? "selected" : ""}`}
                  disabled={!nativeCapability?.available}
                  onClick={() => setCaptureSource("macos-both")}
                >
                  <div className="setup-source-card-top">
                    <span className="setup-source-icon">
                      <IconHeadphones size={15} />
                    </span>
                    <strong>混合会议</strong>
                    {captureSource === "macos-both" && (
                      <span className="source-check-badge">✓</span>
                    )}
                  </div>
                  <span className="setup-source-desc">电脑声音 ＋ 麦克风</span>
                  <small className="setup-source-sub">
                    腾讯会议 / 飞书 / Zoom
                  </small>
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
                    <span className="setup-source-icon">
                      <IconMic size={15} />
                    </span>
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
                    <span className="setup-source-icon">
                      <IconLaptop size={15} />
                    </span>
                    <strong>线上旁听</strong>
                    {captureSource === "macos-system" && (
                      <span className="source-check-badge">✓</span>
                    )}
                  </div>
                  <span className="setup-source-desc">仅电脑声音</span>
                  <small className="setup-source-sub">
                    线上培训 / 直播 / 讲座
                  </small>
                </button>

                {browserCaptureAllowed() ? (
                  <button
                    type="button"
                    className={`setup-source-card ${captureSource === "browser" ? "selected" : ""}`}
                    onClick={() => setCaptureSource("browser")}
                  >
                    <div className="setup-source-card-top">
                      <span className="setup-source-icon">
                        <IconGlobe size={15} />
                      </span>
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
                      <span className="setup-source-icon">
                        <IconLock size={15} />
                      </span>
                      <strong>系统收音就绪</strong>
                    </div>
                    <span className="setup-source-desc">
                      原生高保真离线采集
                    </span>
                    <small className="setup-source-sub">低延迟 · 纯本地</small>
                  </div>
                )}
              </div>

              {/* 双轨高级处理选项（仅在混合会议时展开，Apple 风格分段控制） */}
              {captureSource === "macos-both" && (
                <div className="setup-echo-control-bar">
                  <div className="echo-bar-label">
                    <span className="echo-icon">
                      <IconSliders size={15} />
                    </span>
                    <div className="echo-label-group">
                      <strong>双轨回声消除</strong>
                      <small>佩戴耳机推荐标准处理；外放扬声器推荐 AEC3</small>
                    </div>
                  </div>
                  <div
                    className="echo-segmented-pills"
                    role="radiogroup"
                    aria-label="双轨回声消除模式"
                  >
                    <button
                      type="button"
                      className={`echo-pill-btn ${echoMode === "standard" ? "active" : ""}`}
                      disabled={requesting || testingAudio}
                      onClick={() => setEchoMode("standard")}
                    >
                      标准处理
                    </button>
                    <button
                      type="button"
                      className={`echo-pill-btn ${echoMode === "aec3" ? "active" : ""}`}
                      disabled={requesting || testingAudio}
                      onClick={() => setEchoMode("aec3")}
                    >
                      WebRTC AEC3
                      <span className="pill-badge">实验</span>
                    </button>
                  </div>
                </div>
              )}

              {/* 专业拟真音源试录与电平监听台 */}
              <AudioCheck
                key={captureSource}
                source={captureSource}
                engine={draftEngine}
                engineReady={
                  !!engines.find((engine) => engine.id === draftEngine)?.ready
                }
                onActiveChange={setTestingAudio}
              />
            </div>

            {/* 卡片 2: 语言、识别引擎与会后交付 */}
            <div className="setup-card">
              <div className="setup-card-header">
                <div className="setup-card-title-row">
                  <span className="setup-card-icon">
                    <IconCpu size={16} />
                  </span>
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
                        source_language: e.target
                          .value as typeof brief.source_language,
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
                  <span className="setup-terms-icon">
                    <IconBook size={15} />
                  </span>
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
                    <IconSparkles size={13} /> 文字稿 ＋ AI 纪要
                  </button>
                  <button
                    type="button"
                    className={`setup-pill ${postGoal === "transcript" && completionMode !== "none" ? "active" : ""}`}
                    onClick={() => {
                      setPostGoal("transcript");
                      setCompletionMode("full");
                    }}
                  >
                    <IconFileText size={13} /> 仅生成文字稿
                  </button>
                  <button
                    type="button"
                    className={`setup-pill ${completionMode === "none" ? "active" : ""}`}
                    onClick={() => {
                      setCompletionMode("none");
                    }}
                  >
                    <IconSave size={13} /> 仅保存录音
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
                        output_language: e.target
                          .value as typeof brief.output_language,
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
    </div>
  );
}
