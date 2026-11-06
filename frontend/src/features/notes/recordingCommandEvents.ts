/** Same-origin refresh hints only. Never carries audio, content or executable commands. */
export type CommandFeedback = {
  kind: "conflict" | "unconfirmed" | "unavailable";
};
const feedback = new Map<string, CommandFeedback>();
const listeners = new Set<(id: string) => void>();
let channel: BroadcastChannel | null = null;
let attempted = false;
function broadcastChannel() {
  if (attempted || typeof window === "undefined") return channel;
  attempted = true;
  try {
    if (typeof BroadcastChannel !== "undefined") {
      channel = new BroadcastChannel("tingji-recording-state");
      channel.onmessage = (event) => {
        const value = event.data;
        if (
          value?.type === "changed" &&
          typeof value.recording_id === "string" &&
          /^[0-9a-f]{32}$/.test(value.recording_id)
        )
          listeners.forEach((listener) => listener(value.recording_id));
      };
    }
  } catch {
    /* Page return/focus recovery remains available. */
  }
  return channel;
}
export function watchRecordingCommands(listener: (id: string) => void) {
  broadcastChannel();
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
export function recordingCommandFeedback(id: string) {
  return feedback.get(id) || null;
}
export function dismissRecordingCommandFeedback(id: string) {
  feedback.delete(id);
  listeners.forEach((listener) => listener(id));
}
export function reportRecordingCommand(
  id: string,
  kind?: CommandFeedback["kind"],
) {
  if (kind) {
    feedback.delete(id);
    feedback.set(id, { kind });
    if (feedback.size > 64) feedback.delete(feedback.keys().next().value!);
  } else feedback.delete(id);
  listeners.forEach((listener) => listener(id));
  try {
    broadcastChannel()?.postMessage({ type: "changed", recording_id: id });
  } catch {
    /* Refresh locally. */
  }
}
