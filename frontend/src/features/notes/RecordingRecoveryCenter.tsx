import { watchRecordingCommands } from "./recordingCommandEvents";
import { healthFreshness } from "./healthFreshness";
import { useCallback, useEffect, useRef, useState } from "react";
import Modal from "../../components/Modal";
import { api } from "../../api";
import type { RecordingSession } from "../../types";
import { recoveryInventorySnapshot } from "./recordingBuffer";
import { recoverSavedRecording } from "./recoverSavedRecording";
import RecordingReceipt from "./RecordingReceipt";
import { watchRecordingRecovery } from "./recordingRecovery";
import RecordingRecoveryList from "./RecordingRecoveryList";
import type { CompletionMode } from "./recordingPreferences";
export default function RecordingRecoveryCenter({
  onClose,
  onComplete,
  toast,
  defaultEngine = "whisper",
}: {
  defaultEngine?: "whisper" | "qwen";
  onClose: () => void;
  onComplete: (
    id: string,
    options: { mode: CompletionMode; engine: "whisper" | "qwen" },
  ) => Promise<void>;
  toast: (s: string) => void;
}) {
  const [sessions, setSessions] = useState<RecordingSession[]>([]),
    [loading, setLoading] = useState(true),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [inventoryError, setInventoryError] = useState("");
  const [checkedAt, setCheckedAt] = useState(0);
  const [now, setNow] = useState(Date.now());
  const [review, setReview] = useState<RecordingSession | null>(null),
    [confirmed, setConfirmed] = useState(false),
    [serverAvailable, setServerAvailable] = useState(false);
  const [localAvailable, setLocalAvailable] = useState(false);
  const [unverified, setUnverified] = useState(0);
  const applyInventory = useCallback(
    (result: Awaited<ReturnType<typeof recoveryInventorySnapshot>>) => {
      setSessions(result.sessions);
      setCheckedAt(Date.now());
      setNow(Date.now());
      setServerAvailable(result.serverAvailable);
      setLocalAvailable(result.localAvailable);
      setUnverified(result.unverifiedCount);
      setInventoryError(
        !result.serverAvailable
          ? "暂时无法检查本机保存状态，请重新检查。已有内容仍保留。"
          : !result.localAvailable
            ? "浏览器暂存暂不可读，以下仍展示本机保存的记录。"
            : result.unverifiedCount
              ? `${result.unverifiedCount} 条浏览器记录的本机状态尚未确认，恢复前会重新核对。`
              : "",
      );
    },
    [],
  );
  const [saved, setSaved] = useState<{
    id: string;
    notes: number;
    engine: "whisper" | "qwen";
  } | null>(null);
  const requestVersion = useRef(0);
  const refresh = useCallback(
    async (showLoading = true) => {
      const version = ++requestVersion.current;
      if (showLoading) setLoading(true);
      try {
        const result = await recoveryInventorySnapshot();
        if (version === requestVersion.current) applyInventory(result);
      } catch (e) {
        if (version === requestVersion.current) {
          setServerAvailable(false);
          setInventoryError((e as Error).message);
        }
      } finally {
        if (version === requestVersion.current) setLoading(false);
      }
    },
    [applyInventory],
  );
  useEffect(() => {
    void refresh(false);
    const timer = window.setInterval(() => void refresh(false), 15000);
    const unwatch = watchRecordingRecovery(() => void refresh());
    const unwatchCommands = watchRecordingCommands(() => void refresh());
    const clock = window.setInterval(() => setNow(Date.now()), 1000);
    return () => {
      ++requestVersion.current;
      window.clearInterval(timer);
      unwatch();
      unwatchCommands();
      window.clearInterval(clock);
    };
  }, [refresh]);
  const stateUnavailable =
    healthFreshness(
      checkedAt,
      Math.max(now, Date.now()),
      !serverAvailable,
      loading,
    ) !== "current";
  const recover = async (session: RecordingSession) => {
    if (stateUnavailable) return;
    setBusy(true);
    setError("");
    try {
      const snapshot = await recoveryInventorySnapshot();
      applyInventory(snapshot);
      if (!snapshot.serverAvailable)
        throw new Error(
          "本机状态尚未确认，未执行恢复；已有内容保留，请重新检查。",
        );
      const latest = snapshot.sessions.find((s) => s.id === session.id);
      if (!latest)
        throw new Error(
          "此会话状态已变化，请重新检查录音列表或会议资料库；未执行恢复。",
        );
      const result = await recoverSavedRecording(latest);
      setReview(null);
      setConfirmed(false);
      setSaved({
        id: result.meeting.id,
        notes: result.meeting.notes.length,
        engine: latest.draft_engine || defaultEngine,
      });
      if (result.cleanupPending)
        toast("会议已保存；浏览器暂存尚未清理，下次检查会按已保存会议去重。");
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const open = async (mode: CompletionMode) => {
    if (!saved) return;
    setBusy(true);
    setError("");
    try {
      await onComplete(saved.id, { mode, engine: saved.engine });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      id="recordingRecoveryCenter"
      className="recording-recovery-center"
      ariaLabel="录音恢复中心"
      onClose={() => {
        if (!busy) onClose();
      }}
    >
      <header className="recovery-center-heading">
        <div>
          <h2>录音恢复</h2>
          <p>查看中断会话，将已保存的录音和简记整理为会议。</p>
        </div>
        <button disabled={busy} onClick={onClose}>
          关闭
        </button>
      </header>
      {error && (
        <p className="recovery-center-error" role="alert">
          {error}
        </p>
      )}
      {inventoryError && (
        <p className="recovery-center-error" role="status">
          {inventoryError}
        </p>
      )}
      {review && (
        <section className="recovery-confirm">
          <h3>核对后保存为会议</h3>
          <p>
            {review.brief?.title ||
              new Date(review.created * 1000).toLocaleString("zh-CN")}{" "}
            · {review.notes.length} 条已存简记
          </p>
          <label>
            <input
              type="checkbox"
              disabled={busy}
              checked={confirmed}
              onChange={(e) => setConfirmed(e.target.checked)}
            />
            我确认原录音已经停止，整理已保存的内容；未落盘的尾段无法补回。
          </label>
          <div>
            <button
              className="primary"
              disabled={!confirmed || busy || stateUnavailable}
              onClick={() => void recover(review)}
            >
              {busy ? "正在保存…" : "确认保存"}
            </button>
            <button disabled={busy} onClick={() => setReview(null)}>
              取消
            </button>
          </div>
        </section>
      )}
      {saved && (
        <section className="recovery-saved" role="status">
          <strong>录音与 {saved.notes} 条简记已保存</strong>
          <RecordingReceipt id={saved.id} engine={saved.engine} />
          <p>可以先打开会议核对，也可以复用实时识别结果补齐文字稿。</p>
          <button
            className="primary"
            disabled={busy}
            onClick={() => void open("none")}
          >
            打开已保存的会议
          </button>
          <button disabled={busy} onClick={() => void open("quick")}>
            快速完成文字稿
          </button>
          <details>
            <summary>其他处理方式</summary>
            <button disabled={busy} onClick={() => void open("full")}>
              精细转写
            </button>
          </details>
        </section>
      )}
      <div className="recovery-center-tools">
        <span>
          {busy
            ? "正在保存，请稍候…"
            : loading
              ? "正在检查本机与浏览器暂存…"
              : `本机录音${serverAvailable ? "已检查" : "未确认"} · 浏览器暂存${localAvailable ? "已检查" : "未确认"}`}
        </span>
        <button disabled={busy || loading} onClick={() => void refresh()}>
          重新检查
        </button>
      </div>
      {!loading && (
        <fieldset disabled={busy} className="recovery-center-body">
          <RecordingRecoveryList
            sessions={sessions}
            inventoryConfirmed={
              serverAvailable && localAvailable && unverified === 0
            }
            recoveryAvailable={!stateUnavailable}
            stateUnavailable={stateUnavailable}
            showEmptyState={!saved}
            onRecover={(s) => {
              setReview(s);
              setConfirmed(false);
            }}
            onArchive={async (id, archived) => {
              setBusy(true);
              try {
                await api.archiveEmptyRecording(id, archived);
                await refresh();
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
            onDiagnostics={(id) => {
              void api
                .recordingDiagnostics(id)
                .then((report) => {
                  const url = URL.createObjectURL(
                    new Blob([JSON.stringify(report, null, 2)], {
                      type: "application/json",
                    }),
                  );
                  const a = document.createElement("a");
                  a.href = url;
                  a.download = "tingji-recording-diagnostics.json";
                  a.click();
                  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
                })
                .catch((e) => setError(e.message));
            }}
          />
        </fieldset>
      )}
    </Modal>
  );
}
