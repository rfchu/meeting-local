import { currentHandoff } from "./handoff";
import type { Meeting } from "../types";

// Personal and explicitly deferred notes remain available without driving delivery.
export function deliveryAttentionCount(meeting: Meeting) {
  const projected = currentHandoff(meeting)?.stages.find(
    (stage) => stage.key === "notes",
  )?.counts.pending_meeting;
  if (typeof projected === "number") return projected;
  return meeting.notes.filter((note) => {
    const entry = meeting.flow?.attention.find(
      (item) => item.note_id === note.id,
    );
    const decision = entry?.review?.decision;
    if (decision === "personal" || decision === "deferred") return false;
    const pending = !entry || ["pending", "stale"].includes(entry.state);
    return pending && (note.note_scope === "meeting" || decision === "meeting");
  }).length;
}
