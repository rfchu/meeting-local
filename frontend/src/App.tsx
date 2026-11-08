import MeetingLibrary from "./features/meeting/MeetingLibrary";
import RuntimeManagement, {
  type ManagementPage,
} from "./features/management/RuntimeManagement";
import OverviewArchiveButton from "./features/overview/OverviewArchiveButton";
import SampleMeetingDialog from "./features/onboarding/SampleMeetingDialog";
import { recordingRecoveryCount } from "./features/notes/recordingInventory";
import MinutesDeliveryPanel from "./features/minutes/MinutesDeliveryPanel";
import ReviewSourcePane from "./features/meeting/ReviewSourcePane";
import {
  browserMeetingStorage,
  readMeetingView,
  resolveMeetingView,
  saveMeetingView,
  lastMeeting,
  rememberMeeting,
  type MeetingView,
} from "./preferences/meetingView";
import { minutesAdoptionBlockReason } from "./features/minutes/presentation";
import {
  readMinutesOptions,
  persistMinutesOptions,
  type MinutesOptions,
} from "./features/minutes/composition";
import MinutesCompare from "./features/minutes/MinutesCompare";
import { useModelChoice } from "./preferences/useModelChoice";
import FollowupsDialog from "./features/meeting/FollowupsDialog";
import ChineseMeetingRecord from "./features/meeting/ChineseMeetingRecord";
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
  TranscriptionLanguage,
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
import type { TaskFocus } from "./types";
import { handoffWorkspace } from "./components/handoff";
import Player from "./components/Player";
import { AppModals } from "./components/AppModals";
import { MinutesPreview, RenameDialog } from "./components/Dialogs";
import TranscriptView from "./features/transcript/TranscriptView";
import AnalysisView from "./features/analysis/AnalysisView";
import MinutesView from "./features/minutes/MinutesView";
import NotesView from "./features/notes/NotesView";
import { UnifiedTimeline } from "./features/copilot/UnifiedTimeline";
import CompanionRecordingStatus, {
  companionRecording,
} from "./features/notes/CompanionRecordingStatus";
import RecordingDialog from "./features/notes/RecordingDialog";
import { recoverableRecordings } from "./features/notes/recordingBuffer";

