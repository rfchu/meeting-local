/** Tracks can end before the UI requests stop; never wait for an event already emitted. */
export async function stopRecorder(
  recorder: Pick<
    MediaRecorder,
    "state" | "addEventListener" | "removeEventListener" | "stop"
  >,
) {
  if (recorder.state === "inactive") return;
  await new Promise<void>((resolve, reject) => {
    const stopped = () => resolve();
    recorder.addEventListener("stop", stopped, { once: true });
    try {
      recorder.stop();
    } catch (error) {
      recorder.removeEventListener("stop", stopped);
      reject(error);
    }
  });
}
