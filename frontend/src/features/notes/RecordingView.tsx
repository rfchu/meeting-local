import {
  browserCaptureAllowed,
  embeddedHost,
} from "../../preferences/nativeHost";
import RecordingSettingsDialog from "./RecordingSettingsDialog";
import { toggleOverviewFocus } from "../overview/notes";
import LiveOverviewPanel from "../overview/LiveOverviewPanel";
import RecordingReadiness from "../onboarding/RecordingReadiness";
import TermDialog from "../terminology/TermDialog";
import LiveCorrectionReview from "./LiveCorrectionReview";
import RecordingSetupBar from "./RecordingSetupBar";
import type { MeetingNoteType } from "../../types";
import { clock } from "../../utils";
import Modal from "../../components/Modal";
import RecordingWaveform from "./RecordingWaveform";
import RecordingHealthPanel from "./RecordingHealthPanel";
import RecordingCompletionPanel from "./RecordingCompletionPanel";
import RecordingEndGuide from "./RecordingEndGuide";
import LiveDraftList from "./LiveDraftList";
import type { RecordingViewModel } from "./useRecordingController";

export default function RecordingView({
  acceptReadiness,
  accruedRef,
  addNote,
  attachRunning,
  autoProcess,
  awakeStatus,
  backendLive,
  brief,
  briefReady,
  briefSaved,
  captureHealth,
  nativeOwnership,
  recoveringControl,
  recoverActiveControl,
  captureSource,
  checkCapture,
  checkingCapture,
  closeWithPreparation,
  completing,
  completionMode,
  contextEnabled,
  controlModel,
  correctionEnabled,
  correctionProfileId,
  correctionReviewOpen,
  correctionSessionId,
  coverageDetails,
  coverageStatus,
  currentSeconds,
  decodeReport,
  displayDrafts,
  draftEngine,
  draftStatus,
  drafts,
  elapsed,
  endGuide,
  engineLabel,
  engineName,
  engines,
  environment,
  exportDiagnostics,
  failedChunksRef,
  finish,
  finishStage,
  flowReady,
  handoffError,
  handoffSaved,
  historyOpen,
  inspectorOpen,
  jumpDraft,
  keepAwake,
  liveCorrection,
  liveMetrics,
  localSaveFailedRef,
  managedMode,
  markNow,
  meetings,
  metadata,
  microphoneLevels,
  minutesModel,
  mobilePane,
  modelActionBusy,
  modelStatus,
  nativeCapability,
  nativeUnavailable,
  noteLabels,
  noteOptionsOpen,
  noteOptionsRef,
  noteScope,
  noteText,
  noteType,
  notes,
  onClose,
  onMinutesModel,
  onModels,
  partialDraft,
  pause,
  postGoal,
  preferencesSaved,
  prefixEnabled,
  preflight,
  processSaved,
  processingDeferredRef,
  recognitionTerms,
  recordOnly,
  recording,
  responsiveMode,
  resume,
  retrySync,
  runningSessions,
  saveStatus,
  savedEngine,
  savedMeetingId,
  sessionRef,
  setAutoProcess,
  setBrief,
  setCaptureSource,
  setCompletionMode,
  setContextEnabled,
  setCorrectionEnabled,
  setCorrectionProfileId,
  setCorrectionReviewOpen,
  setDraftEngine,
  setHistoryOpen,
  setInspectorOpen,
  setJumpDraft,
  setKeepAwake,
  setMobilePane,
  setNoteOptionsOpen,
  setNoteScope,
  setNoteText,
  setNoteType,
  setNotes,
  setPostGoal,
  setPrefixEnabled,
  setRecognitionTerms,
  setResponsiveMode,
  setSettingsOpen,
  setSettingsTab,
  setSilenceReminder,
  setStreamEnabled,
  setTermsOpen,
  settingsOpen,
  settingsTab,
  silenceReminder,
  sourceLabel,
  start,
  state,
  stop,
  streamEnabled,
  systemLevels,
  termsOpen,
  toast,
}: RecordingViewModel) {
  const controlRequired =
    sessionRef.current?.source === "macos" &&
    !!nativeOwnership?.control_required;
  return (
    <Modal
      id="recordingWorkbench"
      className={`recording-workbench${recording ? " focus-mode" : ""}`}
      ariaLabel="录音与会议简记工作台"
      onClose={() => {
        if (
          !recording &&
          !completing &&
          !["saving", "requesting"].includes(state) &&
          !failedChunksRef.current.length &&
          !localSaveFailedRef.current
        )
          closeWithPreparation();
      }}
    >
      {termsOpen && (
        <TermDialog
          scope={sessionRef.current?.id || "library"}
          onClose={() => setTermsOpen(false)}
        />
      )}
      {embeddedHost() && (
        <p className="muted">
          内嵌工作区可使用已有原生音轨模式；浏览器麦克风录音请打开宿主的“浏览器工作台”。
        </p>
      )}
      {state === "idle" &&
        captureSource !== "browser" &&
        nativeCapability?.ownership &&
        nativeCapability.ownership.lease !== "free" && (
          <p className="muted" role="status">
            原生采集所有权待核对，请在“采集与识别”中重新检查；不会自动接管或重复录音。
          </p>
        )}
      <div className="workbench-topbar">
        <div>
          <div className="eyebrow">本机会议采集</div>
          <h2>录音与会议简记</h2>
          <button className="text-btn" onClick={() => setTermsOpen(true)}>
            {sessionRef.current ? "会议术语" : "个人术语库"}
          </button>
        </div>
        <div
          className={`recording-clock${recording && (captureSource === "browser" || (!captureHealth?.stalled && !nativeUnavailable)) ? " active" : ""}`}
        >
          <span /> {clock(elapsed)}
        </div>
        <div className="recording-controls">
          {state === "idle" && (
            <button
              className="primary"
              disabled={
                preflight?.storage.writable === false ||
                (captureSource === "browser" &&
                  (!browserCaptureAllowed() ||
                    typeof MediaRecorder === "undefined" ||
                    !navigator.mediaDevices?.getUserMedia)) ||
                (captureSource !== "browser" &&
                  (!nativeCapability?.available ||
                    (!!nativeCapability.ownership &&
                      nativeCapability.ownership.lease !== "free")))
              }
              onClick={() => void start()}
            >
              ●{" "}
              {(preflight?.engines || engines).find(
                (engine) => engine.id === draftEngine,
              )?.ready
                ? "开始录音"
                : "仅录音与简记"}
            </button>
          )}
          {state === "requesting" && (
            <button disabled>
              {captureSource === "browser"
                ? "正在请求麦克风…"
                : "等待 macOS 采集权限…"}
            </button>
          )}
          {state === "recording" && (
            <button disabled={controlRequired} onClick={pause}>
              暂停
            </button>
          )}
          {state === "paused" && (
            <button disabled={controlRequired} onClick={resume}>
              继续录音
            </button>
          )}
          {recording && (
            <button
              className="recording-stop"
              disabled={endGuide.pending || controlRequired}
              onClick={endGuide.request}
            >
              ■{" "}
              {autoProcess && !processingDeferredRef.current
                ? "结束并整理"
                : "结束录音"}
            </button>
          )}
          {recording && controlRequired && (
            <button
              disabled={
                recoveringControl || !nativeOwnership.can_recover_control
              }
              onClick={() => void recoverActiveControl()}
            >
              {recoveringControl
                ? "正在恢复控制…"
                : nativeOwnership.can_recover_control
                  ? "恢复录音控制"
                  : "等待控制租约释放"}
            </button>
          )}
          {state === "saving" && (
            <span role="status" aria-live="polite">
              {finishStage}
            </span>
          )}
          {state === "failed" && (
            <button
              className="primary"
              onClick={() => {
                const session = metadata();
                if (session)
                  void finish(session, session.draft_engine || draftEngine);
              }}
            >
              重试完成保存
            </button>
          )}
          {!recording &&
            !completing &&
            state !== "saving" &&
            state !== "requesting" && (
              <button
                onClick={() => {
                  if (
                    failedChunksRef.current.length ||
                    localSaveFailedRef.current
                  )
                    return toast("还有未落盘的分段，请先重试保存");
                  closeWithPreparation();
                }}
              >
                {state === "failed"
                  ? "稍后恢复"
                  : state === "finished"
                    ? "返回会议资料库"
                    : "返回"}
              </button>
            )}
        </div>
      </div>
      <RecordingEndGuide guide={endGuide} />
      {recording &&
        captureSource !== "browser" &&
        (captureHealth?.stalled ||
          nativeUnavailable ||
          captureHealth?.tracks.some(
            (t) => t.state === "no_data" || t.file_state === "unconfirmed",
          )) && (
          <section
            className="recording-end-guide"
            role="alert"
            aria-label="采集状态异常"
          >
            <div>
              <strong>
                {nativeUnavailable
                  ? "暂时无法连接录音服务"
                  : captureHealth?.stalled
                    ? "采集心跳停止更新"
                    : captureHealth?.tracks.some(
                          (t) => t.file_state === "unconfirmed",
                        )
                      ? "音轨保存状态待核对"
                      : "部分音轨未收到音频"}
              </strong>
              <span>
                采集上报位置 {clock(accruedRef.current)}
                。已有音轨文件保留；请检查输入设备、其他软件的设备使用及磁盘空间，状态恢复后会自动更新。
              </span>
            </div>
            <button onClick={() => setInspectorOpen(true)}>检查运行详情</button>
            {!controlRequired && (
              <button disabled={endGuide.pending} onClick={endGuide.request}>
                结束并检查已存音轨
              </button>
            )}
          </section>
        )}
      {state === "idle" ? (
        <RecordingSetupBar
          source={sourceLabel}
          recognition={`${engineName} · ${({ auto: "自动识别", zh: "中文", en: "英文" } as const)[brief.source_language || "auto"]} · ${draftEngine === "qwen" && streamEnabled ? (responsiveMode ? "短片段模式" : "逐字推送") : "分段识别"}`}
          outcome={
            completionMode === "none"
              ? "仅保存录音与简记"
              : postGoal === "minutes"
                ? "文字稿＋AI 纪要"
                : "生成会议文字稿"
          }
          processing={`${completionMode === "none" ? "暂不转写" : completionMode === "quick" ? "快速完成" : "精细处理"} · ${autoProcess ? "自动进入整理" : "手动选择"}`}
          title={brief.title}
          brief={
            brief.interview?.role
              ? `线上面试 · ${brief.interview.role}`
              : brief.purpose
          }
          editable={state === "idle"}
          preferencesSaved={preferencesSaved}
          onCapture={() => {
            setSettingsTab("recording");
            setSettingsOpen(true);
          }}
          onOutcome={() => {
            setSettingsTab("outcome");
            setSettingsOpen(true);
          }}
          onBrief={
            flowReady
              ? () => {
                  setSettingsTab("preparation");
                  setSettingsOpen(true);
                }
              : undefined
          }
        />
      ) : (
        <div className="recording-session-strip" aria-label="当前录音方案">
          <strong>{brief.title || "本次会议"}</strong>
          <span>{sourceLabel}</span>
          <span>
            {recordOnly
              ? "仅录音与简记"
              : `${engineName} · ${({ auto: "自动识别", zh: "中文", en: "英文" } as const)[brief.source_language || "zh"]}`}
          </span>
          <span>
            {postGoal === "minutes" && !recordOnly
              ? "会后整理 AI 纪要"
              : "原始录音独立保存"}
          </span>
          <button className="text-btn" onClick={() => setInspectorOpen(true)}>
            运行详情
          </button>
        </div>
      )}
      {state === "idle" && (
        <RecordingReadiness
          nativeCapability={nativeCapability}
          source={captureSource}
          engine={draftEngine}
          onReadiness={acceptReadiness}
          onConfigure={() => {
            setSettingsTab("recording");
            setSettingsOpen(true);
          }}
          onModels={onModels}
        />
      )}
      {state === "idle" &&
        captureSource !== "browser" &&
        !nativeCapability?.available && (
          <p className="capture-warning">
            上次选择的原生音源暂不可用，改用浏览器麦克风后即可开始。
          </p>
        )}
      {state === "finished" && (
        <RecordingCompletionPanel
          notes={notes}
          savedMeetingId={savedMeetingId}
          savedEngine={savedEngine}
          handoffError={handoffError}
          recordOnly={recordOnly}
          completing={completing}
          completionMode={completionMode}
          postGoal={postGoal}
          engineLabel={engineLabel}
          onOpen={() => handoffSaved(savedMeetingId, "none", savedEngine)}
          onProcess={processSaved}
        />
      )}
      {state === "idle" && runningSessions.length > 0 && (
        <div className="live-session-resume" role="status">
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
      {recording && (
        <section
          className="recording-live-status"
          aria-label="声音、保存与识别"
        >
          <RecordingWaveform
            tracks={
              captureSource === "browser"
                ? [
                    {
                      label: "麦克风",
                      levels: microphoneLevels,
                      color: "#263752",
                    },
                  ]
                : [
                    {
                      label: "电脑声音",
                      levels: systemLevels,
                      color: "#3968e8",
                    },
                    ...(captureSource === "macos-both"
                      ? [
                          {
                            label: "麦克风",
                            levels: microphoneLevels,
                            color: "#263752",
                          },
                        ]
                      : []),
                  ]
            }
            active={recording}
            paused={state === "paused"}
            status={saveStatus}
            onRetry={
              recording &&
              (failedChunksRef.current.length > 0 ||
                localSaveFailedRef.current ||
                /失败|未完成|错误|待重试/.test(saveStatus))
                ? () => void retrySync().catch((error) => toast(error.message))
                : undefined
            }
          />
        </section>
      )}
      {state !== "idle" && (
        <RecordingHealthPanel
          id={sessionRef.current?.id || ""}
          state={state}
          native={sessionRef.current?.source === "macos"}
          tracks={captureHealth}
          localFailure={localSaveFailedRef.current}
          memoryOnly={failedChunksRef.current.length}
          recognitionEnabled={!recordOnly}
          onDiagnostics={() =>
            void exportDiagnostics(sessionRef.current!.id).catch((error) =>
              toast(error.message),
            )
          }
        />
      )}
      {state !== "idle" && (
        <div
          className={`workbench-history-wrapper${state === "finished" ? (historyOpen ? " ended open" : " ended") : ""}`}
        >
          {state === "finished" && (
            <button
              className="history-toggle"
              aria-expanded={historyOpen}
              onClick={() => setHistoryOpen((value) => !value)}
            >
              {historyOpen ? "收起" : "查看"}本次实时文字与简记（{notes.length}{" "}
              条简记）
            </button>
          )}
          {(state !== "finished" || historyOpen) && (
            <>
              <nav className="recording-view-tabs" aria-label="会中视图">
                <button
                  aria-pressed={mobilePane === "transcript"}
                  onClick={() => setMobilePane("transcript")}
                >
                  实时文字
                </button>
                <button
                  aria-pressed={mobilePane === "overview"}
                  onClick={() => setMobilePane("overview")}
                >
                  AI 概览
                </button>
                <button
                  className="mobile-only"
                  aria-pressed={mobilePane === "notes"}
                  onClick={() => setMobilePane("notes")}
                >
                  我的简记{notes.length ? ` · ${notes.length}` : ""}
                </button>
                <button
                  className="mobile-only"
                  disabled={!recording}
                  onClick={() => markNow()}
                >
                  ⚑ 标记此刻
                </button>
              </nav>
              <div
                className="recording-workspace"
                data-mobile-pane={mobilePane}
              >
                <section className="live-draft">
                  <header>
                    <h3>实时文字</h3>
                    <span>{draftStatus}</span>
                  </header>
                  {!recordOnly && draftEngine === "qwen" && streamEnabled && (
                    <div className="live-correction-bar">
                      <small>
                        {liveCorrection.status ||
                          (contextEnabled
                            ? "上下文辅助识别已开启 · 临时尾部可修订"
                            : "临时尾部可修订")}
                      </small>
                      <button
                        className="text-btn"
                        disabled={!correctionSessionId}
                        onClick={() => setCorrectionReviewOpen(true)}
                      >
                        纠错记录
                        {liveCorrection.corrections.filter(
                          (c) => c.state === "suggested",
                        ).length
                          ? ` · ${liveCorrection.corrections.filter((c) => c.state === "suggested").length} 待确认`
                          : ""}
                      </button>
                    </div>
                  )}
                  <LiveDraftList
                    active={mobilePane === "transcript"}
                    jump={jumpDraft}
                    drafts={displayDrafts}
                    canAddNote={recording}
                    partial={partialDraft}
                    emptyText={
                      recording &&
                      captureSource !== "browser" &&
                      (captureHealth?.stalled || nativeUnavailable)
                        ? "已保存的原音保留，当前采集状态异常；请检查上方提示。"
                        : recordOnly
                          ? "本次未开启实时识别，请关注下方音轨与文件状态。会后可配置模型生成文字稿。"
                          : responsiveMode &&
                              draftEngine === "qwen" &&
                              streamEnabled
                            ? "约 3 秒开始预览，后端根据识别速度调整更新节奏。"
                            : undefined
                    }
                    onAddNote={(draft) =>
                      setNotes((old) => [
                        ...old,
                        {
                          captured_at: draft.start,
                          note_type: "note",
                          raw_text: draft.text,
                          note_scope: "meeting",
                        },
                      ])
                    }
                  />
                </section>
                <section className="live-overview">
                  <LiveOverviewPanel
                    recordingId={correctionSessionId || ""}
                    engine={draftEngine}
                    active={recording && !recordOnly}
                    profiles={environment?.analysis.profiles || []}
                    onModels={onModels}
                    onLocate={(e) => {
                      setMobilePane("transcript");
                      setJumpDraft({ id: e.segment_id, nonce: Date.now() });
                    }}
                  />
                </section>
                <section className="live-notes">
                  <header>
                    <h3>我的简记</h3>
                    <span>
                      {notes.length ? `${notes.length} 条` : "记录此刻的关注"}
                    </span>
                  </header>
                  <div className="note-capture-actions">
                    <button
                      className="mark-now"
                      aria-label="标记此刻 Alt+M"
                      title="标记此刻（Alt+M）"
                      disabled={!recording}
                      onClick={() => markNow()}
                    >
                      ⚑ 标记
                    </button>
                    <div className="attention-quick-actions">
                      {(
                        [
                          ["highlight", "标重点"],
                          ["question", "记问题"],
                          ["action", "记待办"],
                          ["note", "随手记"],
                        ] as const
                      ).map(([type, label]) => (
                        <button
                          key={type}
                          disabled={!recording}
                          onClick={() => {
                            if (noteText.trim()) {
                              setNotes((old) => [
                                ...old,
                                {
                                  captured_at: Math.max(0, currentSeconds()),
                                  note_type: type,
                                  raw_text: noteText.trim(),
                                  note_scope: "personal",
                                },
                              ]);
                              setNoteText("");
                            } else markNow(type);
                          }}
                        >
                          {label}
                        </button>
                      ))}
                    </div>
                  </div>
                  <p className="note-purpose-hint">
                    随手记下关键词，系统会关联此刻的录音。
                  </p>
                  <div className="live-note-options" ref={noteOptionsRef}>
                    <button
                      className="note-options-trigger"
                      disabled={!recording}
                      aria-expanded={noteOptionsOpen}
                      onClick={() => setNoteOptionsOpen(!noteOptionsOpen)}
                    >
                      {noteLabels[noteType]} ·{" "}
                      {noteScope === "personal" ? "个人备注" : "会议内容"}{" "}
                      <span>调整用途</span>
                    </button>
                    {noteOptionsOpen && (
                      <div
                        className="note-options-popover"
                        role="group"
                        aria-label="简记标签与用途"
                      >
                        <select
                          aria-label="记录用途"
                          value={noteScope}
                          onChange={(e) =>
                            setNoteScope(e.target.value as typeof noteScope)
                          }
                          disabled={!recording}
                        >
                          <option value="personal">个人备注</option>
                          <option value="meeting">会议内容（待核对）</option>
                        </select>
                        <select
                          aria-label="简记类型"
                          value={noteType}
                          disabled={!recording}
                          onChange={(event) =>
                            setNoteType(event.target.value as MeetingNoteType)
                          }
                        >
                          {Object.entries(noteLabels).map(([value, label]) => (
                            <option value={value} key={value}>
                              {label}
                            </option>
                          ))}
                        </select>
                        <button onClick={() => setNoteOptionsOpen(false)}>
                          完成
                        </button>
                      </div>
                    )}
                  </div>
                  <div className="live-note-form">
                    <textarea
                      aria-label="会议简记"
                      placeholder={
                        recording
                          ? "只记关键词，系统会保留对应会议时间…"
                          : "开始录音后可记下重点"
                      }
                      value={noteText}
                      disabled={!recording}
                      onChange={(event) => setNoteText(event.target.value)}
                      onKeyDown={(event) => {
                        if (
                          (event.metaKey || event.ctrlKey) &&
                          event.key === "Enter"
                        )
                          addNote();
                      }}
                    />
                    <button
                      disabled={!recording || !noteText.trim()}
                      onClick={addNote}
                    >
                      保存关注点
                    </button>
                  </div>
                  <div
                    className={`live-note-list${!notes.length ? " empty" : ""}`}
                  >
                    {!notes.length && <p>⌘ / Ctrl + Enter 保存简记</p>}
                    {notes.map((note, index) => (
                      <article key={`${note.captured_at}-${index}`}>
                        <button
                          className="text-btn danger"
                          aria-label="删除简记"
                          disabled={!recording}
                          onClick={() =>
                            setNotes((old) => old.filter((_, i) => i !== index))
                          }
                        >
                          删除
                        </button>
                        <b>{clock(note.captured_at)}</b>
                        <span>{noteLabels[note.note_type]}</span>
                        <p>
                          {note.is_marker && !note.raw_text
                            ? "⚑ 时间标记 · 会后补充"
                            : note.raw_text}
                        </p>
                        <small>
                          {note.note_scope === "meeting"
                            ? "会议内容 · 待核对"
                            : "个人备注"}
                        </small>
                        {note.raw_text.trim() &&
                          note.note_scope !== "meeting" && (
                            <button
                              className="text-btn overview-focus-toggle"
                              disabled={!recording}
                              title="开启概览后，选入内容会发送至本场所选模型；仍保留个人备注用途"
                              onClick={() =>
                                setNotes((old) =>
                                  old.map((n, i) =>
                                    i === index ? toggleOverviewFocus(n) : n,
                                  ),
                                )
                              }
                            >
                              {note.overview_focus
                                ? "移出概览关注点"
                                : "用作概览关注点"}
                            </button>
                          )}
                        {note.note_scope === "meeting" && (
                          <small>可作为概览关注点 · 仍需原文依据</small>
                        )}
                      </article>
                    ))}
                  </div>
                </section>
              </div>
            </>
          )}
        </div>
      )}
      {recording && (
        <details className="workbench-bottom">
          <summary>亮屏与后台采集说明</summary>
          <div className="recording-awake-status">
            <label>
              <input
                type="checkbox"
                aria-label="录音时保持亮屏"
                checked={keepAwake}
                disabled={state === "requesting" || state === "saving"}
                onChange={(event) => setKeepAwake(event.target.checked)}
              />{" "}
              录音时保持亮屏
            </label>
            <span role="status">{awakeStatus}</span>
          </div>
          {captureSource !== "browser" && (
            <p className="muted">
              {sessionRef.current?.capture_transport === "broker_v2"
                ? "独立采集服务持续保存音轨，API 重启后可明确恢复控制；请返回此处主动结束。系统睡眠、设备断开或采集服务退出仍可能中断录音。"
                : "原生录音由本机服务持续采集，页面进入后台或关闭后仍会继续；请返回此处主动结束。系统睡眠、设备断开或服务退出仍可能中断录音。"}
            </p>
          )}
        </details>
      )}
      {correctionReviewOpen && correctionSessionId && (
        <LiveCorrectionReview
          recordingId={correctionSessionId}
          canDecide={state === "recording" || state === "paused"}
          drafts={drafts}
          profileId={correctionEnabled ? correctionProfileId : ""}
          onChange={liveCorrection.setCorrections}
          onClose={() => setCorrectionReviewOpen(false)}
        />
      )}
      {inspectorOpen && (
        <Modal
          className="recording-inspector"
          ariaLabel="录音运行详情"
          onClose={() => setInspectorOpen(false)}
        >
          <div className="search-heading">
            <div>
              <h2>运行详情</h2>
              <p>{coverageStatus}</p>
            </div>
            <button onClick={() => setInspectorOpen(false)}>关闭</button>
          </div>{" "}
          <p>{draftStatus}</p>
          {managedMode && (
            <p>
              {backendLive.connection} ·{" "}
              {draftEngine === "whisper"
                ? "Whisper 分段确认，不提供逐字预览"
                : "Qwen 支持临时预览与修订"}
            </p>
          )}
          {managedMode && draftEngine === "whisper" && decodeReport.load && (
            <p>
              Whisper 待处理尾段 {decodeReport.load.backlog_seconds.toFixed(1)}{" "}
              秒；按片段返回确认文字。
            </p>
          )}
          {managedMode && draftEngine === "qwen" && decodeReport.load && (
            <p>
              识别调度：
              {decodeReport.load.mode === "saving"
                ? "降低预览频率"
                : "常规预览"}
              · 下一轮至少增加 {decodeReport.load.preview_step_seconds} 秒音频 ·
              待处理尾段 {decodeReport.load.backlog_seconds.toFixed(1)} 秒
              <br />
              调整仅影响临时预览频率，原音照常保存；确认结果仍需完整识别。
            </p>
          )}
          {managedMode && decodeReport.boundary_reason && (
            <p>
              分段依据：
              {(
                {
                  silence: "检测到自然停顿",
                  sentence_pause: "句末标点附近检测到短停顿",
                  pause: "用户暂停录音",
                  stop: "录音结束收尾",
                  hard_limit: "达到 25 秒上限，前后段连续关联",
                  preview: "正在等待自然停顿",
                  standard: "标准分段",
                } as Record<string, string>
              )[decodeReport.boundary_reason] || decodeReport.boundary_reason}
            </p>
          )}
          {managedMode && decodeReport.decoder?.mode && (
            <p className="native-capture-hint">
              本轮
              {decodeReport.decoder.mode === "prefix"
                ? `保留 ${decodeReport.decoder.prefix_tokens || 0} 个前缀 token，续写尾部`
                : "完整识别窗口"}
              {decodeReport.decoder.feature_cache_hit
                ? " · 复用相同音频编码"
                : ""}
              {decodeReport.decoder.final_verified ? " · 确认前已完整复核" : ""}
              <br />
              累计前缀续写 {decodeReport.decoder_totals?.prefix_rounds || 0} 次
              · 编码缓存命中 {decodeReport.decoder_totals?.feature_hits || 0} 次
              · 回退 {decodeReport.decoder_totals?.fallbacks || 0} 次
            </p>
          )}
          {draftEngine === "qwen" && streamEnabled && (
            <p className="native-capture-hint">
              {liveMetrics.firstMs !== null
                ? `本次请求首字 ${(liveMetrics.firstMs / 1000).toFixed(2)}s · `
                : ""}
              处理 {(liveMetrics.processingMs / 1000).toFixed(2)}s ·
              临时尾部更新 {liveMetrics.revisions} 次（不含逐字追加）
              {liveMetrics.rtf !== null
                ? ` · RTF ${liveMetrics.rtf.toFixed(2)}`
                : ""}
              {liveMetrics.lagMs !== null
                ? ` · 确认滞后 ${(liveMetrics.lagMs / 1000).toFixed(1)}s`
                : ""}
            </p>
          )}
          {captureSource !== "browser" && captureHealth && (
            <div className="capture-health" role="status" aria-live="polite">
              {captureHealth.tracks.map((track) => (
                <p
                  key={track.name}
                  className={
                    ["no_data", "quiet", "stalled"].includes(track.state)
                      ? "capture-warning"
                      : ""
                  }
                >
                  <strong>
                    {track.name === "system" ? "电脑声音" : "麦克风"}
                  </strong>{" "}
                  ·{" "}
                  {
                    {
                      receiving: "正在接收音频",
                      waiting: "等待音频",
                      paused: "已暂停",
                      inactive: "已停止",
                      stalled: "采集状态超过 10 秒未更新，已有音轨保留",
                      no_data: "超过 10 秒未收到音频数据，请检查声音来源和权限",
                      quiet:
                        "连续 10 秒声音很弱，请确认会议正在播放或麦克风正常",
                    }[track.state]
                  }{" "}
                  · 已保存 {track.saved_seconds.toFixed(1)}s
                </p>
              ))}
              <small>
                静音提醒不会停止录音。保存时长包含时间线中的静音间隔，不能代表有效发言时长。
              </small>
            </div>
          )}
          {coverageDetails &&
            (coverageDetails.gaps.length > 0 ||
              coverageDetails.recent_failures.length > 0) && (
              <details className="capture-health">
                <summary>查看未覆盖范围及失败记录</summary>
                {coverageDetails.gaps.slice(0, 20).map((gap, index) => (
                  <p key={`gap-${index}`}>
                    {clock(gap.start)}–{clock(gap.end)} · 快速完成时补识别
                  </p>
                ))}
                {coverageDetails.recent_failures.map((attempt, index) => (
                  <p key={`attempt-${index}`}>
                    {clock(attempt.start)}–{clock(attempt.end)} ·{" "}
                    {attempt.state === "running"
                      ? "识别中"
                      : attempt.state === "preview"
                        ? "临时结果，未确认"
                        : "失败"}{" "}
                    {attempt.error}
                  </p>
                ))}
                <small>
                  历史失败尝试会保留；已重试成功的范围不会再次补识别。最多显示
                  20 个缺段及最近 50 条失败/进行中记录。
                </small>
              </details>
            )}
        </Modal>
      )}
      {settingsOpen && (
        <RecordingSettingsDialog
          autoProcess={autoProcess}
          brief={brief}
          briefReady={briefReady}
          briefSaved={briefSaved}
          captureSource={captureSource}
          checkCapture={checkCapture}
          checkingCapture={checkingCapture}
          completionMode={completionMode}
          contextEnabled={contextEnabled}
          controlModel={controlModel}
          correctionEnabled={correctionEnabled}
          correctionProfileId={correctionProfileId}
          draftEngine={draftEngine}
          engineLabel={engineLabel}
          engines={engines}
          environment={environment}
          flowReady={flowReady}
          meetings={meetings}
          minutesModel={minutesModel}
          modelActionBusy={modelActionBusy}
          modelStatus={modelStatus}
          nativeCapability={nativeCapability}
          onClose={onClose}
          onMinutesModel={onMinutesModel}
          onModels={onModels}
          postGoal={postGoal}
          prefixEnabled={prefixEnabled}
          recognitionTerms={recognitionTerms}
          responsiveMode={responsiveMode}
          setAutoProcess={setAutoProcess}
          setBrief={setBrief}
          setCaptureSource={setCaptureSource}
          setCompletionMode={setCompletionMode}
          setContextEnabled={setContextEnabled}
          setCorrectionEnabled={setCorrectionEnabled}
          setCorrectionProfileId={setCorrectionProfileId}
          setDraftEngine={setDraftEngine}
          setPostGoal={setPostGoal}
          setPrefixEnabled={setPrefixEnabled}
          setRecognitionTerms={setRecognitionTerms}
          setResponsiveMode={setResponsiveMode}
          setSettingsOpen={setSettingsOpen}
          setSettingsTab={setSettingsTab}
          setSilenceReminder={setSilenceReminder}
          setStreamEnabled={setStreamEnabled}
          settingsTab={settingsTab}
          silenceReminder={silenceReminder}
          state={state}
          streamEnabled={streamEnabled}
        />
      )}
    </Modal>
  );
}
