import { browserCaptureAllowed } from "../../preferences/nativeHost";
import MeetingBriefForm from "../meeting/MeetingBriefForm";
import { type CompletionMode } from "./recordingPreferences";
import Modal from "../../components/Modal";
import type { RecordingViewModel } from "./useRecordingController";

type Props = Pick<
  RecordingViewModel,
  | "autoProcess"
  | "brief"
  | "briefReady"
  | "briefSaved"
  | "captureSource"
  | "echoMode"
  | "setEchoMode"
  | "checkCapture"
  | "checkingCapture"
  | "completionMode"
  | "contextEnabled"
  | "controlModel"
  | "correctionEnabled"
  | "correctionProfileId"
  | "draftEngine"
  | "engineLabel"
  | "engines"
  | "environment"
  | "flowReady"
  | "meetings"
  | "minutesModel"
  | "modelActionBusy"
  | "modelStatus"
  | "nativeCapability"
  | "onClose"
  | "onMinutesModel"
  | "onModels"
  | "postGoal"
  | "prefixEnabled"
  | "recognitionTerms"
  | "responsiveMode"
  | "setAutoProcess"
  | "setBrief"
  | "setCaptureSource"
  | "setCompletionMode"
  | "setContextEnabled"
  | "setCorrectionEnabled"
  | "setCorrectionProfileId"
  | "setDraftEngine"
  | "setPostGoal"
  | "setPrefixEnabled"
  | "setRecognitionTerms"
  | "setResponsiveMode"
  | "setSettingsOpen"
  | "setSettingsTab"
  | "setSilenceReminder"
  | "setStreamEnabled"
  | "settingsTab"
  | "silenceReminder"
  | "state"
  | "streamEnabled"
