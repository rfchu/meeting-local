import { currentMinutes } from "../../utils";
import { useState } from "react";
import { api } from "../../api";
import type { Meeting } from "../../types";
import { TodoCard } from "../notes/NotesView";
export default function MeetingOutcomes({
  meeting,
  onRefresh,
  onNotes,
  onSeek,
  toast,
  minutesDirty = false,
}: {
  meeting: Meeting;
  minutesDirty?: boolean;
  onRefresh: () => Promise<void>;
  onNotes: () => void;
  onSeek: (time: number) => void;
  toast: (text: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const counts = meeting.flow?.counts;
  const minutes = currentMinutes(meeting);
  return (
    <section className="meeting-outcomes">
      <h3>会议成果与后续跟进</h3>
      <p>
        纪要整理、人工确认和跟进共用本次会议的原文依据。个人跟进不自动进入对外纪要。
      </p>
      <div className="attention-summary">
        <span>
          关注点：已确认可用 {counts?.meeting || 0} · 个人保留{" "}
          {counts?.personal || 0} · 待处理{" "}
          {(counts?.pending || 0) +
            (counts?.deferred || 0) +
            (counts?.stale || 0)}
        </span>
        <button onClick={onNotes}>回到会后整理</button>
      </div>
      {meeting.flow?.brief.questions && (
        <details>
          <summary>会前希望确认的问题</summary>
          <p style={{ whiteSpace: "pre-wrap" }}>
            {meeting.flow.brief.questions}
          </p>
          <small>
            请结合纪要和原文核对是否得到回答；不会自动标记为已解决。
          </small>
        </details>
      )}
      {meeting.notes.some(
        (n) =>
          n.note_type === "question" &&
          ["pending", "stale", "deferred"].includes(
            meeting.flow?.attention.find((a) => a.note_id === n.id)?.state ||
              "pending",
          ),
      ) && (
        <details>
          <summary>尚待处理的问题线索</summary>
          {meeting.notes
            .filter(
              (n) =>
                n.note_type === "question" &&
                ["pending", "stale", "deferred"].includes(
                  meeting.flow?.attention.find((a) => a.note_id === n.id)
                    ?.state || "pending",
                ),
            )
            .map((n) => (
              <p key={n.id}>{n.raw_text || "标记了问题时间，待补充"}</p>
            ))}
          <button onClick={onNotes}>核对问题与原声</button>
        </details>
      )}
      {minutes && (
        <details>
          <summary>
            从纪要行动项核对跟进 · {minutes.payload.action_items.length} 项
          </summary>
          <p>
            这是纪要中的候选内容，核对原文后再加入跟进。已有同内容事项会提示去重。
          </p>
          <fieldset disabled={minutesDirty}>
            {minutesDirty && <p>请先保存纪要，再核对行动项。</p>}
            {minutes.payload.action_items.map(
              (item: import("../../types").MinutesItem, index: number) => (
                <ActionCandidate
                  key={`${minutes.id}-${index}`}
                  meetingId={meeting.id}
                  artifactId={minutes.id}
                  index={index}
                  item={item}
                  sources={meeting.segments.filter((s) =>
                    item.source_segment_ids.includes(s.id),
                  )}
                  onSeek={onSeek}
                  alreadyTracked={meeting.note_todos.some(
                    (t) =>
                      (t.source_artifact_id === minutes.id &&
                        t.source_index === index) ||
                      t.content.trim() === item.content.trim(),
                  )}
                  onRefresh={onRefresh}
                  toast={toast}
                />
              ),
            )}
          </fieldset>
        </details>
      )}
      <details
        open={(meeting.note_todos || []).some((t) => t.status === "pending")}
      >
        <summary>
          跟进事项 ·{" "}
          {
            (meeting.note_todos || []).filter((t) => t.status === "pending")
              .length
          }{" "}
          项待完成
        </summary>
        {!meeting.note_todos?.length && (
          <p>
            暂无已确认的跟进事项。可在关注点核对后“转为待办”；AI
            纪要中的行动项仍需人工核对。
          </p>
        )}
        <fieldset disabled={busy}>
          {(meeting.note_todos || []).map((todo) => (
            <TodoCard
              key={todo.id}
              todo={todo}
              onSave={async (value) => {
                setBusy(true);
                try {
                  await api.updateNoteTodo(meeting.id, todo.id, value);
                  await onRefresh();
                } catch (e) {
                  toast((e as Error).message);
                } finally {
                  setBusy(false);
                }
              }}
            />
          ))}
        </fieldset>
      </details>
    </section>
  );
}

function ActionCandidate({
  meetingId,
  artifactId,
  index,
  item,
  sources,
  onSeek,
  alreadyTracked,
  onRefresh,
  toast,
}: {
  meetingId: string;
  artifactId: string;
  index: number;
  item: import("../../types").MinutesItem;
  sources: import("../../types").Segment[];
  onSeek: (time: number) => void;
  alreadyTracked: boolean;
  onRefresh: () => Promise<void>;
  toast: (text: string) => void;
}) {
  const [content, setContent] = useState(item.content),
    [owner, setOwner] = useState(item.owner || ""),
    [due, setDue] = useState(item.due_date || ""),
    [confirmed, setConfirmed] = useState(false),
    [busy, setBusy] = useState(false);
  return (
    <article className="action-candidate">
      <h4>行动项 {index + 1} · 核对后加入跟进</h4>
      <details>
        <summary>核对原文依据 · {sources.length} 条</summary>
        {sources.map((source) => (
          <article key={source.id}>
            <button onClick={() => onSeek(source.start)}>
              回听 {source.start.toFixed(0)} 秒
            </button>
            <p>
              {source.speaker}：{source.text}
            </p>
          </article>
        ))}
      </details>
      <label>
        事项
        <textarea
          aria-label={`跟进事项 ${index + 1}`}
          value={content}
          maxLength={2000}
          onChange={(e) => {
            setContent(e.target.value);
            setConfirmed(false);
          }}
        />
      </label>
      <label>
        负责人
        <input
          value={owner}
          maxLength={200}
          placeholder="未明确可留空"
          onChange={(e) => {
            setOwner(e.target.value);
            setConfirmed(false);
          }}
        />
      </label>
      <label>
        期限
        <input
          value={due}
          maxLength={200}
          placeholder="未明确可留空"
          onChange={(e) => {
            setDue(e.target.value);
            setConfirmed(false);
          }}
        />
      </label>
      <label>
        <input
          type="checkbox"
          checked={confirmed}
          onChange={(e) => setConfirmed(e.target.checked)}
        />
        我已核对行动项及原文，确认负责人和期限的填写。
      </label>
      <button
        disabled={busy || alreadyTracked || !confirmed || !content.trim()}
        onClick={() => {
          setBusy(true);
          void api
            .trackMinutesAction(meetingId, artifactId, index, {
              content,
              owner,
              due_date: due,
              confirmed,
            })
            .then(async (result) => {
              await onRefresh();
              toast(
                result.reused
                  ? "已有同内容的跟进项，未重复添加。"
                  : "已加入本次会议跟进，不会自动通知任何人。",
              );
            })
            .catch((e) => toast(e.message))
            .finally(() => setBusy(false));
        }}
      >
        {alreadyTracked ? "已加入跟进" : "加入跟进"}
      </button>
    </article>
  );
}
