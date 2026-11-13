import RecordingStateSummary from "./RecordingStateSummary";
import { recordingActive } from "./recordingStatus";
import { useState } from "react";
import {
  emptyRecording,
  hasSavedAudio,
  noAudioArchivable,
} from "./recordingInventory";
import type { MeetingBrief, RecordingSession } from "../../types";
import { clock } from "../../utils";

function briefSummaryItems(brief?: MeetingBrief): [string, string][] {
  if (!brief) return [];
  const candidates: [string, string | undefined | null][] = [
    ["会议目的", brief.purpose],
    ["议程", brief.agenda],
    ["参会人", brief.participants],
    ["关注问题", brief.questions],
    ["候选人", brief.interview?.candidate],
    ["应聘岗位", brief.interview?.role],
    ["考察重点", brief.interview?.focus],
  ];
  return candidates
    .map(([label, val]): [string, string] => [label, (val || "").trim()])
    .filter(([, val]) => val.length > 0);
}

export function recoveryGroups(sessions: RecordingSession[]) {
  return {
    pending: sessions.filter(
      (s) =>
        !recordingActive(s.health) &&
        !s.health?.recovery_archived &&
        !emptyRecording(s),
    ),
    active: sessions.filter((s) => recordingActive(s.health)),
    empty: sessions.filter(
      (s) => emptyRecording(s) && !s.health?.recovery_archived,
    ),
    archived: sessions.filter(
      (s) => !recordingActive(s.health) && !!s.health?.recovery_archived,
    ),
  };
}
const date = (s: RecordingSession) =>
  new Date(s.created * 1000).toLocaleString("zh-CN", { hour12: false });
