import { useState } from "react";
import { api } from "../../api";
import type { Meeting, MeetingNote, MeetingNoteType } from "../../types";
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
  const add = async () => {
    if (!text.trim()) return;
    setBusy(true);
    try {
      await api.createNote(meeting.id, {
        captured_at: 0,
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
  onSeek,
  onUpdate,
  onDelete,
}: {
  note: MeetingNote;
  onSeek: (time: number) => void;
  onUpdate: (note: MeetingNote) => Promise<void>;
  onDelete: () => Promise<void>;
}) {
  const [draft, setDraft] = useState(note),
    [saving, setSaving] = useState(false);
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
      <div className="note-expansion-placeholder">
        AI 扩写将在下一阶段接入；原始简记始终保留。
      </div>
    </article>
  );
}
