import { useState } from "react";
import HandoffSourcesDialog from "../features/meeting/HandoffSourcesDialog";
import type { HandoffSource } from "../features/meeting/handoffSources";
import {
  currentHandoff,
  handoffAction,
  handoffTaskFocus,
  handoffStates,
  handoffRelations,
} from "./handoff";
import { recordingQualityAttention } from "../features/notes/recordingQuality";
import { deliveryAttentionCount } from "./deliveryAttention";
import { pendingMinutesDrafts } from "../features/minutes/presentation";
import type { Meeting, ReviewStep, TaskFocus } from "../types";
import { currentMinutes, isRunning, latestMinutes } from "../utils";
import { reviewProgress } from "./reviewProgress";

interface Props {
  meeting: Meeting;
  transcriptDirty: boolean;
  minutesDirty: boolean;
  reviewBusy: boolean;
  onSourceLocate?: (source: HandoffSource) => void;
  onTranscribe: () => void;
  onRecordingReview?: () => void;
  onSave: () => void;
  onAnalysis: () => void;
  onTasks?: (focus?: TaskFocus) => void;
  onActions?: () => void;
  onOverview?: () => void;
  onMinutes: () => void;
  onPreview: () => void;
  onDelivery?: () => void;
  onReview: (step: ReviewStep) => void;
  onConfirm: (
    step: ReviewStep,
    decision: "confirmed" | "skipped" | "reset",
  ) => void;
}
const labels: Record<ReviewStep, string> = {
  transcript: "文字稿与主题",
  speakers: "发言人",
  notes: "现场关注与用途",
  minutes: "纪要发送前检查",
};
const hints: Record<ReviewStep, string> = {
  transcript: "回听重点和分段边界，核对主题、人名、数字及关键表述。",
  speakers: "确认人物对应；也可以明确选择本次不区分。",
  notes: "核对附近原文，区分个人备注与会议内容，再整理待办。",
  minutes: "核对决策、负责人、期限和个人信息，再预览导出。",
};

