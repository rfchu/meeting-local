import {
  transcriptionOptions,
  type CompletionMode,
} from "./features/notes/recordingPreferences";
import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, download } from "./api";
import type {
  Meeting,
  MeetingMinutes,
  MinutesTemplate,
  MinutesStyle,
  Segment,
  Workspace,
} from "./types";
import {
  clone,
  clock,
  currentAnalysis,
  engineLabel,
  isRunning,
  latestJob,
  latestMinutes,
  revisionLabel,
} from "./utils";
import SearchDialog, { type SearchHit } from "./features/SearchDialog";
import Sidebar from "./components/Sidebar";
import Workflow from "./components/Workflow";
import Player from "./components/Player";
import {
  EnvironmentDialog,
  MinutesPreview,
  ModelConfigDialog,
  RenameDialog,
} from "./components/Dialogs";
import TranscriptView from "./features/transcript/TranscriptView";
import AnalysisView from "./features/analysis/AnalysisView";
import MinutesView from "./features/minutes/MinutesView";
import NotesView from "./features/notes/NotesView";
import RecordingDialog from "./features/notes/RecordingDialog";

export default function App() {
  const client = useQueryClient();
  const listQuery = useQuery({
    queryKey: ["meetings"],
    queryFn: api.meetings,
    refetchInterval: 2500,
  });
  const envQuery = useQuery({
    queryKey: ["environment"],
    queryFn: api.environment,
    refetchInterval: 15000,
  });
  const templatesQuery = useQuery({
    queryKey: ["minutes-templates"],
    queryFn: api.minutesTemplates,
  });
  const [meeting, setMeeting] = useState<Meeting | null>(null),
    [workspace, setWorkspace] = useState<Workspace>("transcript");
  const [transcriptDirty, setTranscriptDirty] = useState(false),
    [savingTranscript, setSavingTranscript] = useState(false);
  const [minutesDraft, setMinutesDraft] = useState<MeetingMinutes | null>(null),
    [minutesDirty, setMinutesDirty] = useState(false),
    [savingMinutes, setSavingMinutes] = useState(false);
  const [minutesStyle, setMinutesStyle] = useState<MinutesStyle>("formal"),
    [minutesTemplateId, setMinutesTemplateId] = useState("general-meeting"),
    [minutesMode, setMinutesMode] = useState<"quick" | "full">("quick"),
    [showCitations, setShowCitations] = useState(false);
  const [analysisModel, setAnalysisModel] = useState(""),
    [minutesModel, setMinutesModel] = useState(""),
    [generating, setGenerating] = useState(false),
    [fullPending, setFullPending] = useState(false);
  const [uploading, setUploading] = useState(false),
    [toastText, setToastText] = useState(""),
    [renameOpen, setRenameOpen] = useState(false),
    [environmentOpen, setEnvironmentOpen] = useState(false),
    [modelsOpen, setModelsOpen] = useState(false),
    [previewOpen, setPreviewOpen] = useState(false),
    [recordingOpen, setRecordingOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchTarget, setSearchTarget] = useState<SearchHit | null>(null);
  const [engine, setEngine] = useState("whisper"),
    [diarization, setDiarization] = useState(false),
    [numSpeakers, setNumSpeakers] = useState(0);
  const pausePlaybackRef = useRef<(() => void) | null>(null);
  const seekRef = useRef<((time: number) => void) | null>(null),
    toastTimer = useRef<number | undefined>(undefined),
    meetingRef = useRef<Meeting | null>(null);
  const transcriptGeneration = useRef(0),
    minutesGeneration = useRef(0);
  meetingRef.current = meeting;
  const toast = useCallback((text: string) => {
    setToastText(text);
    window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToastText(""), 5000);
  }, []);

  useEffect(() => {
    const env = envQuery.data;
    if (!env) return;
    const value =
      env.analysis.default_profile_id || env.analysis.models[0] || "";
    setAnalysisModel((old) => old || value);
    setMinutesModel((old) => old || value);
    if (env.diarization_ready && !meeting) setDiarization(true);
  }, [envQuery.data, meeting]);
  useEffect(() => {
    const handler = (event: BeforeUnloadEvent) => {
      if (transcriptDirty || savingTranscript || minutesDirty || savingMinutes)
        event.preventDefault();
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [transcriptDirty, savingTranscript, minutesDirty, savingMinutes]);

  useEffect(() => {
    if (!searchTarget || meeting?.id !== searchTarget.meeting_id) return;
    const timer = window.setTimeout(() => {
      if (searchTarget.start !== null) seekRef.current?.(searchTarget.start);
      setSearchTarget(null);
    }, 100);
    return () => window.clearTimeout(timer);
  }, [searchTarget, meeting?.id]);

  const saveTranscript = useCallback(async () => {
    const target = meetingRef.current;
    if (!target || !transcriptDirty || savingTranscript) return;
    const generation = transcriptGeneration.current;
    setSavingTranscript(true);
    try {
      const result = await api.saveTranscript(
        target.id,
        target.revision,
        clone(target.segments),
      );
      setMeeting((old) =>
        old?.id === target.id
          ? {
              ...old,
              revision: result.revision,
              revisions: [
                {
                  revision: result.revision,
                  created: new Date().toISOString(),
                  source: "manual",
                },
                ...old.revisions,
              ],
            }
          : old,
      );
      if (transcriptGeneration.current === generation)
        setTranscriptDirty(false);
    } catch (error) {
      toast(`保存失败：${(error as Error).message}`);
      throw error;
    } finally {
      setSavingTranscript(false);
    }
  }, [savingTranscript, toast, transcriptDirty]);
  useEffect(() => {
    if (!transcriptDirty) return;
    const timer = window.setTimeout(() => void saveTranscript(), 700);
    return () => window.clearTimeout(timer);
  }, [transcriptDirty, meeting?.segments, saveTranscript]);

  const saveMinutes = useCallback(async () => {
    const target = meetingRef.current;
    if (!target || !minutesDraft || !minutesDirty || savingMinutes) return;
    const generation = minutesGeneration.current;
    setSavingMinutes(true);
    try {
      const source =
        latestMinutes(target)?.provenance?.source_analysis_id ||
        currentAnalysis(target)?.id ||
        null;
      const artifact = await api.saveMinutes(
        target.id,
        String(source || "") || null,
        clone(minutesDraft),
      );
      setMeeting((old) =>
        old?.id === target.id
          ? { ...old, artifacts: [artifact, ...old.artifacts] }
          : old,
      );
      if (minutesGeneration.current === generation) setMinutesDirty(false);
    } catch (error) {
      toast(`纪要保存失败：${(error as Error).message}`);
      throw error;
    } finally {
      setSavingMinutes(false);
    }
  }, [minutesDirty, minutesDraft, savingMinutes, toast]);
  useEffect(() => {
    if (!minutesDirty) return;
    const timer = window.setTimeout(() => void saveMinutes(), 900);
    return () => window.clearTimeout(timer);
  }, [minutesDirty, minutesDraft, saveMinutes]);

  const openMeeting = async (id: string) => {
    try {
      await saveTranscript();
      await saveMinutes();
      const value = await api.meeting(id);
      setMeeting(value);
      setTranscriptDirty(false);
      const artifact = latestMinutes(value);
      setMinutesDraft(artifact ? clone(artifact.payload) : null);
      setMinutesDirty(false);
      setMinutesStyle(artifact?.payload.style || "formal");
      setMinutesTemplateId(artifact?.payload.template_id || "general-meeting");
      setShowCitations(artifact?.payload.style === "internal");
      setWorkspace("transcript");
      setFullPending(false);
      meetingRef.current = value;
      const job = latestJob(value, "transcription");
      try {
        const config = JSON.parse(job?.config || "{}");
        setEngine(config.engine || "whisper");
        setDiarization(!!config.diarization);
        setNumSpeakers(config.num_speakers || 0);
      } catch {
        setEngine("whisper");
      }
      return value;
    } catch (error) {
      toast((error as Error).message);
      return null;
    }
  };

  useEffect(() => {
    if (!meeting) return;
    const poll = window.setInterval(async () => {
      try {
        const fresh = await api.meeting(meeting.id);
        setMeeting((old) => {
          if (!old || old.id !== fresh.id) return old;
          if (!transcriptDirty && fresh.revision !== old.revision) {
            const artifact = latestMinutes(fresh);
            setMinutesDraft(artifact ? clone(artifact.payload) : null);
            setMinutesDirty(false);
            return fresh;
          }
          return {
            ...old,
            notes: fresh.notes,
            note_todos: fresh.note_todos,
            jobs: fresh.jobs,
            artifacts: fresh.artifacts,
            revisions: fresh.revisions,
            duration: fresh.duration,
          };
        });
      } catch {
        /* next poll retries */
      }
    }, 2500);
    return () => window.clearInterval(poll);
  }, [meeting?.id, transcriptDirty]);

  const upload = async (file: File) => {
    setUploading(true);
    try {
      toast("正在将录音保存到本机…");
      const value = await api.upload(file);
      await client.invalidateQueries({ queryKey: ["meetings"] });
      await openMeeting(value.id);
      toast("录音已导入，可以开始转写");
    } catch (error) {
      toast((error as Error).message);
    } finally {
      setUploading(false);
    }
  };
  const completeRecording = async (
    meetingId: string,
    options: { mode: CompletionMode; engine: "whisper" | "qwen" },
  ) => {
    setUploading(true);
    let created: Meeting | null = null;
    const followUpErrors: string[] = [];
    try {
      created = await api.meeting(meetingId);
      try {
        const plan = transcriptionOptions(
          options.mode,
          options.engine,
          engine,
          diarization,
          numSpeakers,
        );
        if (plan) await api.transcribe(created.id, plan);
      } catch (error) {
        followUpErrors.push(`转写未启动：${(error as Error).message}`);
      }
      await client.invalidateQueries({ queryKey: ["meetings"] });
      setRecordingOpen(false);
      await openMeeting(created.id);
      setWorkspace("notes");
      toast(
        followUpErrors.length
          ? `录音已保存；${followUpErrors.join("；")}`
          : options.mode === "none"
            ? `录音和 ${created.notes.length} 条简记已保存，可稍后转写`
            : `录音和 ${created.notes.length} 条简记已保存，文字稿正在本机生成`,
      );
    } catch (error) {
      toast((error as Error).message);
      if (!created) throw error;
    } finally {
      setUploading(false);
    }
  };
  const updateSegments = (segments: Segment[]) => {
    transcriptGeneration.current++;
    setMeeting((old) => (old ? { ...old, segments } : old));
    setTranscriptDirty(true);
  };
  const startTranscription = async () => {
    if (!meeting) return;
    try {
      await saveTranscript();
      if (
        meeting.revision &&
        !confirm(
          "重新转写会生成新的模型文稿版本。现有校对稿保留在历史版本中，继续吗？",
        )
      )
        return;
      await api.transcribe(meeting.id, {
        engine,
        diarization,
        num_speakers: numSpeakers,
      });
      setMeeting(await api.meeting(meeting.id));
    } catch (error) {
      toast((error as Error).message);
    }
  };
  const jobAction = async (id: string, action: "cancel" | "retry") => {
    try {
      await api.jobAction(id, action);
      if (meeting) setMeeting(await api.meeting(meeting.id));
    } catch (error) {
      toast((error as Error).message);
    }
  };
  const generateAnalysis = async () => {
    if (!meeting || !analysisModel) return toast("请先配置分析模型");
    try {
      await saveTranscript();
      const profiles = envQuery.data?.analysis.profiles || [];
      await api.analyze(
        meeting.id,
        profiles.length
          ? { profile_id: analysisModel }
          : { model: analysisModel },
      );
      setWorkspace("analysis");
      setMeeting(await api.meeting(meeting.id));
    } catch (error) {
      toast((error as Error).message);
    }
  };
  const generateMinutes = useCallback(
    async (
      forceMode?: "quick" | "full",
      forceStyle?: MinutesStyle,
      skipSave = false,
      forceTemplateId?: string,
    ) => {
      const target = meetingRef.current;
      if (!target) return;
      const mode = forceMode || minutesMode,
        selectedStyle = forceStyle || minutesStyle,
        selectedTemplateId = forceTemplateId || minutesTemplateId;
      try {
        if (!skipSave) await saveMinutes();
        setGenerating(true);
        let artifact;
        if (selectedStyle === "ai") {
          if (!minutesModel) throw new Error("请先选择纪要生成模型");
          const profiles = envQuery.data?.analysis.profiles || [];
          artifact = await api.generateAiMinutes(
            target.id,
            profiles.length
              ? {
                  profile_id: minutesModel,
                  mode,
                  template_id: selectedTemplateId,
                }
              : { model: minutesModel, mode, template_id: selectedTemplateId },
          );
        } else
          artifact = await api.generateMinutes(
            target.id,
            selectedStyle,
            selectedTemplateId,
          );
        setMeeting((old) =>
          old?.id === target.id
            ? { ...old, artifacts: [artifact, ...old.artifacts] }
            : old,
        );
        setMinutesDraft(clone(artifact.payload));
        setMinutesDirty(false);
        setMinutesStyle(artifact.payload.style);
        setMinutesTemplateId(artifact.payload.template_id || "general-meeting");
        setWorkspace("minutes");
        toast(
          selectedStyle === "ai"
            ? mode === "full"
              ? "完整处理完成：会议分析和 AI 纪要均已生成"
              : "AI 会议纪要已直接基于当前文字稿生成"
            : "会议纪要已生成，可直接编辑后导出",
        );
      } catch (error) {
        toast((error as Error).message);
      } finally {
        setGenerating(false);
      }
    },
    [
      envQuery.data?.analysis.profiles,
      minutesMode,
      minutesModel,
      minutesStyle,
      minutesTemplateId,
      saveMinutes,
      toast,
    ],
  );
  const startMinutes = async () => {
    if (!meeting) return;
    if (
      minutesStyle === "ai" &&
      minutesMode === "full" &&
      !currentAnalysis(meeting)
    ) {
      setFullPending(true);
      await generateAnalysis();
      toast("完整处理已开始：会议分析完成后将自动生成 AI 纪要");
      return;
    }
    await generateMinutes();
  };
  const changeMinutesStyle = async (value: MinutesStyle) => {
    if (!meeting) return;
    await saveMinutes();
    setMinutesStyle(value);
    if (value === "internal") setShowCitations(true);
    const matching = meeting.artifacts.find(
      (item) =>
        item.kind === "meeting_minutes" &&
        item.revision === meeting.revision &&
        (item.payload as MeetingMinutes).style === value &&
        ((item.payload as MeetingMinutes).template_id || "general-meeting") ===
          minutesTemplateId,
    );
    setMinutesDraft(
      matching ? clone(matching.payload as MeetingMinutes) : null,
    );
    setMinutesDirty(false);
    if (value !== "ai" && currentAnalysis(meeting))
      await generateMinutes(undefined, value, true);
  };
  const changeMinutesTemplate = async (value: string) => {
    if (!meeting) return;
    await saveMinutes();
    setMinutesTemplateId(value);
    const matching = meeting.artifacts.find(
      (item) =>
        item.kind === "meeting_minutes" &&
        item.revision === meeting.revision &&
        (item.payload as MeetingMinutes).style === minutesStyle &&
        ((item.payload as MeetingMinutes).template_id || "general-meeting") ===
          value,
    );
    setMinutesDraft(
      matching ? clone(matching.payload as MeetingMinutes) : null,
    );
    setMinutesDirty(false);
    if (minutesStyle !== "ai" && currentAnalysis(meeting))
      await generateMinutes(undefined, minutesStyle, true, value);
  };
  useEffect(() => {
    if (
      fullPending &&
      meeting &&
      currentAnalysis(meeting) &&
      !isRunning(latestJob(meeting, "analysis")) &&
      !generating
    ) {
      setFullPending(false);
      setMinutesStyle("ai");
      setWorkspace("minutes");
      void generateMinutes("full");
    }
  }, [fullPending, meeting, generating, generateMinutes]);

  const exportMinutes = async (format: "docx" | "pdf") => {
    await saveMinutes();
    download(
      `/meetings/${meeting!.id}/minutes/export/${format}?include_citations=${showCitations}`,
    );
  };
  const openMinutesPreview = async () => {
    await saveMinutes();
    setPreviewOpen(true);
  };
  const environment = envQuery.data,
    transcriptionJob = latestJob(meeting, "transcription"),
    transcriptionRunning = isRunning(transcriptionJob),
    engineReady = !!environment?.engines.find((item) => item.id === engine)
      ?.ready;
  const candidateArtifacts =
    meeting?.artifacts.filter((item) => item.kind === "transcript_candidate") ||
    [];
  return (
    <>
      <Sidebar
        meetings={listQuery.data || []}
        currentId={meeting?.id}
        uploading={uploading}
        onSelect={(id) => void openMeeting(id)}
        onUpload={(file) => void upload(file)}
        onRecord={() => setRecordingOpen(true)}
        onModels={() => setModelsOpen(true)}
        onSearch={() => setSearchOpen(true)}
        onEnvironment={() => setEnvironmentOpen(true)}
      />
      <main>
        <header>
          <div className="breadcrumb">
            工作空间 <span>/</span> 会议转写
          </div>
          <div className="local-pill">
            <span className="dot" /> 转写本地处理 · 分析引擎可配置
          </div>
        </header>
        {!meeting ? (
          <Welcome
            onUpload={(file) => void upload(file)}
            onRecord={() => setRecordingOpen(true)}
          />
        ) : (
          <section id="detail">
            <div className="title-row">
              <div>
                <div className="eyebrow">
                  会议资料 / {meeting.created.slice(0, 10)}
                </div>
                <h1>{meeting.title}</h1>
              </div>
              <button
                className="text-btn danger"
                onClick={async () => {
                  if (
                    !confirm(
                      "删除此会议的录音、所有文稿版本和分析结果？此操作无法撤销。",
                    )
                  )
                    return;
                  try {
                    await api.remove(meeting.id);
                    setMeeting(null);
                    await client.invalidateQueries({ queryKey: ["meetings"] });
                  } catch (error) {
                    toast((error as Error).message);
                  }
                }}
              >
                删除会议
              </button>
            </div>
            <div className="action-row">
              <label className="engine-field">
                识别引擎{" "}
                <select
                  value={engine}
                  disabled={transcriptionRunning}
                  onChange={(e) => setEngine(e.target.value)}
                >
                  {environment?.engines.map((item) => (
                    <option
                      value={item.id}
                      disabled={!item.ready}
                      key={item.id}
                    >
                      {item.label}
                      {item.ready ? "" : " · 待安装"}
                    </option>
                  )) || (
                    <>
                      <option value="whisper">Whisper large-v3-turbo</option>
                      <option value="qwen">千问 Qwen3-ASR-1.7B</option>
                    </>
                  )}
                </select>
              </label>
              <label className="check">
                <input
                  type="checkbox"
                  checked={diarization}
                  disabled={!environment?.diarization_ready}
                  onChange={(e) => setDiarization(e.target.checked)}
                />{" "}
                自动区分发言人
              </label>
              <select
                value={numSpeakers}
                onChange={(e) => setNumSpeakers(Number(e.target.value))}
              >
                {[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map((value) => (
                  <option value={value} key={value}>
                    {value ? `${value} 位发言人` : "人数自动估计"}
                  </option>
                ))}
              </select>
              <button
                className="primary"
                disabled={transcriptionRunning || !engineReady}
                onClick={() => void startTranscription()}
              >
                {meeting.revision ? "重新转写" : "开始转写"}
              </button>
              <button onClick={() => setRenameOpen(true)}>重命名</button>
              <select
                value=""
                aria-label="导出格式"
                onChange={async (e) => {
                  const format = e.target.value;
                  if (!format) return;
                  await saveTranscript();
                  download(`/meetings/${meeting.id}/export/${format}`);
                }}
              >
                <option value="">导出文字稿 ↓</option>
                <option value="txt">纯文本 TXT</option>
                <option value="md">Markdown</option>
                <option value="srt">字幕 SRT</option>
                <option value="json">结构化 JSON</option>
              </select>
            </div>
            {transcriptionJob && (
              <div id="jobBox">
                <div className="job-head">
                  <span>
                    {engineLabel(
                      JSON.parse(transcriptionJob.config || "{}").engine,
                    )}{" "}
                    · {transcriptionJob.stage}
                  </span>
                  {(transcriptionRunning ||
                    ["failed", "cancelled"].includes(
                      transcriptionJob.state,
                    )) && (
                    <button
                      className="text-btn"
                      onClick={() =>
                        void jobAction(
                          transcriptionJob.id,
                          transcriptionRunning ? "cancel" : "retry",
                        )
                      }
                    >
                      {transcriptionRunning ? "取消任务" : "从检查点重试"}
                    </button>
                  )}
                </div>
                <progress max={1} value={transcriptionJob.progress} />
                <pre id="jobError">{transcriptionJob.error}</pre>
              </div>
            )}
            <Workflow
              meeting={meeting}
              transcriptDirty={transcriptDirty}
              onTranscribe={() => void startTranscription()}
              onSave={() => void saveTranscript()}
              onAnalysis={() => setWorkspace("analysis")}
              onMinutes={() => setWorkspace("minutes")}
              onPreview={() => void openMinutesPreview()}
            />
            <div className="workspace">
              <div className="transcript-panel">
                <div className="panel-header">
                  <div className="tabs">
                    {(
                      [
                        ["transcript", "会议文字稿"],
                        [
                          "notes",
                          `会议简记${meeting.notes.length ? ` · ${meeting.notes.length}` : ""}`,
                        ],
                        ["analysis", "会议分析"],
                        ["minutes", "会议纪要"],
                      ] as [Workspace, string][]
                    ).map(([key, label]) => (
                      <button
                        className={`tab${workspace === key ? " active" : ""}`}
                        key={key}
                        onClick={() => setWorkspace(key)}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                  {workspace === "transcript" && (
                    <button
                      className="text-btn"
                      onClick={() => void saveTranscript()}
                    >
                      {savingTranscript
                        ? "保存中…"
                        : transcriptDirty
                          ? "正在编辑…"
                          : meeting.revision
                            ? `已保存 · v${meeting.revision}`
                            : "尚无文稿"}
                    </button>
                  )}
                </div>
                {workspace === "transcript" && (
                  <TranscriptView
                    meeting={meeting}
                    onChange={updateSegments}
                    onSeek={(time) => seekRef.current?.(time)}
                    onSave={saveTranscript}
                    onRefresh={async () => {
                      const fresh = await api.meeting(meeting.id);
                      setMeeting(fresh);
                      setTranscriptDirty(false);
                      await client.invalidateQueries({
                        queryKey: ["meetings"],
                      });
                    }}
                    toast={toast}
                  />
                )}
                {workspace === "analysis" && (
                  <AnalysisView
                    meeting={meeting}
                    environment={environment}
                    model={analysisModel}
                    onModel={setAnalysisModel}
                    onGenerate={() => void generateAnalysis()}
                    onConfigure={() => setModelsOpen(true)}
                    onJobAction={(id, action) => void jobAction(id, action)}
                    onSeek={(time) => seekRef.current?.(time)}
                    transcriptDirty={transcriptDirty}
                  />
                )}
                {workspace === "notes" && (
                  <NotesView
                    meeting={meeting}
                    onRefresh={async () => {
                      const fresh = await api.meeting(meeting.id);
                      setMeeting(fresh);
                      if (!minutesDirty) {
                        const artifact = latestMinutes(fresh);
                        setMinutesDraft(
                          artifact ? clone(artifact.payload) : null,
                        );
                      }
                    }}
                    minutesDirty={minutesDirty}
                    onPause={() => pausePlaybackRef.current?.()}
                    onSeek={(time) => seekRef.current?.(time)}
                    toast={toast}
                  />
                )}
                {workspace === "minutes" && (
                  <MinutesView
                    meeting={meeting}
                    environment={environment}
                    draft={minutesDraft}
                    dirty={minutesDirty}
                    transcriptDirty={transcriptDirty}
                    style={minutesStyle}
                    templates={(templatesQuery.data || []) as MinutesTemplate[]}
                    templateId={minutesTemplateId}
                    mode={minutesMode}
                    model={minutesModel}
                    showCitations={showCitations}
                    generating={generating || fullPending}
                    onStyle={(value) => void changeMinutesStyle(value)}
                    onTemplate={(value) => void changeMinutesTemplate(value)}
                    onMode={setMinutesMode}
                    onModel={setMinutesModel}
                    onCitations={setShowCitations}
                    onGenerate={() => void startMinutes()}
                    onDraft={(value) => {
                      minutesGeneration.current++;
                      setMinutesDraft(value);
                      setMinutesDirty(true);
                    }}
                    onSave={() => void saveMinutes()}
                    onPreview={() => void openMinutesPreview()}
                    onSeek={(time) => seekRef.current?.(time)}
                  />
                )}
              </div>
              <div className="info-panel">
                <div className="eyebrow">会议概览</div>
                <dl>
                  <dt>录音时长</dt>
                  <dd>
                    {meeting.duration ? clock(meeting.duration) : "转写后显示"}
                  </dd>
                  <dt>文字片段</dt>
                  <dd>{meeting.segments.length}</dd>
                  <dt>当前版本</dt>
                  <dd>
                    {meeting.revision ? `v${meeting.revision}` : "尚未生成"}
                  </dd>
                  <dt>最近识别引擎</dt>
                  <dd>
                    {revisionLabel(
                      meeting.revisions.find((item) => item.source !== "manual")
                        ?.source,
                    )}
                  </dd>
                  <dt>转写方式</dt>
                  <dd>本机离线</dd>
                </dl>
                <div className="tip">
                  <b>校对小提示</b>
                  <p>
                    点击时间戳回听原音；直接编辑文字即可自动保存。发言人名称可逐段修改或批量合并。
                  </p>
                </div>
                {candidateArtifacts.map((item) => (
                  <div key={item.id}>
                    <p>重新转写期间文稿有修改，识别结果已作为候选稿保留。</p>
                    <button
                      onClick={() => {
                        const link = document.createElement("a");
                        link.href = URL.createObjectURL(
                          new Blob([JSON.stringify(item.payload, null, 2)], {
                            type: "application/json",
                          }),
                        );
                        link.download = "候选转写.json";
                        link.click();
                      }}
                    >
                      查看候选稿
                    </button>
                  </div>
                ))}
              </div>
            </div>
            <Player
              meetingId={meeting.id}
              segments={meeting.segments}
              seekRef={seekRef}
              pauseRef={pausePlaybackRef}
              audioTracks={meeting.audio_tracks}
            />
          </section>
        )}
      </main>
      {searchOpen && (
        <SearchDialog
          meetings={listQuery.data || []}
          onClose={() => setSearchOpen(false)}
          onOpen={async (hit) => {
            const current = await api.resolveSearch(hit);
            const opened = await openMeeting(current.meeting_id);
            if (!opened) throw new Error("会议未能打开，请重试");
            await api.resolveSearch(hit);
            setSearchOpen(false);
            setSearchTarget(current);
          }}
        />
      )}
      {toastText && (
        <div id="toast" role="status">
          {toastText}
        </div>
      )}
      {renameOpen && meeting && (
        <RenameDialog
          title={meeting.title}
          onClose={() => setRenameOpen(false)}
          onSave={async (title) => {
            await api.rename(meeting.id, title);
            setMeeting((old) => (old ? { ...old, title } : old));
            setRenameOpen(false);
            await client.invalidateQueries({ queryKey: ["meetings"] });
            toast("会议名称已更新");
          }}
        />
      )}
      {environmentOpen && environment && (
        <EnvironmentDialog
          environment={environment}
          onClose={() => setEnvironmentOpen(false)}
        />
      )}
      {modelsOpen && (
        <ModelConfigDialog
          onClose={() => setModelsOpen(false)}
          onChanged={() =>
            void client.invalidateQueries({ queryKey: ["environment"] })
          }
          toast={toast}
        />
      )}
      {previewOpen && minutesDraft && (
        <MinutesPreview
          meetingId={meeting!.id}
          includeCitations={showCitations}
          onClose={() => setPreviewOpen(false)}
          onExport={(format) => void exportMinutes(format)}
        />
      )}
      {recordingOpen && (
        <RecordingDialog
          engineLabel={engineLabel(engine)}
          engines={environment?.engines || []}
          onClose={() => setRecordingOpen(false)}
          onComplete={completeRecording}
          onModels={() => setModelsOpen(true)}
          toast={toast}
        />
      )}
    </>
  );
}

function Welcome({
  onUpload,
  onRecord,
}: {
  onUpload: (file: File) => void;
  onRecord: () => void;
}) {
  const [dragging, setDragging] = useState(false);
  return (
    <section id="welcome">
      <div className="eyebrow">把讨论，留成清晰的记录</div>
      <h1>
        让每一次会议
        <br />
        都有迹可循。
      </h1>
      <p className="intro">
        实时记录会议，或导入已有录音，生成带时间戳的文字稿。
        <br />
        回听、校对、整理，全部在你的 Mac 上完成。
      </p>
      <button
        id="dropzone"
        className={dragging ? "drag" : ""}
        onClick={() => document.getElementById("file")?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          const file = e.dataTransfer.files[0];
          if (file) onUpload(file);
        }}
      >
        <span className="upload-icon">↥</span>
        <strong>拖放录音到这里，或点击选择</strong>
        <span>WAV / MP3 / M4A / MP4 等 · 最大 4GB</span>
      </button>
      <button className="welcome-record" onClick={onRecord}>
        <span>●</span>
        <strong>直接开始会议录音</strong>
        <small>录音时可随手记下重点、问题、决策和待办</small>
      </button>
      <div className="features">
        <div>
          <b>01</b>
          <h3>本地转写</h3>
          <p>普通话优先，支持长会议分段处理</p>
        </div>
        <div>
          <b>02</b>
          <h3>逐句校对</h3>
          <p>点击时间戳，回到原声确认细节</p>
        </div>
        <div>
          <b>03</b>
          <h3>随时导出</h3>
          <p>文字稿、字幕与结构化数据</p>
        </div>
      </div>
    </section>
  );
}
