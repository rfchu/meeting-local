import type { Artifact, MeetingMinutes, Meeting, Workspace } from "../types";
import { latestMinutes } from "../utils";
export interface MeetingView {
  workspace?: Workspace;
  minutesId?: string | null;
  position?: number;
  track?: string;
  rate?: number;
}
const KEY = "tingji.meeting.views.v1";
type StorageLike = Pick<Storage, "getItem" | "setItem">;
function read(storage: Pick<Storage, "getItem">) {
  try {
    const value = JSON.parse(storage.getItem(KEY) || "{}");
    return value && typeof value === "object" && !Array.isArray(value)
      ? value
      : {};
  } catch {
    return {};
  }
}
export function readMeetingView(
  id: string,
  storage: Pick<Storage, "getItem">,
): MeetingView {
  const raw = read(storage)[id];
  if (!raw || typeof raw !== "object") return {};
  return {
    workspace: ["minutes", "analysis", "transcript"].includes(raw.workspace)
      ? raw.workspace
      : "minutes",
    minutesId:
      typeof raw.minutesId === "string" && raw.minutesId.length <= 100
        ? raw.minutesId
        : undefined,
    position:
      typeof raw.position === "number" &&
      Number.isFinite(raw.position) &&
      raw.position >= 0
        ? raw.position
        : 0,
    track: ["system", "microphone"].includes(raw.track) ? raw.track : "",
    rate: [0.75, 1, 1.25, 1.5, 2].includes(raw.rate) ? raw.rate : 1,
  };
}
export function saveMeetingView(
  id: string,
  patch: MeetingView,
  storage: StorageLike,
) {
  try {
    const values = read(storage);
    const previous = readMeetingView(id, storage);
    delete values[id];
    values[id] = { ...previous, ...patch };
    storage.setItem(
      KEY,
      JSON.stringify(Object.fromEntries(Object.entries(values).slice(-100))),
    );
    return true;
  } catch {
    return false;
  }
}
export function resolveMeetingView(meeting: Meeting, saved: MeetingView) {
  const minutes = meeting.artifacts.find(
    (a) => a.id === saved.minutesId && a.kind === "meeting_minutes",
  ) as Artifact<MeetingMinutes> | undefined;
  const selected = minutes || latestMinutes(meeting);
  const targetWorkspace = saved.workspace && ["minutes", "analysis", "transcript"].includes(saved.workspace)
    ? saved.workspace
    : selected
      ? "minutes"
      : meeting.revision
        ? "minutes"
        : "transcript";
  return {
    workspace: targetWorkspace as Workspace,
    minutes: selected,
    position: Math.min(
      saved.position || 0,
      meeting.duration || saved.position || 0,
    ),
    track: (meeting.audio_tracks || []).includes(saved.track || "")
      ? saved.track || ""
      : "",
    rate: saved.rate || 1,
  };
}
export function lastMeeting(storage: Pick<Storage, "getItem">) {
  try {
    return storage.getItem("tingji.last-meeting.v1") || "";
  } catch {
    return "";
  }
}
export function rememberMeeting(id: string, storage: Pick<Storage, "setItem">) {
  try {
    storage.setItem("tingji.last-meeting.v1", id);
  } catch {
    /* Optional browser storage. */
  }
}

export function browserMeetingStorage(): StorageLike {
  try {
    return window.localStorage;
  } catch {
    return { getItem: () => null, setItem: () => {} };
  }
}