export default function RecordingRecoveryList({
  sessions,
  onRecover,
  onDiagnostics,
  onArchive,
  inventoryConfirmed = true,
  recoveryAvailable = true,
  stateUnavailable = false,
  showEmptyState = true,
}: {
  sessions: RecordingSession[];
  inventoryConfirmed?: boolean;
  recoveryAvailable?: boolean;
  stateUnavailable?: boolean;
  showEmptyState?: boolean;
  onRecover: (s: RecordingSession) => void;
  onDiagnostics: (id: string) => void;
  onArchive: (id: string, archived: boolean) => Promise<void>;
}) {
  const groups = recoveryGroups(sessions);
  return (
    <section className="recovery-inventory" aria-label="待恢复录音">
      {showEmptyState && groups.pending.length === 0 && (
        <div className="recovery-clear">
          <span aria-hidden="true">{inventoryConfirmed ? "✓" : "…"}</span>
          <div>
            <strong>
              {inventoryConfirmed ? "没有待恢复的录音" : "恢复检查尚未完成"}
            </strong>
            <p>
              {inventoryConfirmed
                ? "已保存的会议可在会议资料库中查看。"
                : "部分来源或记录状态尚未确认，不能排除仍有待恢复内容。已有录音和暂存仍保留。"}
            </p>
          </div>
        </div>
      )}
      {groups.pending.length > 0 && (
        <>
          <h3>待处理 · {groups.pending.length} 条</h3>
          <p className="recovery-intro">
            恢复只整理已保存内容；未落盘的音频无法补回。
          </p>
        </>
      )}
      {groups.pending.map((session) => {
        const h = session.health;
        const audio = hasSavedAudio(session);
        const canArchiveNoAudio = noAudioArchivable(session);
        const briefItems = briefSummaryItems(session.brief);
        return (
          <article className="recovery-session-card" key={session.id}>
            <header>
              <div>
                <h4>{session.brief?.title || "未完成的录音"}</h4>
                <small>
                  {date(session)} · {clock(session.duration)} ·{" "}
                  {session.source === "macos" ? "原生音轨" : "浏览器录音"}
                </small>
              </div>
              <span className="recovery-state-tag">
                {!h
                  ? "状态待确认"
                  : h.runtime?.control?.status === "unconfirmed"
                    ? "控制待确认"
                    : h.audio.missing_chunk_count
                      ? "分段待补齐"
                      : audio
                        ? "待保存"
                        : "仅保存了信息"}
              </span>
            </header>
            <p>
              {h
                ? `${h.audio.saved_bytes >= 1048576 ? (h.audio.saved_bytes / 1048576).toFixed(1) + " MB" : h.audio.saved_bytes >= 1024 ? (h.audio.saved_bytes / 1024).toFixed(1) + " KB" : h.audio.saved_bytes + " B"} 音频`
                : "保存状态待确认"}{" "}
              · {session.notes.length} 条简记
            </p>
            {!!h?.audio.missing_chunk_count && (
              <p className="health-warning">
                本机有 {h.audio.missing_chunk_count}{" "}
                个缺段，将先尝试同步浏览器暂存。
              </p>
            )}
            {!audio && (
              <p className="health-warning">
                {h
                  ? h.capture_failure_code === "permission_required"
                    ? "当时未获得屏幕与系统音频录制权限，没有已知音频可恢复；已保存的简记和准备信息仍保留。"
                    : "没有已知音频可恢复；已保存的简记和准备信息仍保留。"
                  : "本机音频尚未核对，暂不能判断是否有可恢复音频。已有简记和准备信息仍保留。"}
              </p>
            )}
            <RecordingStateSummary
              id={session.id}
              runtime={h?.runtime}
              unavailable={stateUnavailable || !h?.runtime}
            />
            <div className="recovery-session-actions">
              {canArchiveNoAudio ? (
                <button
                  disabled={stateUnavailable}
                  onClick={() => void onArchive(session.id, true)}
                >
                  收起无音频记录
                </button>
              ) : (
                <button
                  className="primary"
                  disabled={!audio || !recoveryAvailable}
                  onClick={() => onRecover(session)}
                >
                  核对并保存为会议
                </button>
              )}
              <details>
                <summary>保存详情</summary>
                {h?.asr && (
                  <p>
                    已确认识别：Qwen{" "}
                    {Math.round(h.asr.qwen?.covered_seconds || 0)} 秒 / Whisper{" "}
                    {Math.round(h.asr.whisper?.covered_seconds || 0)} 秒
                  </p>
                )}
                {briefItems.length > 0 && (
                  <div>
                    {briefItems.map(([label, val]) => (
                      <p key={label}>
                        {label}：{val}
                      </p>
                    ))}
                  </div>
                )}
                {session.notes.length > 0 && (
                  <div>
                    {session.notes.map((n, i) => (
                      <p key={i}>
                        {clock(n.captured_at)} · {n.raw_text || "时间标记"}
                      </p>
                    ))}
                  </div>
                )}
                <button
                  className="text-btn"
                  onClick={() => onDiagnostics(session.id)}
                >
                  导出脱敏诊断
                </button>
              </details>
            </div>
          </article>
        );
      })}
      {groups.active.length > 0 && (
        <details
          className="recovery-secondary"
          open={groups.active.some(
            (s) => s.health?.runtime?.control?.status === "unconfirmed",
          )}
        >
          <summary>正在采集或暂停 · {groups.active.length} 条</summary>
          <p>
            列表检查不会停止或重新开始采集。若有待确认控制，可使用明确确认的结束入口；正常录音请在原页面结束。
          </p>
          {groups.active.map((s) => (
            <div key={s.id}>
              <p>
                {date(s)} · {clock(s.duration)}
              </p>
              <RecordingStateSummary
                id={s.id}
                runtime={s.health?.runtime}
                unavailable={stateUnavailable}
              />
            </div>
          ))}
        </details>
      )}
      {(groups.empty.length > 0 || groups.archived.length > 0) && (
        <details className="recovery-secondary">
          <summary>
            启动失败与空会话 · {groups.empty.length + groups.archived.length} 条
          </summary>
          <p>这些会话没有可恢复音频，不计入待处理录音。</p>
          {groups.empty.map((s) => (
            <EmptySession
              key={s.id}
              session={s}
              onArchive={onArchive}
              onDiagnostics={onDiagnostics}
            />
          ))}
          {groups.archived.length > 0 && (
            <details className="recovery-archived">
              <summary>已收起记录 · {groups.archived.length} 条</summary>
              {groups.archived.map((s) => (
                <EmptySession
                  key={s.id}
                  session={s}
                  onArchive={onArchive}
                  onDiagnostics={onDiagnostics}
                />
              ))}
            </details>
          )}
        </details>
      )}
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
  const canArchive =
    !!session.health?.empty_session || archived || noAudioArchivable(session);
  return (
    <article className="recovery-empty-session">
      <strong>
        {session.brief?.title ? `${session.brief.title} · ` : ""}
        {date(session)} · {archived ? "已收起" : "未保存音频"}
      </strong>
      <p>
        {session.health?.capture_failure_code === "permission_required"
          ? "当时未获得屏幕与系统音频录制权限，采集未能启动。"
          : session.health?.empty_session === false
            ? `未保存音频（${session.notes.length} 条简记）；原有准备与简记信息仍保留。`
            : "采集未留下音频，也没有已保存的简记或会议准备。"}
      </p>
      <div>
        {canArchive && (
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
          脱敏诊断
        </button>
      </div>
    </article>
  );
}