>;
export default function RecordingSettingsDialog({
  autoProcess,
  brief,
  briefReady,
  briefSaved,
  captureSource,
  echoMode,
  setEchoMode,
  checkCapture,
  checkingCapture,
  completionMode,
  contextEnabled,
  controlModel,
  correctionEnabled,
  correctionProfileId,
  draftEngine,
  engineLabel,
  engines,
  environment,
  flowReady,
  meetings,
  minutesModel,
  modelActionBusy,
  modelStatus,
  nativeCapability,
  onClose,
  onMinutesModel,
  onModels,
  postGoal,
  prefixEnabled,
  recognitionTerms,
  responsiveMode,
  setAutoProcess,
  setBrief,
  setCaptureSource,
  setCompletionMode,
  setContextEnabled,
  setCorrectionEnabled,
  setCorrectionProfileId,
  setDraftEngine,
  setPostGoal,
  setPrefixEnabled,
  setRecognitionTerms,
  setResponsiveMode,
  setSettingsOpen,
  setSettingsTab,
  setSilenceReminder,
  setStreamEnabled,
  settingsTab,
  silenceReminder,
  state,
  streamEnabled,
}: Props) {
  return (
    <Modal
      className="recording-settings-dialog recording-config-panel"
      ariaLabel="本次录音配置"
      onClose={() => setSettingsOpen(false)}
    >
      <div className="search-heading">
        <div>
          <span className="config-panel-eyebrow">本次会议</span>
          <h2>录音方案</h2>
          <p>调整当前方案，主工作区保持原位。</p>
        </div>
        <button onClick={() => setSettingsOpen(false)}>完成</button>
      </div>
      <div className="settings-tabs" aria-label="录音配置分类">
        {(
          [
            ["recording", "音源与权限"],
            ["model", "实时识别"],
            ["outcome", "会后产物"],
            ...(flowReady ? [["preparation", "会议准备"]] : []),
          ] as [typeof settingsTab, string][]
        ).map(([tab, label]) => (
          <button
            key={tab}
            aria-pressed={settingsTab === tab}
            onClick={() => setSettingsTab(tab)}
          >
            {label}
          </button>
        ))}
      </div>
      {(settingsTab === "model" || settingsTab === "outcome") && (
        <fieldset disabled={state !== "idle"} className="config-panel-section">
          {settingsTab === "model" ? (
            <label>
              原文语言
              <select
                aria-label="录音原文语言"
                value={brief.source_language || "auto"}
                onChange={(event) =>
                  setBrief({
                    ...brief,
                    source_language: event.target
                      .value as typeof brief.source_language,
                  })
                }
              >
                <option value="auto">自动识别（中英）</option>
                <option value="zh">中文</option>
                <option value="en">英文</option>
                <option value="ja">日语（Beta）</option>
              </select>
            </label>
          ) : (
            <label>
              纪要输出语言
              <select
                aria-label="录音纪要输出语言"
                value={brief.output_language || "zh"}
                onChange={(event) =>
                  setBrief({
                    ...brief,
                    output_language: event.target
                      .value as typeof brief.output_language,
                  })
                }
              >
                <option value="zh">中文</option>
                <option value="en">英文</option>
              </select>
            </label>
          )}
          <p className="muted">原文按所选语言转写，纪要按独立设置整理。</p>
        </fieldset>
      )}
      {settingsTab === "outcome" && (
        <section className="config-panel-section">
          <h3>结束录音后，你希望得到什么？</h3>
          <p className="config-panel-description">
            原音与简记先保存，再按此方案继续整理。
          </p>{" "}
          <div className="recording-plan-body">
            <label>
              交付目标
              <select
                aria-label="录音交付目标"
                value={postGoal}
                onChange={(e) => {
                  setPostGoal(e.target.value as "transcript" | "minutes");
                  if (e.target.value === "minutes" && completionMode === "none")
                    setCompletionMode("quick");
                }}
              >
                <option value="transcript">文字稿／录音资料</option>
                <option
                  value="minutes"
                  disabled={!minutesModel || !environment?.auto_minutes_ready}
                >
                  文字稿＋AI 纪要（含待办候选）
                </option>
              </select>
            </label>
            {postGoal === "minutes" && (
              <label>
                纪要模型
                <select
                  aria-label="自动纪要模型"
                  value={minutesModel}
                  onChange={(e) => onMinutesModel(e.target.value)}
                >
                  {environment?.analysis.profiles.length
                    ? environment.analysis.profiles.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name} ·{" "}
                          {p.provider_type === "cloud" ? "云端" : "本地"}
                        </option>
                      ))
                    : environment?.analysis.models.map((m) => (
                        <option key={m}>{m}</option>
                      ))}
                </select>
              </label>
            )}
            {postGoal === "minutes" &&
              (!minutesModel || !environment?.auto_minutes_ready) && (
                <p role="alert">
                  自动纪要模型或后台服务尚未就绪，请调整模型或选择仅文字稿。
                </p>
              )}
            <label>
              结束后处理
              <select
                aria-label="结束后处理"
                value={completionMode}
                onChange={(e) => {
                  setCompletionMode(e.target.value as CompletionMode);
                  if (e.target.value === "none") setPostGoal("transcript");
                }}
              >
                <option value="quick">
                  快速完成文字稿（复用
                  {draftEngine === "qwen" ? "千问" : "Whisper"}实时结果）
                </option>
                <option value="full">精细处理文字稿（{engineLabel}）</option>
                <option value="none">仅保存录音和关注点</option>
              </select>
            </label>
            <label>
              <input
                type="checkbox"
                checked={autoProcess}
                onChange={(e) => setAutoProcess(e.target.checked)}
              />
              结束后自动进入会后整理
            </label>
            <p>
              先保存录音，再完成文字稿；选择 AI
              纪要时随后排队生成草稿，使用上次纪要模板与详略，行动项仍需核对。云端模型会在排队前再次确认；异常恢复的录音默认只处理文字稿。
            </p>
          </div>
        </section>
      )}
      {settingsTab === "preparation" && flowReady && (
        <section className="config-panel-section">
          <h3>给这场会议一点背景</h3>
          <p className="config-panel-description">
            可选填写，帮助会后整理理解本次目标。
            {briefSaved ? "已保存到本机。" : "正在保存…"}
          </p>{" "}
          <MeetingBriefForm
            value={brief}
            onChange={setBrief}
            meetings={meetings}
            disabled={!briefReady}
          />
        </section>
      )}
      {settingsTab === "recording" && (
        <section>
          <label>
            <input
              type="checkbox"
              checked={silenceReminder}
              onChange={(event) => setSilenceReminder(event.target.checked)}
            />
            持续安静约 5 分钟时提醒（仅提醒，继续录音）
          </label>
          <label className="capture-source">
            录音来源
            <select
              aria-label="录音来源"
              value={captureSource}
              disabled={state !== "idle"}
              onChange={(e) =>
                setCaptureSource(e.target.value as typeof captureSource)
              }
            >
              <option value="browser" disabled={!browserCaptureAllowed()}>
                浏览器麦克风（支持实时草稿）
              </option>
              <option
                value="macos-both"
                disabled={!nativeCapability?.available}
              >
                macOS 电脑声音 + 麦克风
              </option>
              <option
                value="macos-system"
                disabled={!nativeCapability?.available}
              >
                macOS 仅电脑声音
              </option>
            </select>
          </label>
          {captureSource !== "browser" && (
            <p className="native-capture-hint">
              首次使用需允许
              macOS“屏幕与系统音频录制”权限，联合采集还需麦克风权限。仅保存音频，不保存屏幕画面。建议戴耳机，避免麦克风重复收录扬声器声音。所选引擎生成实时草稿，停止后生成正式文字稿。
            </p>
          )}
          {state === "idle" && (
            <div className="capture-preflight">
              <span>
                原生采集器：
                {nativeCapability?.available ? "已就绪" : "未就绪"} ·
                系统音频权限：
                {nativeCapability?.screen_permission ? "已授权" : "待授权"} ·
                麦克风权限：
                {nativeCapability?.microphone_permission ? "已授权" : "待授权"}
              </span>
              <button
                disabled={checkingCapture}
                onClick={() => void checkCapture()}
              >
                {checkingCapture ? "检查中…" : "重新检查"}
              </button>
              <small>
                在系统设置中授权后重新检查；开始原生录音时会请求所需权限。
              </small>
              {nativeCapability?.ownership &&
                nativeCapability.ownership.lease !== "free" && (
                  <small role="status">
                    已有采集所有者或所有权尚未核对；不会重复启动或自动接管。请先检查正在进行的录音。
                  </small>
                )}
              <small>
                独立控制服务可在 API
                重启后继续采集；使用“继续查看”明确恢复控制。旧版录音仍只恢复已保存内容。
              </small>
            </div>
          )}
          {!nativeCapability?.available && (
            <p className="native-capture-hint">
              系统音频采集器尚未就绪，可运行 bash scripts/build_capture.sh
              构建。
            </p>
          )}
        </section>
      )}
      {settingsTab === "model" && (
        <section>
          <button
            onClick={() => {
              setSettingsOpen(false);
              onModels();
            }}
          >
            分析 LLM 与本机服务配置
          </button>
          <label className="capture-source">
            实时草稿引擎
            <select
              aria-label="实时草稿引擎"
              value={draftEngine}
              disabled={state !== "idle"}
              onChange={(event) =>
                setDraftEngine(event.target.value as "whisper" | "qwen")
              }
            >
              {(["whisper", "qwen"] as const).map((id) => (
                <option
                  key={id}
                  value={id}
                  disabled={!engines.find((engine) => engine.id === id)?.ready}
                >
                  {id === "qwen" ? "千问 Qwen-ASR" : "Whisper"}
                  {!engines.find((engine) => engine.id === id)?.ready
                    ? "（未就绪）"
                    : ""}
                </option>
              ))}
            </select>
          </label>
          {captureSource === "macos-both" && (
            <label className="capture-source">
              双轨实时消回声
              <select
                value={echoMode}
                disabled={state !== "idle"}
                onChange={(event) =>
                  setEchoMode(
                    event.target.value === "aec3" ? "aec3" : "standard",
                  )
                }
              >
                <option value="standard">标准</option>
                <option value="aec3">WebRTC AEC3（实验）</option>
              </select>
              <small>AEC3 不可用时自动切回标准处理。原始音轨独立保存。</small>
            </label>
          )}
          <p className="native-capture-hint">
            实时草稿与停止后的正式转写引擎独立选择。
            {draftEngine === "qwen"
              ? `千问草稿按识别片段定位，不是精确字词时间。${streamEnabled ? "流式识别复用常驻模型" : "整批输出每段加载模型"}；正式转写仍保留精细对齐。`
              : "Whisper 草稿保留句级时间戳。"}
          </p>
          {draftEngine === "qwen" && (
            <label className="capture-source">
              <input
                type="checkbox"
                checked={streamEnabled}
                disabled={state !== "idle"}
                onChange={(event) => setStreamEnabled(event.target.checked)}
              />
              逐字推送
              <small>
                {responsiveMode
                  ? "短片段先预览，再用后续上下文修订。"
                  : "收集约 15 秒音频后，文字逐步显示。"}
                未确认文字不计入覆盖，也不能转为简记。关闭后整批显示。
              </small>
            </label>
          )}
          {draftEngine === "qwen" && streamEnabled && (
            <>
              <label className="capture-source">
                <input
                  type="checkbox"
                  checked={responsiveMode}
                  disabled={state !== "idle" || !modelStatus}
                  onChange={(event) => setResponsiveMode(event.target.checked)}
                />
                短片段与尾部修订（试用）
              </label>
              <p className="native-capture-hint">
                {responsiveMode
                  ? "约 3 秒开始预览；优先在约 0.8 秒停顿处确认。15 秒为软目标，连续讲话可延长到 25 秒；硬切分的连续发言会合并阅读。"
                  : "标准模式：约 15 秒一段，完成后确认。"}
              </p>
              {responsiveMode && (
                <label className="capture-source">
                  <input
                    type="checkbox"
                    aria-label="前缀续写与尾部回退"
                    checked={prefixEnabled}
                    disabled={state !== "idle"}
                    onChange={(event) => setPrefixEnabled(event.target.checked)}
                  />
                  前缀续写与尾部回退（试用）
                  <small>
                    连续预览稳定后保留前缀，重新生成尾部；确认时完整复核。音频相同可复用编码，音频增长后重新编码。
                  </small>
                </label>
              )}
              <div className="capture-preflight">
                <span>
                  Qwen 模型：
                  {modelStatus?.loading
                    ? "加载中"
                    : modelStatus?.loaded
                      ? "已常驻"
                      : "未加载"}{" "}
                  ·{" "}
                  {modelStatus?.busy
                    ? "识别中"
                    : modelStatus?.formal_job_running
                      ? "正式任务处理中"
                      : "空闲"}
                  {modelStatus?.peak_memory_gb
                    ? ` · 峰值 ${modelStatus.peak_memory_gb.toFixed(1)} GB`
                    : ""}
                </span>
                <button
                  disabled={
                    state !== "idle" || modelActionBusy || modelStatus?.busy
                  }
                  onClick={() => void controlModel("warm")}
                >
                  预加载模型
                </button>
                <button
                  disabled={
                    !modelStatus?.loaded ||
                    modelActionBusy ||
                    modelStatus?.busy ||
                    !["idle", "paused"].includes(state)
                  }
                  onClick={() => void controlModel("release")}
                >
                  释放模型
                </button>
                <small>
                  同一进程复用模型，空闲约 3
                  分钟自动释放；正式处理任务优先释放常驻模型。取消正在识别的窗口会重建进程。
                </small>
              </div>
            </>
          )}
          {draftEngine === "qwen" && streamEnabled && (
            <fieldset
              className="live-context-settings"
              disabled={state !== "idle"}
            >
              <legend>上下文与纠错</legend>
              <label>
                <input
                  type="checkbox"
                  checked={contextEnabled}
                  onChange={(e) => setContextEnabled(e.target.checked)}
                />{" "}
                使用前文与会议准备辅助识别
              </label>
              <label>
                会议术语／姓名
                <textarea
                  aria-label="识别参考术语"
                  rows={2}
                  maxLength={1000}
                  value={recognitionTerms}
                  placeholder="例如：听记、Qwen、项目代号、参会人姓名；仅作为词汇参考"
                  onChange={(e) => setRecognitionTerms(e.target.value)}
                />
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={correctionEnabled}
                  onChange={(e) => setCorrectionEnabled(e.target.checked)}
                />{" "}
                已确认片段智能纠错（本地 LLM）
              </label>
              {correctionEnabled && (
                <label>
                  纠错模型
                  <select
                    aria-label="实时纠错模型"
                    value={correctionProfileId}
                    onChange={(e) => setCorrectionProfileId(e.target.value)}
                  >
                    <option value="">请选择本地模型配置</option>
                    {environment?.analysis.profiles
                      .filter((p) => p.provider_type === "local")
                      .map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name} · {p.model}
                        </option>
                      ))}
                  </select>
                </label>
              )}
              <small>
                原始识别始终保存；内容修改需在“纠错记录”确认。校对失败不影响录音与转写。与
                ASR 同时运行本地 LLM 可能增加延迟。
              </small>
            </fieldset>
          )}
        </section>
      )}
      <p className="muted">
        模型选择保存在本机工作空间，其余录音偏好保存在当前浏览器。录音时不能更换音源或识别模式；结束后可选择快速完成或精细处理。
      </p>
    </Modal>
  );
}
