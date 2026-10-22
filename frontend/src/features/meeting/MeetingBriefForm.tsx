import type { MeetingBrief, MeetingSummary } from "../../types";
export const emptyBrief: MeetingBrief = {
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
      <small>
        这些内容用于准备和引导整理，不代表议题已经讨论或形成决定。个人判断请保留在“我的关注”中。
      </small>
    </fieldset>
  );
}
