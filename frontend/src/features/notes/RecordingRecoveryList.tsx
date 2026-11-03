import { useState } from "react";
import { emptyRecording, recordingRecoveryCount } from "./recordingInventory";
import type { RecordingSession } from "../../types";
import { clock } from "../../utils";

export default function RecordingRecoveryList({
  sessions,
  onRecover,
  onDiagnostics,
  onArchive,
}: {
  sessions: RecordingSession[];
  onRecover: (session: RecordingSession) => void;
  onDiagnostics: (id: string) => void;
  onArchive: (id: string, archived: boolean) => Promise<void>;
}) {
  if (!sessions.length) return null;
  const empty = sessions.filter(emptyRecording);
  const visible = sessions.filter((s) => !emptyRecording(s));
  return (
    <section className="recording-recovery" aria-label="未完成录音恢复中心">
      <h3>
        {recordingRecoveryCount(visible)
          ? `未完成录音恢复中心 · ${recordingRecoveryCount(visible)} 条`
          : "没有待恢复的录音"}
      </h3>
      <p>
        只整理已保存的内容，中断期间和未落盘的尾段无法补回；不会重新开始采集。
      </p>
      {visible.map((session) => {
        const health = session.health,
          running = health?.native_running;
        const hasAudio =
          session.chunk_count > 0 ||
          session.native_audio_saved ||
          (health?.audio.saved_bytes || 0) > 0;
        return (
          <article key={session.id}>
            <strong>
              {new Date(session.created * 1000).toLocaleString()} ·{" "}
              {clock(session.duration)} ·{" "}
              {session.source === "macos" ? "原生音轨" : "浏览器录音"}
            </strong>
            <p>
              本机{" "}
              {health
                ? `${(health.audio.saved_bytes / 1048576).toFixed(1)} MB`
                : "状态待核对"}{" "}
              · {session.notes.length} 条简记 ·
              {health
                ? session.source === "macos"
                  ? " 按已存音轨核对"
                  : ` 本机缺段 ${health.audio.missing_chunk_count} 个`
                : " 保存状态未确认"}
            </p>
            {running ? (
              <p className="health-warning">
                此会话仍在采集，请先回原录音页面结束录音。
              </p>
            ) : health?.audio.missing_chunk_count ? (
              <p className="health-warning">
                将先尝试同步浏览器暂存。缺段未补齐时会保留内容并阻止完成。
              </p>
            ) : !hasAudio ? (
              <p className="health-warning">
                尚无已知音频可恢复，已有简记仍保留。
              </p>
            ) : null}
            {health?.asr && (
              <p>
                已确认识别：Qwen{" "}
                {Math.round(health.asr.qwen?.covered_seconds || 0)}s / Whisper{" "}
                {Math.round(health.asr.whisper?.covered_seconds || 0)}
                s（两者不可直接相加）
              </p>
            )}
            {session.notes.length > 0 && (
              <details>
                <summary>查看已存简记</summary>
                {session.notes.map((note, i) => (
                  <p key={i}>
                    {clock(note.captured_at)} · {note.raw_text || "时间标记"}
                  </p>
                ))}
              </details>
            )}
            <div>
              <button
                disabled={running || !hasAudio}
                onClick={() => onRecover(session)}
              >
                恢复已存内容并完成保存
              </button>
              <button
                className="text-btn"
                onClick={() => onDiagnostics(session.id)}
              >
                导出脱敏诊断
              </button>
            </div>
          </article>
        );
      })}
      {empty.length > 0 && (
        <details>
          <summary>
            未保存音频的会话 ·{" "}
            {empty.filter((s) => !s.health?.recovery_archived).length} 条
            {empty.some((s) => s.health?.recovery_archived)
              ? "（含已收起记录）"
              : ""}
          </summary>
          {empty
            .filter((s) => !s.health?.recovery_archived)
            .map((session) => (
              <EmptySession
                key={session.id}
                session={session}
                onArchive={onArchive}
                onDiagnostics={onDiagnostics}
              />
            ))}
          {empty.some((s) => s.health?.recovery_archived) && (
            <details>
              <summary>已收起记录</summary>
              {empty
                .filter((s) => s.health?.recovery_archived)
                .map((session) => (
                  <EmptySession
                    key={session.id}
                    session={session}
                    onArchive={onArchive}
                    onDiagnostics={onDiagnostics}
                  />
                ))}
            </details>
          )}
        </details>
      )}
      <small>
        诊断仅含状态、计数和耗时，不包含音频、文字稿、简记正文或密钥。
      </small>
    </section>
  );
}

function EmptySession({
  session,
  onArchive,
  onDiagnostics,
}: {
  session: RecordingSession;
  onArchive: (id: string, archived: boolean) => Promise<void>;
  onDiagnostics: (id: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const archived = !!session.health?.recovery_archived;
  return (
    <article>
      <strong>
        {new Date(session.created * 1000).toLocaleString("zh-CN", {
          hour12: false,
        })}{" "}
        · {archived ? "已收起的空会话" : "未保存音频"}
      </strong>
      <p>
        {session.health?.capture_failure_code === "permission_required"
          ? "采集启动失败：当时未获得屏幕与系统音频录制权限。"
          : "采集未留下音频，也没有已保存的简记或会议准备。"}
        此条没有可恢复的录音。
      </p>
      {session.health?.empty_session && (
        <button
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              await onArchive(session.id, !archived);
            } finally {
              setBusy(false);
            }
          }}
        >
          {archived ? "恢复显示" : "收起空记录"}
        </button>
      )}
      <button className="text-btn" onClick={() => onDiagnostics(session.id)}>
        导出脱敏诊断
      </button>
    </article>
  );
}
