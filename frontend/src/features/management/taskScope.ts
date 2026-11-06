import type { TaskFocus } from "../../types";

export function taskQuery(state: string, focus?: TaskFocus) {
  const query = new URLSearchParams({ state });
  if (focus) {
    query.set("meeting_id", focus.meetingId);
    query.set("job_id", focus.jobId);
  }
  return `/runtime/tasks?${query}`;
}