export default function App() {
  const [targetRecordingId, setTargetRecordingId] = useState<string>();
  const [companionId, setCompanionId] = useState(() =>
    companionRecording(window.location.hash),
  );
  useEffect(() => {
    const readTarget = () =>
      setCompanionId(companionRecording(window.location.hash));
    window.addEventListener("hashchange", readTarget);
    return () => window.removeEventListener("hashchange", readTarget);
  }, []);
  const closeCompanionStatus = () => {
    if (companionRecording(window.location.hash)) {
      window.history.replaceState(
        window.history.state,
        "",
        window.location.pathname + window.location.search,
      );
    }
    setCompanionId(undefined);
  };
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
  const [actionsFocus, setActionsFocus] = useState(false);
  const [deliveryFocus, setDeliveryFocus] = useState(false);
  const [overviewFocus, setOverviewFocus] = useState(false);
  const [overviewSourceId, setOverviewSourceId] = useState<string>();
  const [noteSourceId, setNoteSourceId] = useState<string>();
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
  const [activeSegment, setActiveSegment] = useState<string>();
  const [playbackRestore, setPlaybackRestore] = useState<MeetingView>({});
  const restoredMeeting = useRef(false);
  const openingMeeting = useRef(0);
  const [restoredSegment, setRestoredSegment] = useState<string>();
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
  const [analysisModel] = useModelChoice("analysis", meeting?.id),
    [minutesModel, setMinutesModel] = useModelChoice("minutes", meeting?.id),
    [generating, setGenerating] = useState(false);
  const [uploading, setUploading] = useState(false),
    [toastText, setToastText] = useState(""),
    [renameOpen, setRenameOpen] = useState(false),
    [previewOpen, setPreviewOpen] = useState(false),
    [recordingOpen, setRecordingOpen] = useState(false);
  const [taskFocus, setTaskFocus] = useState<TaskFocus | undefined>();
  const [managementPage, setManagementPage] = useState<ManagementPage | null>(
    null,
  );
  const recoveryQuery = useQuery({
    queryKey: ["recording-recoveries"],
    queryFn: recoverableRecordings,
    refetchInterval: 30000,
    enabled: !recordingOpen,
  });
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [transcriptionLanguage, setTranscriptionLanguage] =
    useState<TranscriptionLanguage>("auto");
  const experimentalTranscription = transcriptionLanguage === "ja";
  useEffect(() => {
    if (meeting)
      setTranscriptionLanguage(
        meeting.flow?.brief.source_language ||
          (meeting.revision ? "zh" : "auto"),
      );
  }, [meeting?.id, meeting?.flow?.brief.source_language]);
  const [reviewFocus, setReviewFocus] = useState<number | null>(null);
  const [reviewFocusVersion, setReviewFocusVersion] = useState(0);
  const [reviewSourceOpen, setReviewSourceOpen] = useState(true);
  const [reviewBusy, setReviewBusy] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [followupsOpen, setFollowupsOpen] = useState(false);
  const [searchTarget, setSearchTarget] = useState<SearchHit | null>(null);
  const [engine, setEngine] = useModelChoice("transcription", meeting?.id),
    [diarization, setDiarization] = useState(false),
    [numSpeakers, setNumSpeakers] = useState(0);
  const transcriptionEngine = experimentalTranscription ? "qwen" : engine;
  const pausePlaybackRef = useRef<(() => void) | null>(null);
  const seekRef = useRef<
      | ((time: number, track?: import("./types").SourceTrack | null) => void)
      | null
    >(null),
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
    const failed = (event: Event) =>
      toast(
        "本场模型选择已保留，但未同步到后端：" +
          (event as CustomEvent<string>).detail,
      );
    window.addEventListener("tingji-model-preference-error", failed);
    return () =>
      window.removeEventListener("tingji-model-preference-error", failed);
  }, [toast]);
  useEffect(() => {
    const env = envQuery.data;
    if (!env) return;
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
    setRestoredSegment(undefined);
    const timer = window.setTimeout(() => {
      if (searchTarget.start !== null) seekRef.current?.(searchTarget.start);
      setSearchTarget(null);
    }, 100);
    return () => window.clearTimeout(timer);
  }, [searchTarget, meeting?.id]);

  useEffect(() => {
    if (!restoredSegment || workspace !== "transcript") return;
    const timer = window.setTimeout(() => setRestoredSegment(undefined), 400);
    return () => window.clearTimeout(timer);
  }, [restoredSegment, workspace]);

  const [qualityReviewRequest, setQualityReviewRequest] = useState<{
    id: string;
    nonce: number;
    view: "pending" | "all" | "gaps";
  }>({
    id: "",
    nonce: 0,
    view: "pending",
  });
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

  const locateReview = (time: number) => {
    setReviewFocus(time);
    setReviewFocusVersion((value) => value + 1);
  };

  const openMeeting = async (id: string) => {
    setReviewFocus(null);
    const request = ++openingMeeting.current;
    try {
      await saveTranscript();
      await saveMinutes();
      const value = await api.meeting(id);
      if (request !== openingMeeting.current) return null;
      setMeeting(value);
      setMinutesOptions((old) => ({
        ...old,
        output_language:
          value.flow?.brief.output_language || old.output_language || "zh",
      }));
      setTranscriptionLanguage(
        value.flow?.brief.source_language || (value.revision ? "zh" : "auto"),
      );
      setTranscriptDirty(false);
      const restored = resolveMeetingView(
        value,
        readMeetingView(id, browserMeetingStorage()),
      );
      const artifact = restored.minutes;
      setPlaybackRestore(restored);
      setRestoredSegment(
        restored.position > 0
          ? value.segments.find(
              (segment) =>
                segment.start <= restored.position &&
                segment.end > restored.position,
            )?.id
          : undefined,
      );
      rememberMeeting(id, browserMeetingStorage());
      displayMinutes(artifact || null);
      if (!artifact && minutesOptions.method === "current")
        setMinutesOptions((old) => ({ ...old, method: "ai" }));
      setShowCitations(artifact?.payload.style === "internal");
      setWorkspace(restored.workspace);

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
    if (!listQuery.data || restoredMeeting.current) return;
    restoredMeeting.current = true;
    const id = lastMeeting(browserMeetingStorage());
    if (!meetingRef.current && listQuery.data.some((row) => row.id === id))
      void openMeeting(id);
  }, [listQuery.data]);
  useEffect(() => {
    if (meeting)
      saveMeetingView(
        meeting.id,
        { workspace, minutesId: selectedMinutesId },
        browserMeetingStorage(),
      );
  }, [meeting?.id, workspace, selectedMinutesId]);
  const goHome = async () => {
    if (savingTranscript) {
      toast("文字稿正在保存，请稍候再返回首页");
      return;
    }
    openingMeeting.current++;
    try {
      await saveTranscript();
      await saveMinutes();
      rememberMeeting("", browserMeetingStorage());
      setMeeting(null);
      displayMinutes(null);
    } catch {
      /* Preserve unsaved content. */
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
            recording_quality: fresh.recording_quality,
            flow: fresh.flow,
            handoff: fresh.handoff,
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
      if (await openMeeting(value.id)) {
        toast("录音已导入，可以开始转写");
      }
    } catch (error) {
      toast((error as Error).message);
    } finally {
      setUploading(false);
    }
  };
  const completeRecording = async (
    meetingId: string,
    options: {
      mode: CompletionMode;
      engine: "whisper" | "qwen";
      goal?: "transcript" | "minutes";
    },
  ) => {
    setUploading(true);
    let created: Meeting | null = null;
    let reusedHandoff = false;
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
        if (plan) {
          const profile = envQuery.data?.analysis.profiles.find(
            (p) => p.id === minutesModel,
          );
          const requested = options.goal === "minutes";
          const consent =
            !requested ||
            profile?.provider_type !== "cloud" ||
            window.confirm(
              `转写完成后，将文字稿、会议准备及已确认关注点发送到 ${profile.name}（${profile.base_url}）生成纪要。是否同意本次自动处理？`,
            );
          if (requested && !consent)
            followUpErrors.push("未授权云端纪要，本次只生成文字稿");
          const task = await api.transcribe(created.id, {
            ...plan,
            source_language: created.flow?.brief.source_language || "zh",
            recording_handoff: true,
            ...(requested && consent
              ? {
                  minutes_followup: {
                    ...(envQuery.data?.analysis.profiles.length
                      ? { profile_id: minutesModel }
                      : { model: minutesModel }),
                    confirm_cloud: profile?.provider_type === "cloud",
                    expected_provider: profile
                      ? {
                          id: profile.id,
                          base_url: profile.base_url,
                          provider_type: profile.provider_type,
                          model: profile.model,
                        }
                      : undefined,
                    settings: {
                      ...minutesOptions,
                      output_language:
                        created.flow?.brief.output_language ||
                        minutesOptions.output_language ||
                        "zh",
                      method: "ai",
                    },
                  },
                }
              : {}),
          });
          reusedHandoff = !!task.reused;
        }
      } catch (error) {
        throw new Error(
          `会后任务尚未确认，可安全重试：${(error as Error).message}`,
        );
      }
      await client.invalidateQueries({ queryKey: ["meetings"] });
      const opened = await openMeeting(created.id);
      if (!opened)
        throw new Error("本机会议已保存，打开暂未成功，请重试进入会后整理");
      setRecordingOpen(false);
      setTargetRecordingId(undefined);
      setWorkspace(
        options.mode === "none" ? "notes" : handoffWorkspace(opened),
      );
      toast(
        reusedHandoff
          ? "已打开会议，沿用首次交接的任务。处理状态可在运行管理查看，失败或取消的任务可在那里重试。"
          : followUpErrors.length
            ? `录音已保存；${followUpErrors.join("；")}`
            : options.mode === "none"
              ? `录音和 ${created.notes.length} 条简记已保存，可稍后转写`
              : `录音和 ${created.notes.length} 条简记已保存，文字稿正在本机生成${options.goal === "minutes" ? "，完成后自动排队纪要" : ""}`,
      );
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
        !experimentalTranscription &&
        meeting.revision &&
        !confirm(
          "重新转写会生成新的模型文稿版本。现有校对稿保留在历史版本中，继续吗？",
        )
      )
        return;
      await api.transcribe(meeting.id, {
        engine: transcriptionEngine,
        source_language: transcriptionLanguage,
        diarization: !experimentalTranscription && diarization,
        num_speakers: experimentalTranscription ? 0 : numSpeakers,
      });
      setMeeting(await api.meeting(meeting.id));
    } catch (error) {
      toast((error as Error).message);
    }
  };
  const jobAction = async (id: string, action: "cancel" | "retry") => {
    try {
      await api.jobAction(
        id,
        action,
        meeting?.jobs.find((job) => job.id === id)?.recorded_retries,
      );
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
  const [chineseMinutes, setChineseMinutes] = useState<{
    id: string;
    revision: number;
    profileId: string;
  }>();
  const startMinutes = async (fromAnalysis = false) => {
    const target = meetingRef.current;
    if (!target || generating) return;
    const existingRecord = target.artifacts.find(
      (artifact) => artifact.id === selectedMinutesRef.current,
    )?.provenance.chinese_record as { profile_id?: string } | undefined;
    const recordProfile =
      chineseMinutes?.id === target.id &&
      chineseMinutes.revision === target.revision
        ? chineseMinutes.profileId
        : existingRecord?.profile_id;
    const options = {
      ...minutesOptions,
      ...(fromAnalysis
        ? {
            method: "analysis" as const,
            template_id: "general-meeting",
            audience: "internal" as const,
          }
        : {}),
      output_language: fromAnalysis
        ? undefined
        : minutesOptions.output_language ||
          target.flow?.brief.output_language ||
          "zh",
    };
    if (fromAnalysis && (!currentAnalysis(target) || transcriptDirty))
      return toast("请先生成当前文字稿的分析");
    if (
      !minutesModel &&
      !(options.method === "analysis" && currentAnalysis(target))
    )
      return toast("请先选择纪要模型配置");
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
        expected_analysis_id: fromAnalysis
          ? currentAnalysis(target)?.id
          : undefined,
        chinese_record_profile_id:
          options.method === "ai" ? recordProfile : undefined,
        ...(profile?.provider_type === "cloud" && options.method !== "analysis"
          ? {
              confirm_cloud: true,
              expected_provider: {
                id: profile.id,
                base_url: profile.base_url,
                provider_type: profile.provider_type,
                model: profile.model,
              },
            }
          : {}),
        ...(environment?.analysis.profiles.length
          ? { profile_id: minutesModel }
          : { model: minutesModel }),
        source_minutes_id: selectedMinutesRef.current || undefined,
      });
      const fresh = await api.meeting(target.id);
      if (meetingRef.current?.id === target.id) {
        setMeeting(fresh);
        if ((fromAnalysis || !selectedMinutesRef.current) && response.artifact)
          displayMinutes(response.artifact);
      }
      toast(
        response.job_id
          ? options.method === "analysis"
            ? "已排队分析，完成后保存纪要草稿"
            : "纪要任务已排队，完成后自动保存草稿；刷新页面可继续查看"
          : response.artifact?.id === fresh.active_minutes_id
            ? "会议纪要已生成，可核对编辑后导出"
            : "新草稿已保存；当前采用稿保持不变，可查看、对比后采用",
      );
      if (response.warning) toast(response.warning);
      if (fromAnalysis && meetingRef.current?.id === target.id)
        setWorkspace("minutes");
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
    engineReady = !!environment?.engines.find(
      (item) => item.id === transcriptionEngine,
    )?.ready;
  const candidateArtifacts =
    meeting?.artifacts.filter((item) => item.kind === "transcript_candidate") ||
    [];
  return (
    <>
      <Sidebar
        onHome={() => void goHome()}
        onLibrary={() => setLibraryOpen(true)}
        meetings={listQuery.data || []}
        currentId={meeting?.id}
        uploading={uploading}
        onSelect={(id) => void openMeeting(id)}
        onUpload={(file) => void upload(file)}
        onRecord={() => setRecordingOpen(true)}
        onManage={() => setManagementPage("overview")}
        onSearch={() => setSearchOpen(true)}
        onFollowups={() =>
          environment?.meeting_flow_ready
            ? setFollowupsOpen(true)
            : toast("跨会议跟进需在录音和任务结束后更新服务，现有内容已保留。")
        }
        recoveryCount={recordingRecoveryCount(recoveryQuery.data || [])}
      />
      {libraryOpen && (
        <MeetingLibrary
          meetings={listQuery.data || []}
          onClose={() => setLibraryOpen(false)}
          onPrepare={async (id) => {
            const current = await api.preparation();
            await api.savePreparation({ ...current, previous_meeting_id: id });
            setLibraryOpen(false);
            setRecordingOpen(true);
            toast("已关联系列中的最近会议");
          }}
          onOpen={async (id, segmentId) => {
            setLibraryOpen(false);
            const opened = await openMeeting(id);
            const segment = opened?.segments.find(
              (item) => item.id === segmentId,
            );
            if (segment) {
              setWorkspace("notes");
              setReviewSourceOpen(true);
              locateReview(segment.start);
            }
          }}
        />
      )}
      <main>
        <header>
          <div className="breadcrumb">
            工作空间 <span>/</span> 会议转写
          </div>
          <div className="local-pill">
            <span className="dot" /> 转写本地处理 · 分析引擎可配置
          </div>
        </header>
        {!meeting && !!recordingRecoveryCount(recoveryQuery.data || []) && (
          <section
            className="recording-recovery-banner"
            aria-label="录音恢复提醒"
          >
            <span>
              发现 {recordingRecoveryCount(recoveryQuery.data || [])}{" "}
              条录音会话待检查，请核对已保存内容。
            </span>
            <button onClick={() => setManagementPage("recovery")}>
              前往运行管理查看
            </button>
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
                <small className="muted">音频文件：{meeting.filename}</small>
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
                    rememberMeeting("", browserMeetingStorage());
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
              <details
                className="transcription-options"
                open={!meeting.revision}
              >
                <summary>
                  {meeting.revision ? "重新转写与识别设置" : "转写设置"}
                </summary>
                <div className="transcription-options-body">
                  <label>
                    原文语言{" "}
                    <select
                      aria-label="转写原文语言"
                      value={transcriptionLanguage}
                      disabled={transcriptionRunning}
                      onChange={(event) =>
                        setTranscriptionLanguage(
                          event.target.value as typeof transcriptionLanguage,
                        )
                      }
                    >
                      <option value="auto">自动识别（中英）</option>
                      <option value="zh">中文</option>
                      <option value="en">英文</option>
                      <optgroup label="更多语言">
                        <option value="ja">日语（Beta）</option>
                      </optgroup>
                    </select>
                  </label>
                  <label className="engine-field">
                    识别引擎{" "}
                    <select
                      value={transcriptionEngine}
                      disabled={
                        transcriptionRunning || experimentalTranscription
                      }
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
                          <option value="whisper">
                            Whisper large-v3-turbo
                          </option>
                          <option value="qwen">千问 Qwen3-ASR-1.7B</option>
                        </>
                      )}
                    </select>
                  </label>
                  {experimentalTranscription ? (
                    <p className="muted">
                      使用本机千问生成独立原文草稿，按约 30
                      秒音频块回听。姓名、数字和外语插句请人工核对。
                      {!engineReady && " 请先安装本机千问模型。"}
                    </p>
                  ) : (
                    <>
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
                    </>
                  )}
                  <button
                    className="primary"
                    disabled={transcriptionRunning || !engineReady}
                    onClick={() => void startTranscription()}
                  >
                    {experimentalTranscription
                      ? "生成日语草稿"
                      : meeting.revision
                        ? "重新转写"
                        : "开始转写"}
                  </button>
                </div>
              </details>
              <button onClick={() => setRenameOpen(true)}>重命名</button>
              <OverviewArchiveButton
                openRequested={overviewFocus}
                focusItemId={overviewSourceId}
                onOpened={() => setOverviewFocus(false)}
                key={meeting.id}
                meeting={meeting}
                onSeek={(evidence) => {
                  const source = meeting.segments.find(
                    (segment) =>
                      (segment.source_track ?? null) ===
                        (evidence.source_track ?? null) &&
                      segment.start <= evidence.start &&
                      segment.end > evidence.start &&
                      segment.text.includes(evidence.quote),
                  );
                  if (
                    !source &&
                    evidence.source_track &&
                    evidence.range_id &&
                    evidence.segment_index != null
                  )
                    return false;
                  setWorkspace("transcript");
                  if (source) setRestoredSegment(source.id);
                  seekRef.current?.(
                    source?.start ?? evidence.start,
                    evidence.source_track,
                  );
                  return true;
                }}
              />
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
                {item.provenance.experimental === true && (
                  <strong>日语 · 实验性原文草稿</strong>
                )}
                {item.provenance.incomplete === true &&
                  item.provenance.experimental !== true && (
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
                    : item.provenance.experimental
                      ? "可按音频块回听核对，也可导出文本进行校对。"
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
                {item.provenance.experimental === true &&
                  !item.provenance.incomplete && (
                    <ChineseMeetingRecord
                      id={meeting.id}
                      revision={item.revision}
                      draftId={item.id}
                      dirty={false}
                      profiles={
                        environment?.analysis.profiles.filter(
                          (profile) => profile.provider_type === "local",
                        ) || []
                      }
                      onSeek={(time) => seekRef.current?.(time)}
                    />
                  )}
                <button
                  onClick={() => {
                    if (
                      item.provenance.experimental &&
                      Array.isArray(item.payload)
                    ) {
                      const text = (item.payload as Segment[])
                        .map(
                          (segment) =>
                            `[${clock(segment.start)}–${clock(segment.end)}] ${segment.text}`,
                        )
                        .join("\n\n");
                      const url = URL.createObjectURL(
                        new Blob(
                          ["日语实验性原文草稿（按音频块定位）\n\n", text],
                          { type: "text/plain;charset=utf-8" },
                        ),
                      );
                      const link = document.createElement("a");
                      link.href = url;
                      link.download = "日语实验草稿.txt";
                      link.click();
                      window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
                      return;
                    }
                    const link = document.createElement("a");
                    link.href = URL.createObjectURL(
                      new Blob(
                        [
                          JSON.stringify(
                            item.provenance.incomplete ||
                              item.provenance.experimental
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
                    : item.provenance.experimental
                      ? "导出日语草稿"
                      : "查看候选稿"}
                </button>
              </section>
            ))}
            <Workflow
              meeting={meeting}
              transcriptDirty={transcriptDirty}
              minutesDirty={minutesDirty}
              reviewBusy={reviewBusy}
              onRecordingReview={() => {
                setQualityReviewRequest((old) => ({
                  id: meeting.id,
                  nonce: old.nonce + 1,
                  view: meeting.recording_quality?.pending_count
                    ? "pending"
                    : meeting.recording_quality?.gap_count
                      ? "gaps"
                      : "all",
                }));
                navigateReview("transcript");
              }}
              onSourceLocate={(source) => {
                if (source.target?.item_id) {
                  setOverviewSourceId(source.target.item_id);
                  setOverviewFocus(true);
                } else if (source.target?.note_id) {
                  setNoteSourceId(source.target.note_id);
                  locateReview(source.target.start ?? 0);
                  setReviewSourceOpen(true);
                  navigateReview("notes");
                } else {
                  navigateReview("transcript");
                  setRestoredSegment(source.target?.segment_id);
                }
              }}
              onReview={navigateReview}
              onConfirm={(step, decision) => void confirmReview(step, decision)}
              onTranscribe={() => void startTranscription()}
              onSave={() => void saveTranscript()}
              onTasks={(focus) => {
                setTaskFocus(focus);
                setManagementPage("tasks");
              }}
              onActions={() => {
                setActionsFocus(true);
                navigateReview("minutes");
              }}
              onOverview={() => {
                setOverviewSourceId(undefined);
                setOverviewFocus(true);
              }}
              onAnalysis={() => setWorkspace("analysis")}
              onMinutes={() => navigateReview("minutes")}
              onDelivery={() => {
                setDeliveryFocus(true);
                navigateReview("minutes");
              }}
              onPreview={() => void openMinutesPreview()}
            />
            <div
              className={`workspace ${workspace === "prepare" ? "workspace-focused" : ""}`}
              id="meeting-workspace"
            >
              <div className="transcript-panel">
                <div className="panel-header">
                  <div className="tabs">
                    {(
                      [
                        ["copilot", "伴随时间线"],
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
                <div hidden={workspace !== "prepare"}>
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
                </div>
                {workspace === "copilot" && (
                  <UnifiedTimeline
                    meeting={meeting}
                    activeSegment={activeSegment}
                    onSeek={(time, track) => seekRef.current?.(time, track)}
                  />
                )}
                {workspace === "transcript" && (
                  <ChineseMeetingRecord
                    activeSegment={activeSegment}
                    key={`${meeting.id}-${meeting.revision}`}
                    id={meeting.id}
                    revision={meeting.revision}
                    dirty={transcriptDirty}
                    profiles={
                      environment?.analysis.profiles.filter(
                        (item) => item.provider_type === "local",
                      ) || []
                    }
                    onSeek={(time) => seekRef.current?.(time)}
                    onContinue={(profileId) => {
                      setChineseMinutes({
                        id: meeting.id,
                        revision: meeting.revision,
                        profileId,
                      });
                      setMinutesOptions((old) => ({
                        ...old,
                        method: "ai",
                        output_language: "zh",
                      }));
                      setWorkspace("minutes");
                    }}
                  />
                )}
                {workspace === "transcript" && (
                  <TranscriptView
                    activeSegment={activeSegment}
                    qualityReviewView={qualityReviewRequest.view}
                    qualityReviewRequest={
                      qualityReviewRequest.id === meeting.id
                        ? qualityReviewRequest.nonce
                        : 0
                    }
                    meeting={meeting}
                    jumpSegmentId={
                      searchTarget?.meeting_id === meeting.id
                        ? searchTarget.source_segment_ids[0]
                        : restoredSegment
                    }
                    onChange={updateSegments}
                    onSeek={(time, track) => seekRef.current?.(time, track)}
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
                    onGenerate={() => void generateAnalysis()}
                    onCompose={() => void startMinutes(true)}
                    composing={generating}
                    onConfigure={() => setManagementPage("defaults")}
                    onJobAction={(id, action) => void jobAction(id, action)}
                    onSeek={(time) => seekRef.current?.(time)}
                    transcriptDirty={transcriptDirty}
                  />
                )}
                {workspace === "notes" && (
                  <div
                    className={`meeting-review-workspace ${reviewSourceOpen ? "split" : ""}`}
                  >
                    <button
                      className="review-source-toggle"
                      onClick={() => setReviewSourceOpen(!reviewSourceOpen)}
                    >
                      {reviewSourceOpen ? "收起原文" : "展开原文与回听"}
                    </button>
                    {reviewSourceOpen && (
                      <ReviewSourcePane
                        activeSegment={activeSegment}
                        key={meeting.id}
                        meeting={meeting}
                        focusTime={reviewFocus}
                        focusVersion={reviewFocusVersion}
                        onSeek={(time) => {
                          seekRef.current?.(time);
                        }}
                        onEdit={() => setWorkspace("transcript")}
                      />
                    )}
                    <div className="review-notes-pane">
                      <NotesView
                        key={meeting.id}
                        inlineReview
                        onConfigureModel={() => setManagementPage("defaults")}
                        focusNoteId={noteSourceId}
                        onNoteFocused={() => setNoteSourceId(undefined)}
                        onLocate={(time) => {
                          locateReview(time);
                          setReviewSourceOpen(true);
                        }}
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
                        onSeek={(time) => {
                          locateReview(time);
                          seekRef.current?.(time);
                        }}
                        toast={toast}
                      />
                    </div>
                  </div>
                )}
                {workspace === "minutes" && (
                  <div
                    className={`meeting-review-workspace ${reviewSourceOpen ? "split" : ""}`}
                  >
                    <button
                      className="review-source-toggle"
                      onClick={() => setReviewSourceOpen(!reviewSourceOpen)}
                    >
                      {reviewSourceOpen ? "收起原文" : "展开原文与回听"}
                    </button>
                    {reviewSourceOpen && (
                      <ReviewSourcePane
                        activeSegment={activeSegment}
                        key={meeting.id}
                        meeting={meeting}
                        focusTime={reviewFocus}
                        focusVersion={reviewFocusVersion}
                        onSeek={(time) => {
                          seekRef.current?.(time);
                        }}
                        onEdit={() => setWorkspace("transcript")}
                      />
                    )}
                    <div className="review-notes-pane">
                      <MinutesView
                        meeting={meeting}
                        environment={environment}
                        draft={minutesDraft}
                        dirty={minutesDirty}
                        transcriptDirty={transcriptDirty}
                        options={minutesOptions}
                        selectedId={selectedMinutesId}
                        templates={
                          (templatesQuery.data || []) as MinutesTemplate[]
                        }
                        model={minutesModel}
                        referenceMode={reviewSourceOpen}
                        showCitations={showCitations}
                        generating={generating}
                        onOptions={changeMinutesOptions}
                        onConfirmInterviewRoles={async (roles) => {
                          const target = meetingRef.current;
                          const brief = target?.flow?.brief;
                          if (
                            !target ||
                            !brief ||
                            transcriptDirty ||
                            (roles && target.revision !== roles.revision)
                          )
                            throw new Error("请先保存并刷新文字稿");
                          const flow = await api.saveBrief(target.id, {
                            ...brief,
                            interview: {
                              candidate: "",
                              role: "",
                              round: "",
                              interviewers: "",
                              focus: "",
                              questions: "",
                              job_description: "",
                              ...brief.interview,
                              speaker_roles: roles,
                            },
                          });
                          setMeeting((old) =>
                            old?.id === target.id ? { ...old, flow } : old,
                          );
                        }}
                        onPreparation={() => setWorkspace("prepare")}
                        onSelect={(id) => void selectMinutes(id)}
                        onAdopt={() => void adoptMinutes()}
                        onCompare={(left, right) =>
                          void compareMinutes(left, right)
                        }
                        onExport={(format) => void exportMinutes(format)}
                        onConfigureModel={() => setManagementPage("defaults")}
                        onCitations={setShowCitations}
                        onGenerate={() => void startMinutes()}
                        onDraft={(value) => {
                          minutesGeneration.current++;
                          minutesDraftRef.current = {
                            draft: value,
                            dirty: true,
                          };
                          setMinutesDraft(value);
                          setMinutesDirty(true);
                        }}
                        onJobAction={(id, action) => void jobAction(id, action)}
                        onSave={() => void saveMinutes()}
                        onPreview={() => void openMinutesPreview()}
                        onSeek={(time, track) => {
                          locateReview(time);
                          setReviewSourceOpen(true);
                          seekRef.current?.(time, track);
                        }}
                      />
                      <MeetingOutcomes
                        focusRequested={actionsFocus}
                        onFocused={() => setActionsFocus(false)}
                        onSeek={(time) => {
                          locateReview(time);
                          setReviewSourceOpen(true);
                          seekRef.current?.(time);
                        }}
                        minutesDirty={minutesDirty}
                        meeting={meeting}
                        onNotes={() => setWorkspace("notes")}
                        onRefresh={async () =>
                          setMeeting(await api.meeting(meeting.id))
                        }
                        toast={toast}
                      />
                      <MinutesDeliveryPanel
                        focusRequested={deliveryFocus}
                        onFocused={() => setDeliveryFocus(false)}
                        onNavigate={(target) => {
                          if (target === "actions") {
                            setActionsFocus(true);
                            navigateReview("minutes");
                          } else navigateReview(target);
                        }}
                        meeting={meeting}
                        selectedId={selectedMinutesId}
                        dirty={minutesDirty || transcriptDirty}
                        includeCitations={showCitations}
                        onRefresh={async () =>
                          setMeeting(await api.meeting(meeting.id))
                        }
                      />
                    </div>
                  </div>
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
              onActiveSegment={setActiveSegment}
              key={meeting.id}
              initialView={playbackRestore}
              onBookmark={(bookmark) =>
                saveMeetingView(meeting.id, bookmark, browserMeetingStorage())
              }
              meetingId={meeting.id}
              segments={meeting.segments}
              seekRef={seekRef}
              pauseRef={pausePlaybackRef}
              audioTracks={meeting.audio_tracks}
            />
          </section>
        )}
      </main>
      <AppModals
        toastText={toastText}
        searchOpen={searchOpen}
        meetings={listQuery.data || []}
        onSearchClose={() => setSearchOpen(false)}
        onSearchHit={async (hit) => {
          const current = await api.resolveSearch(hit);
          const opened = await openMeeting(current.meeting_id);
          if (!opened) throw new Error("会议未能打开，请重试");
          await api.resolveSearch(hit);
          if (current.source_segment_ids.length) setWorkspace("transcript");
          setSearchOpen(false);
          setSearchTarget(current);
        }}
        onManageIndex={() => setManagementPage("index")}
        onConfigureSearch={() => {
          setSearchOpen(false);
          setManagementPage("models");
        }}
        renameOpen={renameOpen}
        meeting={meeting}
        onRenameClose={() => setRenameOpen(false)}
        onRenameSave={async (title) => {
          await api.rename(meeting!.id, title);
          setMeeting((old) => (old ? { ...old, title } : old));
          setRenameOpen(false);
          await client.invalidateQueries({ queryKey: ["meetings"] });
          toast("会议名称已更新");
        }}
        managementPage={managementPage}
        recoveryCount={recordingRecoveryCount(recoveryQuery.data || [])}
        recoveryEngine={engine === "qwen" ? "qwen" : "whisper"}
        taskFocus={taskFocus}
        onManagementClose={() => {
          setTaskFocus(undefined);
          setManagementPage(null);
        }}
        onManagementPageChange={(page) => {
          setTaskFocus(undefined);
          setManagementPage(page);
        }}
        onRecoveryComplete={async (id, options) => {
          await completeRecording(id, options);
          setManagementPage(null);
          void client.invalidateQueries({
            queryKey: ["recording-recoveries"],
          });
        }}
        onRecordingFromManagement={() => {
          setManagementPage(null);
          setRecordingOpen(true);
        }}
        onSearchFromManagement={() => {
          setManagementPage(null);
          setSearchOpen(true);
        }}
        onOpenMeetingFromManagement={(id) => {
          if (recordingOpen) {
            toast("请先返回录音现场；结束录音后可打开其他会议。");
            return;
          }
          setManagementPage(null);
          setSearchOpen(false);
          void openMeeting(id);
        }}
        onEnvironmentChanged={() =>
          void client.invalidateQueries({ queryKey: ["environment"] })
        }
        compareIds={compareIds}
        onCompareClose={() => setCompareIds(null)}
        onSaveCompareSelection={
          compareIds && meeting
            ? (() => {
                const left = meeting.artifacts.find(
                  (a) => a.id === compareIds[0],
                ) as Artifact<MeetingMinutes> | undefined;
                const right = meeting.artifacts.find(
                  (a) => a.id === compareIds[1],
                ) as Artifact<MeetingMinutes> | undefined;
                if (
                  left &&
                  right &&
                  left.id === meeting.active_minutes_id &&
                  !left.stale &&
                  left.payload.template_id === right.payload.template_id &&
                  left.payload.template_version ===
                    right.payload.template_version &&
                  left.payload.template_version >= 2 &&
                  (left.payload.output_language || "zh") ===
                    (right.payload.output_language || "zh") &&
                  !left.payload.interview &&
                  !right.payload.interview
                ) {
                  return async (document: MeetingMinutes) => {
                    await saveMinutes();
                    const base = left.provenance.source_analysis_id;
                    const draft = await api.saveMinutes(
                      meeting.id,
                      typeof base === "string" ? base : null,
                      document,
                      left.id,
                      right.id,
                    );
                    const fresh = await api.meeting(meeting.id);
                    if (meetingRef.current?.id !== meeting.id) return;
                    setMeeting(fresh);
                    displayMinutes(draft);
                    setCompareIds(null);
                    toast("已保存为待采用草稿");
                  };
                }
                return undefined;
              })()
            : undefined
        }
        onAdoptCompareMinutes={() => {
          if (compareIds) void adoptMinutes(compareIds[1]);
        }}
        compareBlockedReason={
          compareIds && meeting
            ? minutesAdoptionBlockReason(
                meeting,
                meeting.artifacts.find(
                  (a) => a.id === compareIds[1],
                ) as Artifact<MeetingMinutes>,
                transcriptDirty,
              )
            : undefined
        }
        previewOpen={previewOpen}
        minutesDraft={minutesDraft}
        selectedMinutesId={selectedMinutesId}
        showCitations={showCitations}
        onPreviewClose={() => setPreviewOpen(false)}
        onExportMinutes={(format) => void exportMinutes(format)}
        followupsOpen={followupsOpen}
        onFollowupsClose={() => setFollowupsOpen(false)}
        onOpenFollowupMeeting={async (id) => {
          await openMeeting(id);
          setWorkspace("minutes");
        }}
        recordingOpen={recordingOpen}
        targetRecordingId={targetRecordingId}
        environment={environment}
        minutesModel={minutesModel}
        onMinutesModelChange={setMinutesModel}
        engineLabel={engineLabel(engine)}
        onRecordingClose={() => {
          setRecordingOpen(false);
          setTargetRecordingId(undefined);
          void client.invalidateQueries({
            queryKey: ["recording-recoveries"],
          });
        }}
        onRecordingComplete={completeRecording}
        onRecordingModels={() => setManagementPage("models")}
        companionId={companionId}
        onCloseCompanion={closeCompanionStatus}
        onCompanionNativeRecording={
          !recordingOpen && !managementPage
            ? (id) => {
                closeCompanionStatus();
                setTargetRecordingId(id);
                setRecordingOpen(true);
              }
            : undefined
        }
        onCompanionMeeting={
          !recordingOpen && !managementPage
            ? (id) => {
                closeCompanionStatus();
                void openMeeting(id);
              }
            : undefined
        }
        toast={toast}
      />
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
  const [sampleOpen, setSampleOpen] = useState(false);
  return (
    <section id="welcome">
      {sampleOpen && (
        <SampleMeetingDialog onClose={() => setSampleOpen(false)} />
      )}
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
        <strong>导入已有录音 · 拖放或点击选择</strong>
        <span>WAV / MP3 / M4A / MP4 等 · 最大 4GB</span>
      </button>
      <button className="welcome-record" onClick={onRecord}>
        <span>●</span>
        <strong>开始线上或现场会议</strong>
        <small>录音时可随手记下重点、问题、决策和待办</small>
      </button>
      <button className="text-btn" onClick={() => setSampleOpen(true)}>
        先体验样例会议 · 无需配置模型 →
      </button>
      <div className="features">
        <div>
          <b>01</b>
          <h3>本地转写</h3>
          <p>中文与英文，支持自动识别及长会议分段处理</p>
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
