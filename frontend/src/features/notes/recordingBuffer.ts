import { api } from "../../api";
import type { RecordingSession } from "../../types";
import { readRecoveryInventory } from "./recoveryInventory";

interface BufferedChunk {
  key: string;
  sessionId: string;
  index: number;
  blob: Blob;
}
const dbPromise = () =>
  new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open("tingji-recordings", 1);
    request.onupgradeneeded = () => {
      request.result.createObjectStore("sessions", { keyPath: "id" });
      request.result.createObjectStore("chunks", { keyPath: "key" });
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () =>
      reject(new Error("浏览器暂存不可用，请检查磁盘空间"));
  });

async function transaction(
  stores: string[],
  mode: IDBTransactionMode,
  work: (tx: IDBTransaction) => void,
) {
  const db = await dbPromise();
  try {
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(stores, mode);
      tx.oncomplete = () => resolve();
      tx.onerror = tx.onabort = () =>
        reject(new Error("录音暂存失败，可能是浏览器空间不足"));
      work(tx);
    });
  } finally {
    db.close();
  }
}

async function all<T>(store: string): Promise<T[]> {
  const db = await dbPromise();
  try {
    return await new Promise<T[]>((resolve, reject) => {
      const request = db.transaction(store).objectStore(store).getAll();
      request.onsuccess = () => resolve(request.result as T[]);
      request.onerror = () => reject(request.error);
    });
  } finally {
    db.close();
  }
}

export async function bufferRecording(
  session: RecordingSession,
  index?: number,
  blob?: Blob,
) {
  await transaction(["sessions", "chunks"], "readwrite", (tx) => {
    tx.objectStore("sessions").put(session);
    if (blob && index !== undefined)
      tx.objectStore("chunks").put({
        key: `${session.id}:${index}`,
        sessionId: session.id,
        index,
        blob,
      });
  });
}
export async function forgetRecording(id: string) {
  await transaction(["sessions"], "readwrite", (tx) => {
    tx.objectStore("sessions").delete(id);
  });
}
const syncing = new Map<string, Promise<void>>();
export async function flushRecording(id: string) {
  await syncing.get(id)?.catch(() => {});
  await syncRecording(id);
}
export function syncRecording(id: string): Promise<void> {
  const current = syncing.get(id);
  if (current) return current;
  const task = (async () => {
    const chunks = (await all<BufferedChunk>("chunks"))
      .filter((chunk) => chunk.sessionId === id)
      .sort((a, b) => a.index - b.index);
    for (const chunk of chunks) {
      await api.recordingChunk(id, chunk.index, chunk.blob);
      await transaction(["chunks"], "readwrite", (tx) => {
        tx.objectStore("chunks").delete(chunk.key);
      });
    }
    const session = (await all<RecordingSession>("sessions")).find(
      (s) => s.id === id,
    );
    if (session) await api.recordingMetadata(session);
  })().finally(() => syncing.delete(id));
  syncing.set(id, task);
  return task;
}
let localInventoryRead: Promise<RecordingSession[]> | undefined;
function readLocalInventory() {
  if (!localInventoryRead) {
    const task = all<RecordingSession>("sessions");
    localInventoryRead = task;
    const clear = () => {
      if (localInventoryRead === task) localInventoryRead = undefined;
    };
    void task.then(clear, clear);
  }
  return localInventoryRead;
}
export function recoveryInventorySnapshot() {
  return readRecoveryInventory({
    local: readLocalInventory,
    remote: api.recordings,
    health: api.recordingHealth,
  });
}

export async function recoverableRecordings() {
  return (await recoveryInventorySnapshot()).sessions;
}

export async function pendingRecordingChunks(id: string): Promise<number> {
  const db = await dbPromise();
  try {
    return await new Promise<number>((resolve, reject) => {
      const request = db
        .transaction("chunks")
        .objectStore("chunks")
        .getAllKeys();
      request.onsuccess = () =>
        resolve(
          request.result.filter((key) => String(key).startsWith(id + ":"))
            .length,
        );
      request.onerror = () => reject(request.error);
    });
  } finally {
    db.close();
  }
}
