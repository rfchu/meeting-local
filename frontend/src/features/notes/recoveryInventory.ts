import type { RecordingHealth, RecordingSession } from "../../types";
import { mergeRecordingInventory } from "./recordingInventory";

type Result<T> = { ok: true; value: T } | { ok: false };
async function within<T>(
  read: (signal: AbortSignal) => Promise<T>,
  milliseconds: number,
): Promise<Result<T>> {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout>;
  const timeout = new Promise<Result<T>>((resolve) => {
    timer = setTimeout(() => {
      controller.abort();
      resolve({ ok: false });
    }, milliseconds);
  });
  try {
    return await Promise.race([
      Promise.resolve()
        .then(() => read(controller.signal))
        .then(
          (value): Result<T> => ({ ok: true, value }),
          (): Result<T> => ({ ok: false }),
        ),
      timeout,
    ]);
  } finally {
    clearTimeout(timer!);
  }
}

export interface RecoveryInventoryIO {
  local: () => Promise<RecordingSession[]>;
  remote: (signal: AbortSignal) => Promise<RecordingSession[]>;
  health: (id: string, signal: AbortSignal) => Promise<RecordingHealth>;
}
/** Read-only, bounded inventory: unavailable sources never count as empty. */
export async function readRecoveryInventory(
  io: RecoveryInventoryIO,
  limits = { localMs: 2500, serverMs: 10000, verifyMs: 2500 },
) {
  const [local, remote] = await Promise.all([
    within(io.local, limits.localMs),
    within(io.remote, limits.serverMs),
  ]);
  // Cached health is not evidence of current capture, archival or publication state.
  const checked = (local.ok ? local.value : []).map(
    ({ health: _cached, ...s }) => ({
      ...s,
      health: undefined as RecordingHealth | undefined,
    }),
  );
  const server = remote.ok ? remote.value : [];
  const known = new Set(server.map((s) => s.id));
  const candidates = checked.filter((s) => !known.has(s.id));
  if (remote.ok && candidates.length) {
    await within(async (signal) => {
      let cursor = 0;
      const worker = async () => {
        while (!signal.aborted && cursor < candidates.length) {
          const session = candidates[cursor++];
          try {
            const health = await io.health(session.id, signal);
            if (!signal.aborted) session.health = health;
          } catch {
            // Unknown stays unknown. Do not restore cached healthy/empty flags.
          }
        }
      };
      await Promise.all(
        Array.from({ length: Math.min(4, candidates.length) }, worker),
      );
    }, limits.verifyMs);
  }
  // The returned snapshot must not be mutated by a late reader that ignores abort.
  const sessions = mergeRecordingInventory(
    checked.map((s) => ({ ...s })),
    server,
  );
  return {
    sessions,
    serverAvailable: remote.ok,
    localAvailable: local.ok,
    unverifiedCount: candidates.filter((s) => !s.health).length,
  };
}
