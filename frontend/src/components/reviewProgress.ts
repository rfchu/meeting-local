import type { Meeting, ReviewStep } from "../types";
import { currentMinutes } from "../utils";

export function reviewProgress(
  meeting: Meeting,
  transcriptDirty: boolean,
  minutesDirty: boolean,
) {
  const hasTranscript = !!meeting.revision && meeting.segments.length > 0;
  const steps = (
    ["transcript", "speakers", "notes", "minutes"] as ReviewStep[]
  ).map((key) => {
    const item = meeting.review?.steps[key];
    let state =
      item?.state ||
      (key === "notes" && !meeting.notes.length
        ? "not_needed"
        : !hasTranscript || (key === "minutes" && !currentMinutes(meeting))
          ? "blocked"
          : "pending");
    if (
      state !== "blocked" &&
      state !== "not_needed" &&
      (transcriptDirty ||
        (meeting.review && meeting.review.revision !== meeting.revision) ||
        ((key === "transcript" || key === "minutes") &&
          meeting.review &&
          meeting.review.title !== meeting.title) ||
        (key === "minutes" &&
          (minutesDirty ||
            (meeting.review &&
              meeting.review.minutes_id !== currentMinutes(meeting)?.id))))
    )
      state = "stale";
    return { key, state, item };
  });
  const next = steps.find(
    (s) => s.state !== "done" && s.state !== "not_needed",
  );
  return {
    steps,
    next,
    complete: steps.filter(
      (s) => s.state === "done" || s.state === "not_needed",
    ).length,
    stale: steps.filter((s) => s.state === "stale").length,
    hasTranscript,
  };
}
