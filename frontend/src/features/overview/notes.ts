import type { MeetingNoteInput, LiveDraftSegment } from "../../types";
export function ensureNoteIds(
  notes: MeetingNoteInput[],
  create: () => string,
): MeetingNoteInput[] {
  return notes.map((note) =>
    note.client_id ? note : { ...note, client_id: create() },
  );
}
export function toggleOverviewFocus(note: MeetingNoteInput): MeetingNoteInput {
  if (!note.raw_text.trim() || note.note_scope === "meeting") return note;
  return { ...note, overview_focus: !note.overview_focus };
}
export function draftSourceId(part: LiveDraftSegment): string | null {
  return part.live_range_id && typeof part.live_segment_index === "number"
    ? `${part.live_range_id}:${part.live_segment_index}`
    : null;
}

/** Server-owned notes remain in the companion timeline during recording recovery. */
export function browserOwnedNotes(
  notes: MeetingNoteInput[],
): MeetingNoteInput[] {
  return notes.filter((note) => !("status" in note));
}
