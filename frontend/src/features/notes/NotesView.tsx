import { useEffect, useState } from "react";
import { api } from "../../api";
import type {
  Meeting,
  MeetingNote,
  MeetingNoteType,
  LLMConfig,
} from "../../types";
import { clock } from "../../utils";

const labels: Record<MeetingNoteType, string> = {
  note: "简记",
  highlight: "重点",
  question: "问题",
  decision: "决策",
  action: "待办",
};

export default function NotesView({
  meeting,
  onRefresh,
  onSeek,
  toast,
}: {
  meeting: Meeting;
  onRefresh: () => Promise<void>;
  onSeek: (time: number) => void;
  toast: (text: string) => void;
}) {
  const [text, setText] = useState(""),
    [type, setType] = useState<MeetingNoteType>("note"),
    [busy, setBusy] = useState(false);
  const [capturedAt, setCapturedAt] = useState(0);
  const [profiles, setProfiles] = useState<LLMConfig[]>([]);
  const [profileId, setProfileId] = useState("");
  const [modelsReady, setModelsReady] = useState(false);
  useEffect(() => {
    api
      .environment()
      .then((env) => {
        setProfiles(env.analysis.profiles);
        setProfileId(env.analysis.default_profile_id || "");
        setModelsReady(true);
      })
      .catch((error) => toast((error as Error).message));
  }, []);
  const active = meeting.jobs.find((job) =>
    ["queued", "running"].includes(job.state),
  );
  const action = async (operation: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await operation();
      await onRefresh();
    } catch (error) {
      toast((error as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const add = async () => {
    if (!text.trim()) return;
    setBusy(true);
    try {
      await api.createNote(meeting.id, {
        captured_at: capturedAt,
        note_type: type,
        raw_text: text.trim(),
      });
      setText("");
      await onRefresh();
    } catch (error) {
      toast((error as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const update = async (note: MeetingNote) => {
    try {
      await api.updateNote(meeting.id, note.id, {
        note_type: note.note_type,
        raw_text: note.raw_text,
      });
      await onRefresh();
    } catch (error) {
      toast((error as Error).message);
    }
  };
  return (
    <div className="notes-view">
      <div className="notes-expansion-toolbar">
        <strong>AI 简记扩写</strong>
        <label>
          整理模型{" "}
          <select
            aria-label="简记扩写模型"
            value={profileId}
            onChange={(event) => setProfileId(event.target.value)}
          >
            {!profiles.length && <option value="">LM Studio 默认模型</option>}
            {profiles.map((profile) => (
              <option key={profile.id} value={profile.id}>
                {profile.name} ·{" "}
                {profile.provider_type === "cloud" ? "云端" : "本地"}
              </option>
            ))}
          </select>
        </label>
        <p>
          根据简记前 2 分钟、后 1 分钟的文字稿整理。AI
          建议需核对依据后接受，原始简记始终保留。
          {profiles.find((p) => p.id === profileId)?.provider_type ===
            "cloud" && " 使用云端模型时，会发送该条简记和附近文字稿。"}
        </p>
        {active && (
          <div role="status">
            {active.stage} · {Math.round(active.progress * 100)}%
            {active.kind === "note_expansion" && (
              <button
                disabled={busy}
                onClick={() =>
                  void action(() => api.jobAction(active.id, "cancel"))
                }
              >
                取消扩写
              </button>
            )}
          </div>
        )}
      </div>
      <label className="note-position">
        简记位置（秒）{" "}
        <input
          type="number"
          min={0}
          max={meeting.duration || undefined}
          value={capturedAt}
          onChange={(event) =>
            setCapturedAt(Math.max(0, Number(event.target.value) || 0))
          }
        />
      </label>
      <div className="notes-compose">
        <select
          value={type}
          onChange={(event) => setType(event.target.value as MeetingNoteType)}
        >
          {Object.entries(labels).map(([value, label]) => (
            <option value={value} key={value}>
              {label}
            </option>
          ))}
        </select>
        <textarea
          rows={2}
          placeholder="补充会议简记…"
          value={text}
          onChange={(event) => setText(event.target.value)}
        />
        <button
          className="primary"
          disabled={busy || !text.trim()}
          onClick={() => void add()}
        >
          添加
        </button>
      </div>
      {!meeting.notes.length ? (
        <div className="empty">
          <div>✎</div>
          <h3>还没有会议简记</h3>
          <p>实时录音时记录的重点、问题、决策和待办会显示在这里。</p>
        </div>
      ) : (
        <div className="notes-list">
          {meeting.notes.map((note) => (
            <NoteCard
              key={note.id}
              note={note}
              meeting={meeting}
              disabled={busy || !!active || !modelsReady}
              onExpand={() =>
                action(() => api.expandNote(meeting.id, note.id, profileId))
              }
              onAccept={() =>
                action(() =>
                  api.acceptNoteExpansion(
                    meeting.id,
                    note.id,
                    note.expansion!.id,
                  ),
                )
              }
              onSeek={onSeek}
              onUpdate={update}
              onDelete={async () => {
                if (!confirm("删除这条会议简记？")) return;
                await api.deleteNote(meeting.id, note.id);
                await onRefresh();
              }}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function NoteCard({
  note,
  meeting,
  disabled,
  onExpand,
  onAccept,
  onSeek,
  onUpdate,
  onDelete,
}: {
  note: MeetingNote;
  meeting: Meeting;
  disabled: boolean;
  onExpand: () => Promise<void>;
  onAccept: () => Promise<void>;
  onSeek: (time: number) => void;
  onUpdate: (note: MeetingNote) => Promise<void>;
  onDelete: () => Promise<void>;
}) {
  const [draft, setDraft] = useState(note),
    [saving, setSaving] = useState(false);
  const dirty =
    draft.raw_text !== note.raw_text || draft.note_type !== note.note_type;
  const suggestion = note.expansion;
  const job = meeting.jobs.find(
    (job) =>
      job.kind === "note_expansion" &&
      JSON.parse(job.config).note_id === note.id,
  );
  useEffect(() => {
    setDraft(note);
  }, [note.raw_text, note.note_type]);
  const save = async () => {
    if (
      !draft.raw_text.trim() ||
      (draft.raw_text === note.raw_text && draft.note_type === note.note_type)
    )
      return;
    setSaving(true);
    try {
      await onUpdate({ ...draft, raw_text: draft.raw_text.trim() });
    } finally {
      setSaving(false);
    }
  };
  return (
    <article className={`note-card note-${draft.note_type}`}>
      <div className="note-meta">
        <button className="note-time" onClick={() => onSeek(note.captured_at)}>
          {clock(note.captured_at)}
        </button>
        <select
          value={draft.note_type}
          onChange={(event) =>
            setDraft((old) => ({
              ...old,
              note_type: event.target.value as MeetingNoteType,
            }))
          }
        >
          {Object.entries(labels).map(([value, label]) => (
            <option value={value} key={value}>
              {label}
            </option>
          ))}
        </select>
        <span>{saving ? "保存中…" : "原始简记"}</span>
        <button className="text-btn danger" onClick={() => void onDelete()}>
          删除
        </button>
      </div>
      <textarea
        rows={2}
        value={draft.raw_text}
        onChange={(event) =>
          setDraft((old) => ({ ...old, raw_text: event.target.value }))
        }
        onBlur={() => void save()}
      />
      <div className="note-expansion">
        <button
          disabled={disabled || saving || dirty || !meeting.revision}
          onClick={() => void onExpand()}
        >
          {suggestion ? "重新扩写" : "AI 扩写"}
        </button>
        {!meeting.revision && <span> 完成文字稿后可扩写</span>}
        {dirty && <span> 请先保存简记</span>}
        {job?.state === "failed" && (
          <p className="error" role="alert">
            扩写失败：{job.error}
          </p>
        )}
        {job?.state === "cancelled" && <p>扩写已取消</p>}
        {suggestion && (
          <section>
            <strong>
              {suggestion.payload.status === "supported"
                ? "AI 扩写建议"
                : "依据不足"}
            </strong>
            <small>
              {" "}
              · {String(suggestion.provenance.provider || "")} /{" "}
              {String(suggestion.provenance.model || "")}
            </small>
            {suggestion.stale && (
              <p className="error">
                文字稿或简记已变化，该建议已过期，请重新扩写。
              </p>
            )}
            {suggestion.payload.text && <p>{suggestion.payload.text}</p>}
            {suggestion.payload.reason && <p>{suggestion.payload.reason}</p>}
            {suggestion.payload.evidence.map((evidence, index) => {
              const segment = meeting.segments.find(
                (s) => s.id === evidence.segment_id,
              );
              return (
                <blockquote key={index}>
                  <button
                    disabled={suggestion.stale || !segment}
                    onClick={() => segment && onSeek(segment.start)}
                  >
                    {segment ? clock(segment.start) : "原文"} 回听依据
                  </button>
                  <p>“{evidence.quote}”</p>
                </blockquote>
              );
            })}
            {suggestion.payload.status === "supported" && (
              <button
                className="primary"
                disabled={disabled || saving || dirty || suggestion.stale}
                onClick={() => void onAccept()}
              >
                接受并保存扩写
              </button>
            )}
            <p className="muted">
              引用已核验为原文摘录；是否支持扩写结论仍需人工核对。
            </p>
          </section>
        )}
        {note.accepted_text && (
          <section>
            <strong>
              已接受的扩写{" "}
              {note.expansion_stale ? "（文字稿已变更，待复核）" : ""}
            </strong>
            <p>{note.accepted_text}</p>
          </section>
        )}
      </div>
    </article>
  );
}
