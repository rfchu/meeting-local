import type { MeetingBrief, MeetingSummary } from "../../types";
export const emptyBrief: MeetingBrief = {
  source_language: "auto",
  output_language: "zh",
  title: "",
  purpose: "",
  agenda: "",
  participants: "",
  questions: "",
  previous_meeting_id: null,
};
export default function MeetingBriefForm({
  value,
  onChange,
  meetings = [],
  currentId,
  disabled = false,
}: {
  value: MeetingBrief;
  onChange: (v: MeetingBrief) => void;
  meetings?: MeetingSummary[];
  currentId?: string;
  disabled?: boolean;
}) {
  const fields = [
    ["title", "会议主题", 200],
    ["purpose", "本次希望达成什么", 1000],
    ["agenda", "议程／讨论背景", 3000],
    ["participants", "计划参会人", 1000],
    ["questions", "希望确认的问题", 2000],
  ] as const;
  return (
    <fieldset disabled={disabled} className="meeting-brief-form">
      <label>
        原文语言
        <select
          aria-label="会议原文语言"
          value={value.source_language || "zh"}
          onChange={(event) =>
            onChange({
              ...value,
              source_language: event.target
                .value as MeetingBrief["source_language"],
            })
          }
        >
          <option value="auto">自动识别（中英）</option>
          <option value="zh">中文</option>
          <option value="en">英文</option>
          <option value="ja">日语（Beta）</option>
        </select>
      </label>
      <label>
        纪要输出语言
        <select
          aria-label="会议纪要输出语言"
          value={value.output_language || "zh"}
          onChange={(event) =>
            onChange({
              ...value,
              output_language: event.target
                .value as MeetingBrief["output_language"],
            })
          }
        >
          <option value="zh">中文</option>
          <option value="en">英文</option>
        </select>
      </label>
      {fields.map(([key, label, max]) => (
        <label key={key}>
          {label}
          {key === "title" || key === "participants" ? (
            <input
              aria-label={label}
              maxLength={max}
              value={value[key]}
              onChange={(e) => onChange({ ...value, [key]: e.target.value })}
            />
          ) : (
            <textarea
              aria-label={label}
              rows={2}
              maxLength={max}
              value={value[key]}
              onChange={(e) => onChange({ ...value, [key]: e.target.value })}
            />
          )}
        </label>
      ))}
      <label>
        关联上次会议
        <select
          aria-label="关联上次会议"
          value={value.previous_meeting_id || ""}
          onChange={(e) =>
            onChange({ ...value, previous_meeting_id: e.target.value || null })
          }
        >
          <option value="">不关联</option>
          {meetings
            .filter((m) => m.id !== currentId)
            .map((m) => (
              <option key={m.id} value={m.id}>
                {m.title}
              </option>
            ))}
        </select>
      </label>
      <details className="interview-preparation">
        <summary>
          面试准备（可选）
          {value.interview?.role ? ` · ${value.interview.role}` : ""}
        </summary>
        <p>
          填写岗位与本轮重点。未填写时仅按原文作通用面试记录；准备内容不代表候选人的实际回答。
        </p>
        {(
          [
            ["candidate", "候选人姓名／代称", 200],
            ["role", "应聘岗位", 300],
            ["round", "面试轮次", 200],
            ["interviewers", "计划面试官", 1000],
            ["focus", "本轮考察维度", 3000],
            ["questions", "预设面试问题", 3000],
            ["job_description", "岗位说明（选填）", 6000],
          ] as const
        ).map(([key, label, max]) => (
          <label key={key}>
            {label}
            {max <= 1000 ? (
              <input
                maxLength={max}
                value={value.interview?.[key] || ""}
                onChange={(e) =>
                  onChange({
                    ...value,
                    interview: {
                      candidate: "",
                      role: "",
                      round: "",
                      interviewers: "",
                      focus: "",
                      questions: "",
                      job_description: "",
                      ...value.interview,
                      [key]: e.target.value,
                    },
                  })
                }
              />
            ) : (
              <textarea
                rows={3}
                maxLength={max}
                value={value.interview?.[key] || ""}
                onChange={(e) =>
                  onChange({
                    ...value,
                    interview: {
                      candidate: "",
                      role: "",
                      round: "",
                      interviewers: "",
                      focus: "",
                      questions: "",
                      job_description: "",
                      ...value.interview,
                      [key]: e.target.value,
                    },
                  })
                }
              />
            )}
          </label>
        ))}
        {value.interview && (
          <button onClick={() => onChange({ ...value, interview: null })}>
            清空面试准备
          </button>
        )}
      </details>
      <small>
        这些内容用于准备和引导整理，不代表议题已经讨论或形成决定。个人判断请保留在“我的关注”中。
      </small>
    </fieldset>
  );
}
