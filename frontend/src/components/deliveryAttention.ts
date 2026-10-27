import type { Meeting } from "../types";

// Personal and explicitly deferred notes remain available without driving delivery.
export function deliveryAttentionCount(meeting: Meeting) {
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
