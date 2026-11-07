import { useState } from "react";
import type { InterviewSpeakerRoles, Meeting } from "../../types";

export default function InterviewRolesForm({
  meeting,
  disabled,
  onConfirm,
  onPreparation,
}: {
  meeting: Meeting;
  disabled: boolean;
  onConfirm: (roles: InterviewSpeakerRoles | null) => Promise<void>;
  onPreparation: () => void;
}) {
  const saved = meeting.flow?.brief.interview?.speaker_roles;
  const confirmed = saved?.revision === meeting.revision ? saved : undefined;
  const [candidate, setCandidate] = useState(confirmed?.candidate || "");
  const [interviewers, setInterviewers] = useState(
    confirmed?.interviewers || [],
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState(false);
  const speakers = [
    ...new Set(meeting.segments.map((segment) => segment.speaker)),
  ].filter((speaker) => speaker && speaker !== "未分配");
  const valid =
    speakers.includes(candidate) &&
    interviewers.length > 0 &&
    interviewers.every(
      (speaker) => speaker !== candidate && speakers.includes(speaker),
    );

  const save = async (roles: InterviewSpeakerRoles | null) => {
    setSaving(true);
    setError("");
    try {
      await onConfirm(roles);
      if (!roles) {
        setCandidate("");
        setInterviewers([]);
      }
      setExpanded(false);
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="interview-role-settings">
      <details
        open={expanded}
        onToggle={(event) => setExpanded(event.currentTarget.open)}
      >
        <summary>
          <strong>发言人角色</strong>
          <span>
            {confirmed ? `候选人 · ${confirmed.candidate}` : "可选 · 未指定"}
          </span>
        </summary>
        {speakers.length < 2 ? (
          <p>可以直接生成纪要。需要按角色校验时，先在文字稿中区分发言人。</p>
        ) : (
          <fieldset disabled={disabled || saving}>
            <label>
              候选人
              <select
                value={candidate}
                onChange={(event) => {
                  setCandidate(event.target.value);
                  setInterviewers(
                    interviewers.filter(
                      (speaker) => speaker !== event.target.value,
                    ),
                  );
                }}
              >
                <option value="">不指定</option>
                {speakers.map((speaker) => (
                  <option key={speaker}>{speaker}</option>
                ))}
              </select>
            </label>
            <div className="interview-role-choices">
              <span>面试官</span>
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
            </div>
            <button
              type="button"
              disabled={!valid}
              onClick={() =>
                void save({
                  revision: meeting.revision,
                  candidate,
                  interviewers,
                })
              }
            >
              {saving ? "保存中…" : "确认角色"}
            </button>
            {saved && (
              <button
                type="button"
                className="text-btn"
                onClick={() => void save(null)}
              >
                清除角色
              </button>
            )}
          </fieldset>
        )}
        {error && <p role="alert">{error}</p>}
      </details>
      <button type="button" className="text-btn" onClick={onPreparation}>
        面试准备
      </button>
    </div>
  );
}
