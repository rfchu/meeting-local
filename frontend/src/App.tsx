import {
  readMinutesOptions,
  persistMinutesOptions,
  type MinutesOptions,
} from "./features/minutes/composition";
import MinutesCompare from "./features/minutes/MinutesCompare";
import { useModelChoice } from "./preferences/useModelChoice";
import { resolveModelChoice } from "./preferences/modelChoices";
import FollowupsDialog from "./features/meeting/FollowupsDialog";
import MeetingPreparation from "./features/meeting/MeetingPreparation";
import MeetingOutcomes from "./features/meeting/MeetingOutcomes";
import {
  transcriptionOptions,
  type CompletionMode,
} from "./features/notes/recordingPreferences";
import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, download } from "./api";
import type {
  Meeting,
  Artifact,
  ReviewStep,
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
import { recoverableRecordings } from "./features/notes/recordingBuffer";

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
  const [minutesOptions, setMinutesOptions] =
    useState<MinutesOptions>(readMinutesOptions);
  useEffect(() => {
    const templates = templatesQuery.data;
    if (
      templates?.length &&
      !templates.some((t) => t.id === minutesOptions.template_id)
    ) {
      const next = { ...minutesOptions, template_id: templates[0].id };
      setMinutesOptions(next);
      persistMinutesOptions(next);
    }
  }, [templatesQuery.data, minutesOptions]);
  const [withAnalysis, setWithAnalysis] = useState(false);
  const [selectedMinutesId, setSelectedMinutesId] = useState<string | null>(
    null,
  );
  const selectedMinutesRef = useRef<string | null>(null);
  const minutesDraftRef = useRef<{
    draft: MeetingMinutes | null;
    dirty: boolean;
  }>({ draft: null, dirty: false });
  const minutesSaveTask = useRef<Promise<
    Artifact<MeetingMinutes> | undefined
  > | null>(null);
  const [compareIds, setCompareIds] = useState<[string, string] | null>(null);
  const [showCitations, setShowCitations] = useState(false);
  const changeMinutesOptions = (value: MinutesOptions) => {
    setMinutesOptions(value);
    persistMinutesOptions(value);
  };
  const displayMinutes = (artifact: Artifact<MeetingMinutes> | null) => {
    selectedMinutesRef.current = artifact?.id || null;
    setSelectedMinutesId(artifact?.id || null);
    minutesDraftRef.current = {
      draft: artifact ? clone(artifact.payload) : null,
      dirty: false,
    };
    setMinutesDraft(minutesDraftRef.current.draft);
    setMinutesDirty(false);
  };
  const [analysisModel, setAnalysisModel] = useModelChoice("analysis"),
    [minutesModel, setMinutesModel] = useModelChoice("minutes"),
    [generating, setGenerating] = useState(false);
  const [uploading, setUploading] = useState(false),
    [toastText, setToastText] = useState(""),
    [renameOpen, setRenameOpen] = useState(false),
    [environmentOpen, setEnvironmentOpen] = useState(false),
    [modelsOpen, setModelsOpen] = useState(false),
    [previewOpen, setPreviewOpen] = useState(false),
    [recordingOpen, setRecordingOpen] = useState(false);
  const recoveryQuery = useQuery({
    queryKey: ["recording-recoveries"],
    queryFn: recoverableRecordings,
    refetchInterval: 30000,
    enabled: !recordingOpen,
  });
  const [reviewBusy, setReviewBusy] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [followupsOpen, setFollowupsOpen] = useState(false);
  const [searchTarget, setSearchTarget] = useState<SearchHit | null>(null);
  const [engine, setEngine] = useModelChoice("transcription"),
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
    const choices = env.analysis.profiles.length
      ? env.analysis.profiles.map((p) => p.id)
      : env.analysis.models;
    if (choices.length) {
      setAnalysisModel(resolveModelChoice(analysisModel, choices, value));
      setMinutesModel(resolveModelChoice(minutesModel, choices, value));
    }
    setEngine(
      resolveModelChoice(
        engine,
        env.engines.map((item) => item.id),
        env.default_engine || "whisper",
      ),
    );
    if (env.diarization_ready && !meeting) setDiarization(true);
  }, [envQuery.data, meeting, analysisModel, minutesModel, engine]);
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

  const navigateReview = (step: ReviewStep) => {
    setWorkspace(step === "speakers" ? "transcript" : step);
    window.requestAnimationFrame(() =>
      document
        .getElementById("meeting-workspace")
        ?.scrollIntoView({ behavior: "smooth", block: "start" }),
    );
    if (step === "speakers")
      toast("可在文字稿上方管理发言人，或运行 AI 校对后逐项确认");
  };
  const confirmReview = async (
    step: ReviewStep,
    decision: "confirmed" | "skipped" | "reset",
  ) => {
    const target = meetingRef.current;
    if (
      !target ||
      reviewBusy ||
      transcriptDirty ||
      (step === "minutes" && minutesDirty)
    )
      return;
    const checkpoint = target.review?.steps[step];
    if (!checkpoint) return;
    setReviewBusy(true);
    try {
      const review =
        decision === "reset"
          ? await api.resetReview(target.id, step)
          : await api.confirmReview(
              target.id,
              step,
              checkpoint.fingerprint,
              decision,
            );
      setMeeting((old) => (old?.id === target.id ? { ...old, review } : old));
      toast(decision === "reset" ? "已撤回核对记录" : "核对进度已保存");
    } catch (error) {
      toast((error as Error).message);
      const fresh = await api.meeting(target.id).catch(() => null);
      if (fresh)
        setMeeting((old) =>
          old?.id === target.id ? { ...old, review: fresh.review } : old,
        );
    } finally {
      setReviewBusy(false);
    }
  };
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

  const saveMinutes = useCallback(
    async function persist(): Promise<Artifact<MeetingMinutes> | undefined> {
      if (minutesSaveTask.current) {
        const saved = await minutesSaveTask.current;
        return minutesDraftRef.current.dirty ? persist() : saved;
      }
      const target = meetingRef.current;
      const snapshot = minutesDraftRef.current;
      if (!target || !snapshot.draft || !snapshot.dirty) return;
      const document = snapshot.draft;
      const generation = minutesGeneration.current;
      setSavingMinutes(true);
      const task = (async () => {
        try {
          const baseId = selectedMinutesRef.current;
          const base = target.artifacts.find((a) => a.id === baseId);
          const source =
            base?.provenance?.source_analysis_id ||
            currentAnalysis(target)?.id ||
            null;
          const artifact = await api.saveMinutes(
            target.id,
            String(source || "") || null,
            clone(document),
            baseId || undefined,
          );
          setMeeting((old) =>
            old?.id === target.id
              ? {
                  ...old,
                  active_minutes_id:
                    old.active_minutes_id === baseId &&
                    !artifact.provenance.draft
                      ? artifact.id
                      : old.active_minutes_id,
                  artifacts: [
                    artifact,
                    ...old.artifacts.filter((a) => a.id !== artifact.id),
                  ],
                }
              : old,
          );
          if (
            meetingRef.current?.id === target.id &&
            selectedMinutesRef.current === baseId
          ) {
            selectedMinutesRef.current = artifact.id;
            setSelectedMinutesId(artifact.id);
          }
          if (minutesGeneration.current === generation) {
            minutesDraftRef.current = {
              ...minutesDraftRef.current,
              dirty: false,
            };
            setMinutesDirty(false);
          }
          return artifact;
        } catch (error) {
          toast(`纪要保存失败：${(error as Error).message}`);
          throw error;
        } finally {
          setSavingMinutes(false);
        }
      })();
      minutesSaveTask.current = task;
      let saved;
      try {
        saved = await task;
      } finally {
        minutesSaveTask.current = null;
      }
      return minutesDraftRef.current.dirty ? persist() : saved;
    },
    [toast],
  );
  useEffect(() => {
    if (!minutesDirty) return;
    const timer = window.setTimeout(
      () => void saveMinutes().catch(() => {}),
      900,
    );
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
      displayMinutes(artifact || null);
      if (!artifact && minutesOptions.method === "current")
        setMinutesOptions((old) => ({ ...old, method: "ai" }));
      setShowCitations(artifact?.payload.style === "internal");
      setWorkspace(latestMinutes(value) ? "minutes" : "notes");

      meetingRef.current = value;
      const job = latestJob(value, "transcription");
      try {
        const config = JSON.parse(job?.config || "{}");
        setDiarization(!!config.diarization);
        setNumSpeakers(config.num_speakers || 0);
      } catch {
        /* Historical jobs do not override the last selected model. */
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
        const generated = latestMinutes(fresh),
          previous = latestMinutes(meetingRef.current);
        if (
          !minutesDirty &&
          !savingMinutes &&
          meetingRef.current?.id === fresh.id &&
          generated &&
          (!selectedMinutesRef.current ||
            (selectedMinutesRef.current === previous?.id &&
              generated.id !== previous?.id))
        )
          displayMinutes(generated);
        setMeeting((old) => {
          if (!old || old.id !== fresh.id) return old;
          if (!transcriptDirty && fresh.revision !== old.revision) {
            return fresh;
          }
          return {
            ...old,
            review: fresh.review,
            flow: fresh.flow,
            active_minutes_id: fresh.active_minutes_id,
            minutes_generation: fresh.minutes_generation,
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
  }, [meeting?.id, transcriptDirty, minutesDirty, savingMinutes]);

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
  const startMinutes = async () => {
    const target = meetingRef.current;
    if (!target || generating) return;
    const options = { ...minutesOptions };
    const profile = environment?.analysis.profiles.find(
      (p) => p.id === minutesModel,
    );
    if (
      profile?.provider_type === "cloud" &&
      (options.method !== "analysis" || !currentAnalysis(target)) &&
      !window.confirm(
        options.method === "current"
          ? "当前纪要及其原文依据将发送到所选云端模型，是否继续？"
          : "文字稿和本次整理资料将发送到所选云端模型，是否继续？",
      )
    )
      return;
    try {
      await saveTranscript();
      await saveMinutes();
      setGenerating(true);
      const response = await api.composeMinutes(target.id, {
        ...options,
        also_analyze: withAnalysis && options.method === "ai",
        ...(environment?.analysis.profiles.length
          ? { profile_id: minutesModel }
          : { model: minutesModel }),
        source_minutes_id: selectedMinutesRef.current || undefined,
      });
      const fresh = await api.meeting(target.id);
      if (meetingRef.current?.id === target.id) {
        setMeeting(fresh);
        if (!selectedMinutesRef.current && response.artifact)
          displayMinutes(response.artifact);
      }
      toast(
        response.job_id
          ? "已开始分析，完成后将自动保存纪要草稿；刷新页面不会丢失后续步骤"
          : response.artifact?.id === fresh.active_minutes_id
            ? "会议纪要已生成，可核对编辑后导出"
            : "新草稿已保存；当前采用稿保持不变，可查看、对比后采用",
      );
      if (response.warning) toast(response.warning);
      else if (response.analysis_job_id)
        toast("纪要已保存，独立会议分析已排队；可在原文与分析中查看");
    } catch (error) {
      toast((error as Error).message);
    } finally {
      setGenerating(false);
    }
  };
  const selectMinutes = async (id: string) => {
    try {
      await saveMinutes();
      const target = meetingRef.current;
      if (!target) return;
      const artifact = target.artifacts.find((a) => a.id === id) as
        Artifact<MeetingMinutes> | undefined;
      if (artifact) displayMinutes(artifact);
    } catch {
      /* Keep the unsaved version visible. */
    }
  };
  const compareMinutes = async (left: string, right: string) => {
    try {
      const displayed = selectedMinutesRef.current;
      const saved = await saveMinutes();
      setCompareIds([
        left === displayed && saved ? saved.id : left,
        right === displayed && saved ? saved.id : right,
      ]);
    } catch {
      /* Saving already reported the error. */
    }
  };
  const adoptMinutes = async (id = selectedMinutesRef.current) => {
    if (!meeting || !id) return;
    try {
      const displayed = selectedMinutesRef.current === id;
      const saved = await saveMinutes();
      const aid = displayed && saved ? saved.id : id;
      await api.adoptMinutes(meeting.id, aid);
      const fresh = await api.meeting(meeting.id);
      setMeeting(fresh);
      const adopted = fresh.artifacts.find((a) => a.id === aid);
      if (adopted) displayMinutes(adopted as Artifact<MeetingMinutes>);
      setCompareIds(null);
      toast("已采用此版本，会议成果与跟进使用该版本；旧版本仍保留");
    } catch (error) {
      toast((error as Error).message);
    }
  };
  const exportMinutes = async (format: "docx" | "pdf" | "md") => {
    await saveMinutes();
    if (meeting && selectedMinutesRef.current)
      download(
        `/meetings/${meeting.id}/minutes/export/${format}?include_citations=${showCitations}&artifact_id=${encodeURIComponent(selectedMinutesRef.current)}`,
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
        onFollowups={() =>
          environment?.meeting_flow_ready
            ? setFollowupsOpen(true)
            : toast("跨会议跟进需在录音和任务结束后更新服务，现有内容已保留。")
        }
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
        {!!recoveryQuery.data?.length && (
          <section
            className="recording-recovery-banner"
            aria-label="录音恢复提醒"
          >
            <span>
              发现 {recoveryQuery.data.length}{" "}
              条未完成录音，已有内容待核对保存。
            </span>
            <button onClick={() => setRecordingOpen(true)}>打开恢复中心</button>
          </section>
        )}
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
            {candidateArtifacts.map((item) => (
              <section className="transcription-recovery" key={item.id}>
                {item.provenance.incomplete === true && (
                  <strong>
                    {item.provenance.completion_mode === "quick"
                      ? "快速文字稿"
                      : "精细转写"}{" "}
                    · 未完成恢复稿
                  </strong>
                )}
                <p>
                  {item.provenance.incomplete
                    ? "转写未完成，已完成部分已保留为恢复稿，不会覆盖当前文稿。可从检查点重试。"
                    : "重新转写期间文稿有修改，识别结果已作为候选稿保留。"}
                </p>
                {item.provenance.incomplete === true && (
                  <details>
                    <summary>查看已保留文字与缺段</summary>
                    <p>
                      以下为未完成稿，请勿视为整场完整文字稿。对齐异常的段落只保留块级时间。
                    </p>
                    {Array.isArray(item.provenance.missing_ranges) &&
                      item.provenance.missing_ranges.map(
                        (range: { start: number; end: number }, i: number) => (
                          <p key={i}>
                            待识别：{clock(range.start)}–{clock(range.end)}
                          </p>
                        ),
                      )}
                    <textarea
                      readOnly
                      aria-label="未完成文字恢复稿"
                      rows={12}
                      value={
                        Array.isArray(item.payload)
                          ? (item.payload as Segment[])
                              .map(
                                (s) =>
                                  `[${clock(s.start)}–${clock(s.end)}]${s.alignment_warning ? "（时间待校准）" : ""} ${s.text}`,
                              )
                              .join("\n")
                          : ""
                      }
                    />
                  </details>
                )}
                <button
                  onClick={() => {
                    const link = document.createElement("a");
                    link.href = URL.createObjectURL(
                      new Blob(
                        [
                          JSON.stringify(
                            item.provenance.incomplete
                              ? {
                                  segments: item.payload,
                                  provenance: item.provenance,
                                }
                              : item.payload,
                            null,
                            2,
                          ),
                        ],
                        {
                          type: "application/json",
                        },
                      ),
                    );
                    link.download = item.provenance.incomplete
                      ? "未完成恢复稿.json"
                      : "候选转写.json";
                    link.click();
                  }}
                >
                  {item.provenance.incomplete
                    ? "导出未完成恢复稿"
                    : "查看候选稿"}
                </button>
              </section>
            ))}
            <Workflow
              meeting={meeting}
              transcriptDirty={transcriptDirty}
              minutesDirty={minutesDirty}
              reviewBusy={reviewBusy}
              onReview={navigateReview}
              onConfirm={(step, decision) => void confirmReview(step, decision)}
              onTranscribe={() => void startTranscription()}
              onSave={() => void saveTranscript()}
              onAnalysis={() => setWorkspace("analysis")}
              onMinutes={() => navigateReview("minutes")}
              onPreview={() => void openMinutesPreview()}
            />
            <div className="workspace" id="meeting-workspace">
              <div className="transcript-panel">
                <div className="panel-header">
                  <div className="tabs">
                    {(
                      [
                        ["prepare", "会议准备"],
                        [
                          "notes",
                          `会后整理${meeting.notes.length ? ` · ${meeting.notes.length}` : ""}`,
                        ],
                        ["minutes", "会议成果"],
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
                    <details className="meeting-source-tools">
                      <summary>
                        {workspace === "transcript"
                          ? "文字稿"
                          : workspace === "analysis"
                            ? "深入分析"
                            : "原文与分析"}
                      </summary>
                      <button
                        onClick={(e) => {
                          setWorkspace("transcript");
                          e.currentTarget.closest("details")!.open = false;
                        }}
                      >
                        打开文字稿
                      </button>
                      <button
                        onClick={(e) => {
                          setWorkspace("analysis");
                          e.currentTarget.closest("details")!.open = false;
                        }}
                      >
                        打开深入分析
                      </button>
                    </details>
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
                {workspace === "prepare" && (
                  <MeetingPreparation
                    key={meeting.id}
                    meeting={meeting}
                    meetings={listQuery.data || []}
                    onRefresh={async () => {
                      setMeeting(await api.meeting(meeting.id));
                    }}
                    onOpen={(id) => void openMeeting(id)}
                    toast={toast}
                  />
                )}
                {workspace === "transcript" && (
                  <TranscriptView
                    meeting={meeting}
                    jumpSegmentId={
                      searchTarget?.meeting_id === meeting.id
                        ? searchTarget.source_segment_ids[0]
                        : undefined
                    }
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
                        displayMinutes(artifact || null);
                      }
                    }}
                    minutesDirty={minutesDirty}
                    onMinutes={() => setWorkspace("minutes")}
                    onPause={() => pausePlaybackRef.current?.()}
                    onSeek={(time) => seekRef.current?.(time)}
                    toast={toast}
                  />
                )}
                {workspace === "minutes" && (
                  <>
                    <MeetingOutcomes
                      onSeek={(time) => seekRef.current?.(time)}
                      minutesDirty={minutesDirty}
                      meeting={meeting}
                      onNotes={() => setWorkspace("notes")}
                      onRefresh={async () =>
                        setMeeting(await api.meeting(meeting.id))
                      }
                      toast={toast}
                    />
                    <MinutesView
                      meeting={meeting}
                      environment={environment}
                      draft={minutesDraft}
                      dirty={minutesDirty}
                      transcriptDirty={transcriptDirty}
                      options={minutesOptions}
                      withAnalysis={withAnalysis}
                      selectedId={selectedMinutesId}
                      templates={
                        (templatesQuery.data || []) as MinutesTemplate[]
                      }
                      model={minutesModel}
                      showCitations={showCitations}
                      generating={generating}
                      onOptions={changeMinutesOptions}
                      onWithAnalysis={setWithAnalysis}
                      onSelect={(id) => void selectMinutes(id)}
                      onAdopt={() => void adoptMinutes()}
                      onCompare={(left, right) =>
                        void compareMinutes(left, right)
                      }
                      onExport={(format) => void exportMinutes(format)}
                      onModel={setMinutesModel}
                      onCitations={setShowCitations}
                      onGenerate={() => void startMinutes()}
                      onDraft={(value) => {
                        minutesGeneration.current++;
                        minutesDraftRef.current = { draft: value, dirty: true };
                        setMinutesDraft(value);
                        setMinutesDirty(true);
                      }}
                      onSave={() => void saveMinutes()}
                      onPreview={() => void openMinutesPreview()}
                      onSeek={(time) => seekRef.current?.(time)}
                    />
                  </>
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
          onConfigure={() => {
            setSearchOpen(false);
            setModelsOpen(true);
          }}
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
      {compareIds &&
        meeting &&
        (() => {
          const left = meeting.artifacts.find((a) => a.id === compareIds[0]) as
            Artifact<MeetingMinutes> | undefined;
          const right = meeting.artifacts.find(
            (a) => a.id === compareIds[1],
          ) as Artifact<MeetingMinutes> | undefined;
          return left && right ? (
            <MinutesCompare
              left={left}
              right={right}
              onClose={() => setCompareIds(null)}
              onAdopt={() => void adoptMinutes(right.id)}
            />
          ) : null;
        })()}
      {previewOpen && minutesDraft && (
        <MinutesPreview
          meetingId={meeting!.id}
          artifactId={selectedMinutesId}
          includeCitations={showCitations}
          onClose={() => setPreviewOpen(false)}
          onExport={(format) => void exportMinutes(format)}
        />
      )}
      {followupsOpen && (
        <FollowupsDialog
          onClose={() => setFollowupsOpen(false)}
          onOpen={async (id) => {
            await openMeeting(id);
            setWorkspace("minutes");
          }}
        />
      )}
      {recordingOpen && (
        <RecordingDialog
          flowReady={environment?.meeting_flow_ready === true}
          meetings={listQuery.data || []}
          engineLabel={engineLabel(engine)}
          engines={environment?.engines || []}
          onClose={() => {
            setRecordingOpen(false);
            void client.invalidateQueries({
              queryKey: ["recording-recoveries"],
            });
          }}
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
