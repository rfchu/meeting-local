import type { Meeting, ReviewStep } from "../types";
import { currentMinutes, isRunning, latestMinutes } from "../utils";
import { reviewProgress } from "./reviewProgress";

interface Props {
  meeting: Meeting;
  transcriptDirty: boolean;
  minutesDirty: boolean;
  reviewBusy: boolean;
  onTranscribe: () => void;
  onSave: () => void;
  onAnalysis: () => void;
  onMinutes: () => void;
  onPreview: () => void;
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
  const progress = reviewProgress(meeting, transcriptDirty, minutesDirty);
  const minutes = currentMinutes(meeting),
    busy = meeting.jobs.find(isRunning);
  if (!meeting.review)
    return (
      <section className="workflow" aria-label="会议处理进度">
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
  } else if (
    meeting.flow &&
    meeting.flow.counts.pending +
      meeting.flow.counts.stale +
      meeting.flow.counts.deferred >
      0
  ) {
    hint = `你留下的关注点还有 ${meeting.flow.counts.pending + meeting.flow.counts.stale + meeting.flow.counts.deferred} 个待处理，可先整理，再生成纪要。`;
    label = "核对现场关注";
    action = () => onReview("notes");
  } else if (progress.next && progress.next.key !== "minutes") {
    const step = progress.next.key;
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
  } else if (progress.next) {
    hint = "纪要已生成，请检查内容与最终版式后记录核对结果。";
    label = "检查会议纪要";
    action = onMinutes;
  } else {
    hint = "核对清单已完成。发送前可预览并导出 Word 或 PDF。";
    label = "预览并导出";
    action = onPreview;
  }
  return (
    <section className="workflow" aria-label="会议处理进度">
      <div className="workflow-next">
        <span>{hint}</span>
        <button className="primary" disabled={!!busy} onClick={action}>
          {label}
        </button>
      </div>
      <details className="postmeeting-review">
        <summary>
          会后核对清单 · {progress.complete}/4
          {progress.stale ? ` · ${progress.stale} 项待复核` : ""}
        </summary>
        <p>
          人工核对记录只表示你的确认，不等同于 AI
          已验证；不限制独立生成分析或纪要。
        </p>
        <div className="review-grid">
          {progress.steps.map(({ key, state, item }) => (
            <article key={key} className={`review-card ${state}`}>
              <strong>{labels[key]}</strong>
              <span className="review-state">
                {state === "blocked" &&
                key === "minutes" &&
                latestMinutes(meeting)
                  ? "纪要已过期，请更新"
                  : {
                      done:
                        item?.decision === "skipped" ? "本次不区分" : "已核对",
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
      <div className="review-optional">
        <button className="text-btn" onClick={onAnalysis}>
          会议分析（可选）
        </button>
        <span>未完成清单也可使用各功能；核对标记不会自动发送任何资料。</span>
      </div>
    </section>
  );
}
