import type { Meeting } from "../types";
import {
  currentAnalysis,
  currentMinutes,
  isRunning,
  latestAnalysis,
  latestJob,
  latestMinutes,
} from "../utils";

interface Props {
  meeting: Meeting;
  transcriptDirty: boolean;
  onTranscribe: () => void;
  onSave: () => void;
  onAnalysis: () => void;
  onMinutes: () => void;
  onPreview: () => void;
}

export default function Workflow({
  meeting,
  transcriptDirty,
  onTranscribe,
  onSave,
  onAnalysis,
  onMinutes,
  onPreview,
}: Props) {
  const transcript = !!meeting.revision;
  const analysis = currentAnalysis(meeting);
  const minutes = currentMinutes(meeting);
  const busy = meeting.jobs.find(isRunning);
  const steps: [string, string][] = [
    ["录音已导入", "done"],
    ["文字稿", transcript ? "done" : "active"],
    [
      "会议分析",
      analysis && !transcriptDirty
        ? "done"
        : latestAnalysis(meeting)
          ? "stale"
          : transcript
            ? "active"
            : "",
    ],
    [
      "会议纪要",
      minutes && !transcriptDirty
        ? "done"
        : latestMinutes(meeting)
          ? "stale"
          : analysis
            ? "active"
            : "",
    ],
  ];
  let hint = "",
    label = "",
    action = onTranscribe;
  if (busy) {
    hint = busy.stage;
    label = "处理中…";
  } else if (!transcript) {
    hint = "下一步：选择识别引擎，将录音转换成文字稿。";
    label = "开始转写";
  } else if (transcriptDirty) {
    hint = "文字稿有未保存修改，保存后分析和纪要会标记为过期。";
    label = "保存文字稿";
    action = onSave;
  } else if (!analysis) {
    hint = latestAnalysis(meeting)
      ? "文字稿已经更新，请重新生成会议分析。"
      : "下一步：提取摘要、决策、待办和待确认问题。";
    label = latestAnalysis(meeting) ? "更新会议分析" : "生成会议分析";
    action = onAnalysis;
  } else if (!minutes) {
    hint = latestMinutes(meeting)
      ? "文字稿已经更新，请重新生成会议纪要。"
      : "下一步：生成可编辑、可对外发送的会议纪要。";
    label = latestMinutes(meeting) ? "更新会议纪要" : "生成会议纪要";
    action = onMinutes;
  } else {
    hint = "处理已完成。发送前可预览最终版式并导出 Word 或 PDF。";
    label = "预览并导出";
    action = onPreview;
  }
  return (
    <section className="workflow" aria-label="会议处理进度">
      <div className="workflow-steps">
        {steps.map(([name, state]) => (
          <div key={name} className={`workflow-step ${state}`}>
            {name}
          </div>
        ))}
      </div>
      <div className="workflow-next">
        <span>{hint}</span>
        <button className="primary" disabled={!!busy} onClick={action}>
          {label}
        </button>
      </div>
    </section>
  );
}
