import type { MeetingNoteInput } from "../../types";
import type { CompletionMode } from "./recordingPreferences";
import RecordingReceipt from "./RecordingReceipt";

export default function RecordingCompletionPanel({
  notes,
  savedMeetingId,
  savedEngine,
  handoffError,
  recordOnly,
  completing,
  completionMode,
  postGoal,
  engineLabel,
  onOpen,
  onProcess,
}: {
  notes: MeetingNoteInput[];
  savedMeetingId: string;
  savedEngine: "whisper" | "qwen";
  handoffError: string;
  recordOnly: boolean;
  completing: boolean;
  completionMode: CompletionMode;
  postGoal: "transcript" | "minutes";
  engineLabel: string;
  onOpen: () => Promise<void>;
  onProcess: (mode: CompletionMode) => Promise<void>;
}) {
  return (
    <section
      className="recording-finish-panel"
      aria-label="录音结束后的处理选择"
    >
      <h3>录音与现场关注已保存</h3>
      <p>
        本次留下 {notes.length} 个关注点：
        {notes.filter((n) => n.note_type === "question").length} 个问题、
        {notes.filter((n) => n.note_type === "action").length}{" "}
        个待办线索。进入会后整理后核对用途，再生成纪要。
      </p>
      <RecordingReceipt id={savedMeetingId} engine={savedEngine} />
      {handoffError && (
        <p role="alert" className="health-warning">
          {handoffError} 录音与关注点已保留，重试不会重复创建处理任务。
        </p>
      )}
      <button
        className="primary"
        disabled={completing}
        onClick={() => void (recordOnly ? onOpen() : onProcess(completionMode))}
      >
        {recordOnly ? "打开已保存的会议" : "进入会后整理"}
      </button>
      {recordOnly ? (
        <p>本次未启用实时识别。原音与简记已保存，配置模型后可继续转写。</p>
      ) : (
        <p>
          将沿用上次选择：
          {completionMode === "quick"
            ? "快速完成文字稿"
            : completionMode === "full"
              ? "精细处理文字稿"
              : "先保留录音，暂不转写"}
          。
          {postGoal === "minutes" && completionMode !== "none"
            ? "文字稿完成后将自动生成 AI 纪要草稿。"
            : ""}
          原音与关注点已经保存。
        </p>
      )}
      <details>
        <summary>调整文字稿处理方式</summary>
        <div className="finish-options">
          {(
            [
              {
                mode: "quick",
                title: "快速完成",
                description: `复用 ${savedEngine === "qwen" ? "千问" : "Whisper"} 实时结果，补齐缺段与尾段；不区分发言人。`,
              },
              {
                mode: "full",
                title: "精细处理",
                description: `使用 ${engineLabel} 重识别、时间对齐，按会议设置区分发言人。`,
              },
              {
                mode: "none",
                title: "先整理关注点",
                description: "保留录音和简记，稍后在会议中启动转写。",
              },
            ] as const
          ).map((choice) => (
            <button
              key={choice.mode}
              className={completionMode === choice.mode ? "selected" : ""}
              disabled={completing}
              onClick={() => void onProcess(choice.mode)}
            >
              <strong>
                {choice.title}
                {completionMode === choice.mode ? " · 上次选择" : ""}
              </strong>
              <span>{choice.description}</span>
            </button>
          ))}
        </div>
      </details>
      {completing && <p role="status">正在确认会后任务并打开会议…</p>}
    </section>
  );
}