export default function Workflow(props: Props) {
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const {
    meeting,
    transcriptDirty,
    minutesDirty,
    reviewBusy,
    onTranscribe,
    onSave,
    onAnalysis,
    onMinutes,
    onPreview,
    onReview,
    onConfirm,
  } = props;
  const qualityAttention = recordingQualityAttention(meeting.recording_quality);
  const openRecordingReview =
    props.onRecordingReview || (() => onReview("transcript"));
  const qualityNotice = qualityAttention && (
    <div className="workflow-quality" role="status">
      <span>{qualityAttention.text}</span>
      <button className="text-btn" onClick={openRecordingReview}>
        {qualityAttention.action}
      </button>
    </div>
  );
  const progress = reviewProgress(meeting, transcriptDirty, minutesDirty);
  const pendingDrafts = pendingMinutesDrafts(meeting);
  const automaticMinutes = meeting.jobs.find(
    (job) => job.kind === "minutes" && job.state === "completed",
  );
  const deliveryAttention = deliveryAttentionCount(meeting);
  const nextRequired = progress.steps.find(
    (step) =>
      step.state !== "done" &&
      step.state !== "not_needed" &&
      (step.key !== "notes" || deliveryAttention > 0),
  );
  const minutes = currentMinutes(meeting),
    busy = meeting.jobs.find(isRunning);
  if (!meeting.review)
    return (
      <section className="workflow" aria-label="会议处理进度">
        {qualityNotice}
        <p>
          会后核对清单将在本次录音结束、服务更新后启用。现有整理功能可以照常使用。
        </p>
        <div className="review-optional">
          <button
            disabled={!!busy}
            onClick={
              progress.hasTranscript
                ? () => onReview("transcript")
                : onTranscribe
            }
          >
            {progress.hasTranscript ? "打开文字稿" : "开始转写"}
          </button>
          <button className="text-btn" onClick={onMinutes}>
            打开纪要
          </button>
          <button className="text-btn" onClick={onAnalysis}>
            会议分析（可选）
          </button>
        </div>
      </section>
    );
  const blocked =
    !!busy ||
    reviewBusy ||
    transcriptDirty ||
    meeting.review?.revision !== meeting.revision;
  let hint = "",
    label = "",
    action = onTranscribe;
  if (busy) {
    hint = busy.stage;
    label = "处理中…";
  } else if (meeting.minutes_generation?.state === "running") {
    hint = `AI 纪要正在整理文字稿，已等待 ${meeting.minutes_generation.elapsed_seconds} 秒，完整返回后自动保存。`;
    label = "查看生成状态";
    action = onMinutes;
  } else if (
    automaticMinutes &&
    minutes?.provenance.job === automaticMinutes.id &&
    meeting.review?.steps.minutes.state !== "done" &&
    !transcriptDirty &&
    !minutesDirty
  ) {
    hint = "自动纪要草稿已保存，可先核对决策、负责人和期限，再预览导出。";
    label = "核对自动纪要";
    action = onMinutes;
  } else if (pendingDrafts.length && !transcriptDirty) {
    hint = `有 ${pendingDrafts.length} 份纪要草稿待核对，当前采用稿保持不变；可对比后采用。`;
    label = "查看纪要草稿";
    action = onMinutes;
  } else if (meeting.minutes_generation?.state === "failed") {
    hint = "上次 AI 纪要未生成成功，失败原因已保留；已有文稿和纪要不受影响。";
    label = "查看原因与重试";
    action = onMinutes;
  } else if (!progress.hasTranscript) {
    hint = "下一步：将已保存的原音转为文字稿，再开始会后核对。";
    label = "开始转写";
  } else if (transcriptDirty) {
    hint = "文字稿有未保存修改，保存后重新核对受影响的内容。";
    label = "保存文字稿";
    action = onSave;
  } else if (qualityAttention?.kind === "pending") {
    hint = `${qualityAttention.text} 先回听附近音频，再核对文字和关键结论。`;
    label = "核对录音";
    action = openRecordingReview;
  } else if (deliveryAttention > 0) {
    hint = `有 ${deliveryAttention} 个准备用于会议成果的关注点需核对；个人与暂缓记录可稍后整理。`;
    label = "核对会议关注点";
    action = () => onReview("notes");
  } else if (nextRequired && nextRequired.key !== "minutes") {
    const step = nextRequired.key;
    hint = progress.stale
      ? "内容发生变化，受影响的核对项需要重新确认。"
      : "按清单核对后继续整理；核对进度会保存到本机。";
    label = `去核对${labels[step]}`;
    action = () => onReview(step);
  } else if (!minutes) {
    hint = latestMinutes(meeting)
      ? "现有纪要对应旧文字稿，请更新纪要。"
      : "可直接从文字稿生成 AI 纪要，会议分析可按需使用。";
    label = latestMinutes(meeting) ? "更新会议纪要" : "生成会议纪要";
    action = onMinutes;
  } else if (minutesDirty) {
    hint = "纪要有未保存修改，保存后再确认发送前检查。";
    label = "查看并保存纪要";
    action = onMinutes;
  } else if (nextRequired) {
    hint = "纪要已生成，请检查内容与最终版式后记录核对结果。";
    label = "检查会议纪要";
    action = onMinutes;
  } else {
    hint =
      progress.complete === 4
        ? qualityAttention?.kind === "gap"
          ? "核对清单已完成；录音有已确认缺音，发送前请核对受影响的结论。"
          : "核对清单已完成。发送前可预览并导出 Word 或 PDF。"
        : "本次交付相关核对已完成，可预览导出；个人与暂缓记录可稍后整理。";
    label = "预览并导出";
    const delivered = meeting.deliveries?.some(
      (d) => d.artifact_id === meeting.active_minutes_id,
    );
    label = delivered ? "预览当前稿" : "确认交付版本";
    action = delivered ? onPreview : onMinutes;
  }
  const handoff =
    transcriptDirty || minutesDirty ? null : currentHandoff(meeting);
  const guidance = handoffAction(meeting, transcriptDirty, minutesDirty);
  const openTarget = (target: string, jobId?: string) => {
    switch (target) {
      case "tasks":
        if (props.onTasks)
          return props.onTasks(handoffTaskFocus(meeting, jobId));
        return onAnalysis();
      case "recording":
        return openRecordingReview();
      case "transcribe":
        return onTranscribe();
      case "transcript":
        return onReview("transcript");
      case "speakers":
        return onReview("speakers");
      case "overview":
        return meeting.live_overview_available && props.onOverview
          ? props.onOverview()
          : onReview("notes");
      case "delivery":
        return (props.onDelivery || onMinutes)();
      case "actions":
        return (props.onActions || onMinutes)();
      case "notes":
        return onReview("notes");
      case "preview":
        return onPreview();
      default:
        return onMinutes();
    }
  };
  const followGuidance = () => {
    if (!guidance) return onMinutes();
    if (
      ["delivery", "review_minutes", "preview"].includes(guidance.code) &&
      props.onDelivery
    )
      return props.onDelivery();
    openTarget(guidance.target);
  };
  if (guidance) {
    hint = guidance.summary;
    label = guidance.label;
    action = followGuidance;
  }
  return (
    <section className="workflow" aria-label="会议处理进度">
      {label !== "核对录音" && qualityNotice}
      <div className="workflow-next">
        <span>{hint}</span>
        <button
          className="primary"
          disabled={!!busy && !guidance}
          onClick={action}
        >
          {label}
        </button>
      </div>
      {sourcesOpen && (
        <HandoffSourcesDialog
          key={meeting.id}
          meeting={meeting}
          dirty={transcriptDirty || minutesDirty}
          onClose={() => setSourcesOpen(false)}
          onContinue={() => {
            setSourcesOpen(false);
            followGuidance();
          }}
          onLocate={(source) => {
            setSourcesOpen(false);
            if (props.onSourceLocate) props.onSourceLocate(source);
            else onReview("transcript");
          }}
        />
      )}
      <details className="postmeeting-review">
        <summary>
          本次交接与核对 · {progress.requiredComplete}/{progress.requiredTotal}
          {progress.requiredStale
            ? ` · ${progress.requiredStale} 项待复核`
            : ""}
        </summary>
        <p>
          人工核对记录只表示你的确认，不等同于 AI
          已验证；不限制独立生成分析或纪要。个人与暂缓记录不阻碍纪要交付。
        </p>
        {handoff && (
          <div className="handoff-stages" aria-label="会后交接状态">
            {handoff.stages.map((stage) => (
              <div className={`handoff-stage ${stage.state}`} key={stage.key}>
                <strong>{stage.label}</strong>
                <span>
                  {stage.key === "overview" && stage.state === "review"
                    ? "按需核对"
                    : handoffStates[stage.state]}
                </span>
                <p>{stage.summary}</p>
                <button
                  className="text-btn"
                  onClick={() =>
                    openTarget(
                      stage.source.interrupted_job_id
                        ? "tasks"
                        : stage.key === "delivery"
                          ? "delivery"
                          : stage.key,
                      typeof stage.source.interrupted_job_id === "string"
                        ? stage.source.interrupted_job_id
                        : undefined,
                    )
                  }
                >
                  查看
                </button>
              </div>
            ))}
            {!!handoff.edges.length && (
              <p className="handoff-lineage">
                来源关系：
                {handoff.edges
                  .map(
                    (edge) =>
                      `${handoff.stages.find((stage) => stage.key === edge.from)?.label || "实时识别"} → ${handoff.stages.find((stage) => stage.key === edge.to)?.label || edge.to} · ${handoffRelations[edge.relation]} ${edge.count} 项`,
                  )
                  .join("；")}
              </p>
            )}
          </div>
        )}
        {handoff && (
          <button
            disabled={transcriptDirty || minutesDirty}
            onClick={() => setSourcesOpen(true)}
          >
            查看具体来源与处理
          </button>
        )}
        {handoff && <h4>人工核对记录</h4>}
        <div className="review-grid">
          {progress.steps.map(({ key, state, item }) => (
            <article key={key} className={`review-card ${state}`}>
              <strong>{labels[key]}</strong>
              <span className="review-state">
                {key === "notes" &&
                deliveryAttention === 0 &&
                ["pending", "stale"].includes(state)
                  ? "可稍后整理"
                  : state === "blocked" &&
                      key === "minutes" &&
                      latestMinutes(meeting)
                    ? "纪要已过期，请更新"
                    : {
                        done:
                          item?.decision === "skipped"
                            ? "本次不区分"
                            : "已核对",
                        pending: "待核对",
                        stale: "内容变化，待复核",
                        blocked: "等待内容",
                        not_needed: "无简记，无需核对",
                      }[state]}
              </span>
              <p>{hints[key]}</p>
              <div>
                <button className="text-btn" onClick={() => onReview(key)}>
                  打开
                  {key === "speakers"
                    ? "文字稿"
                    : key === "minutes"
                      ? "纪要"
                      : key === "notes"
                        ? "简记"
                        : "文字稿"}
                </button>
                {state === "done" ? (
                  <button
                    className="text-btn"
                    disabled={blocked || (key === "minutes" && minutesDirty)}
                    onClick={() => onConfirm(key, "reset")}
                  >
                    撤回确认
                  </button>
                ) : (
                  state !== "not_needed" && (
                    <button
                      disabled={
                        blocked ||
                        state === "blocked" ||
                        (key === "minutes" && minutesDirty)
                      }
                      onClick={() => onConfirm(key, "confirmed")}
                    >
                      确认已核对
                    </button>
                  )
                )}
                {key === "speakers" && state !== "done" && (
                  <button
                    className="text-btn"
                    disabled={blocked || state === "blocked"}
                    onClick={() => onConfirm(key, "skipped")}
                  >
                    本次不区分
                  </button>
                )}
              </div>
            </article>
          ))}
        </div>
      </details>
    </section>
  );
}
