import { useState } from "react";
import type { InterviewSpeakerRoles, Meeting } from "../../types";

export default function InterviewRolesForm({
  meeting,
  disabled,
  onConfirm,
}: {
  meeting: Meeting;
  disabled: boolean;
  onConfirm: (roles: InterviewSpeakerRoles) => Promise<void>;
}) {
  const saved = meeting.flow?.brief.interview?.speaker_roles;
  const confirmed = saved?.revision === meeting.revision ? saved : undefined;
  const [candidate, setCandidate] = useState(confirmed?.candidate || "");
  const [interviewers, setInterviewers] = useState(
    confirmed?.interviewers || [],
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const speakers = [
    ...new Set(meeting.segments.map((segment) => segment.speaker)),
  ].filter((speaker) => speaker && speaker !== "未分配");
  const valid =
    speakers.includes(candidate) &&
    interviewers.length > 0 &&
    interviewers.every(
      (speaker) => speaker !== candidate && speakers.includes(speaker),
    );
  return (
    <fieldset disabled={disabled || saving || speakers.length < 2}>
      <legend>确认面试发言人</legend>
      <p className="muted">
        {confirmed
          ? `已确认候选人 ${confirmed.candidate}，面试官 ${confirmed.interviewers.join("、")}。`
          : "生成前请确认候选人和面试官。"}
        若一个音轨包含多个角色，请先在文字稿中区分发言人。
      </p>
      <label>
        候选人
        <select
          value={candidate}
          onChange={(event) => {
            setCandidate(event.target.value);
            setInterviewers(
              interviewers.filter((speaker) => speaker !== event.target.value),
            );
          }}
        >
          <option value="">请选择候选人</option>
          {speakers.map((speaker) => (
            <option key={speaker}>{speaker}</option>
          ))}
        </select>
      </label>
      <div>面试官（可多选）</div>
      {speakers
        .filter((speaker) => speaker !== candidate)
        .map((speaker) => (
          <label key={speaker}>
            <input
              type="checkbox"
              checked={interviewers.includes(speaker)}
              onChange={(event) =>
                setInterviewers(
                  event.target.checked
                    ? [...interviewers, speaker]
                    : interviewers.filter((item) => item !== speaker),
                )
              }
            />
            {speaker}
          </label>
        ))}
      <button
        type="button"
        disabled={!valid}
        onClick={async () => {
          setSaving(true);
          setError("");
          try {
            await onConfirm({
              revision: meeting.revision,
              candidate,
              interviewers,
            });
          } catch (error) {
            setError((error as Error).message);
          } finally {
            setSaving(false);
          }
        }}
      >
        {saving ? "正在保存…" : "确认角色"}
      </button>
      {error && <p role="alert">{error}</p>}
    </fieldset>
  );
}
