import { useEffect, useState } from "react";
import { api } from "../../api";
import type {
  Meeting,
  MeetingNote,
  MeetingNoteType,
  LLMConfig,
  NoteReview,
  NoteTodo,
} from "../../types";
import { clock } from "../../utils";
import Modal from "../../components/Modal";

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
  onPause,
  toast,
  minutesDirty,
}: {
  meeting: Meeting;
  minutesDirty: boolean;
  onRefresh: () => Promise<void>;
  onSeek: (time: number) => void;
  onPause: () => void;
  toast: (text: string) => void;
}) {
  const [text, setText] = useState(""),
    [type, setType] = useState<MeetingNoteType>("note"),
    [scope, setScope] = useState<"personal" | "meeting">("personal"),
    [time, setTime] = useState(0);
  const [profiles, setProfiles] = useState<LLMConfig[]>([]),
    [profileId, setProfileId] = useState(""),
    [busy, setBusy] = useState(false),
    [selected, setSelected] = useState<string | null>(null);
  useEffect(() => {
    api
      .environment()
      .then((env) => {
        setProfiles(env.analysis.profiles);
        setProfileId(env.analysis.default_profile_id || "");
      })
      .catch((error) => toast((error as Error).message));
  }, []);
  const run = async (operation: () => Promise<unknown>) => {
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
  const add = (marker = false) =>
    run(async () => {
      await api.createNote(meeting.id, {
        captured_at: time,
        note_type: marker ? "note" : type,
        raw_text: marker ? "" : text.trim(),
        note_scope: marker ? "personal" : scope,
        is_marker: marker,
      });
      if (!marker) setText("");
    });
  const active = meeting.jobs.find((job) =>
    ["queued", "running"].includes(job.state),
  );
  const selectedNote = meeting.notes.find((note) => note.id === selected);
  return (
    <div className="notes-view">
      <div className="notes-purpose">
        <h3>把现场关注，留给会后核对</h3>
        <p>
          简记保存你的重点、疑问和个人判断。标签表示记录意图，核对后再用于纪要或待办。
        </p>
      </div>
      <details className="notes-expansion-toolbar">
        <summary>AI 整理设置</summary>
        <label>
          整理模型{" "}
          <select
            aria-label="简记扩写模型"
            value={profileId}
            onChange={(e) => setProfileId(e.target.value)}
          >
            {!profiles.length && <option value="">LM Studio 默认模型</option>}
            {profiles.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} · {p.provider_type === "cloud" ? "云端" : "本地"}
              </option>
            ))}
          </select>
        </label>
        <p>云端模型会接收所选简记和附近文字稿；原始记录不会被自动改写。</p>
      </details>
      {active && (
        <p role="status">
          {active.stage} · {Math.round(active.progress * 100)}%{" "}
          {active.kind === "note_expansion" && (
            <button
              onClick={() => void run(() => api.jobAction(active.id, "cancel"))}
            >
              取消扩写
            </button>
          )}
        </p>
      )}
      <label className="note-position">
        位置（秒）{" "}
        <input
          aria-label="补充简记位置"
          type="number"
          min={0}
          value={time}
          onChange={(e) => setTime(Math.max(0, Number(e.target.value) || 0))}
        />
        <button disabled={busy} onClick={() => void add(true)}>
          ⚑ 标记这个位置
        </button>
      </label>
      <div className="note-compose-options">
        <select
          aria-label="补充简记用途"
          value={scope}
          onChange={(e) => setScope(e.target.value as typeof scope)}
        >
          <option value="personal">个人备注</option>
          <option value="meeting">会议内容（待核对）</option>
        </select>
        <select
          aria-label="补充简记标签"
          value={type}
          onChange={(e) => setType(e.target.value as MeetingNoteType)}
        >
          {Object.entries(labels).map(([id, label]) => (
            <option key={id} value={id}>
              {label}
            </option>
          ))}
        </select>
      </div>
      <div className="notes-compose">
        <textarea
          rows={2}
          placeholder="先记关键词、问题或自己的判断…"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === "Enter" && text.trim()) {
              e.preventDefault();
              void add();
            }
          }}
        />
        <button
          className="primary"
          disabled={busy || !text.trim()}
          onClick={() => void add()}
        >
          添加简记
        </button>
      </div>
      {!meeting.notes.length && (
        <div className="empty">
          <h3>还没有简记</h3>
          <p>会中可以一键标记时间，会后补充并核对。</p>
        </div>
      )}
      <div className="notes-list">
        {meeting.notes.map((note) => (
          <NoteCard
            key={note.id}
            note={note}
            disabled={busy}
            onSeek={onSeek}
            onReview={() => setSelected(note.id)}
            onSave={(draft) =>
              run(() =>
                api.updateNote(meeting.id, note.id, {
                  raw_text: draft.raw_text,
                  note_type: draft.note_type,
                  note_scope: draft.note_scope,
                  is_marker: !draft.raw_text.trim() && !!draft.is_marker,
                  captured_at: draft.captured_at,
                }),
              )
            }
            onDelete={() => {
              if (confirm("删除这条简记？已转出的纪要和待办会保留。"))
                void run(() => api.deleteNote(meeting.id, note.id));
            }}
          />
        ))}
      </div>
      <section className="note-todos">
        <h3>由简记转出的待办</h3>
        <p>负责人、期限由你填写；个人跟进不会自动进入对外纪要。</p>
        {!(meeting.note_todos || []).length && (
          <p>核对简记后，点击“转为待办”创建。</p>
        )}
        {(meeting.note_todos || []).map((todo) => (
          <TodoCard
            key={todo.id}
            todo={todo}
            onSave={(value) =>
              run(() => api.updateNoteTodo(meeting.id, todo.id, value))
            }
          />
        ))}
      </section>
      {selectedNote && (
        <ReviewPanel
          note={selectedNote}
          meeting={meeting}
          profileId={profileId}
          minutesDirty={minutesDirty}
          onPause={onPause}
          onRefresh={onRefresh}
          onSeek={onSeek}
          onClose={() => setSelected(null)}
          toast={toast}
        />
      )}
    </div>
  );
}
function NoteCard({
  note,
  disabled,
  onSeek,
  onReview,
  onSave,
  onDelete,
}: {
  note: MeetingNote;
  disabled: boolean;
  onSeek: (time: number) => void;
  onReview: () => void;
  onSave: (note: MeetingNote) => Promise<void>;
  onDelete: () => void;
}) {
  const [draft, setDraft] = useState(note);
  useEffect(
    () => setDraft(note),
    [
      note.raw_text,
      note.note_type,
      note.note_scope,
      note.is_marker,
      note.captured_at,
    ],
  );
  const dirty =
    draft.raw_text !== note.raw_text ||
    draft.note_type !== note.note_type ||
    draft.note_scope !== note.note_scope ||
    draft.captured_at !== note.captured_at;
  return (
    <article className={`note-card note-${draft.note_type}`}>
      <div className="note-meta">
        <button className="note-time" onClick={() => onSeek(note.captured_at)}>
          {clock(note.captured_at)}
        </button>
        <strong>
          {note.is_marker && !note.raw_text
            ? "⚑ 时间标记 · 待补充"
            : note.note_scope === "meeting"
              ? note.adopted_to_minutes
                ? "会议内容 · 已采用到当前纪要"
                : "会议内容 · 待核对"
              : "个人备注"}
        </strong>
        <button
          className="text-btn danger"
          disabled={disabled}
          onClick={onDelete}
        >
          删除
        </button>
      </div>
      <div className="note-compose-options">
        <select
          aria-label="简记用途"
          value={draft.note_scope || "personal"}
          onChange={(e) =>
            setDraft({
              ...draft,
              note_scope: e.target.value as "personal" | "meeting",
            })
          }
        >
          <option value="personal">个人备注</option>
          <option value="meeting">会议内容（待核对）</option>
        </select>
        <select
          aria-label="简记标签"
          value={draft.note_type}
          onChange={(e) =>
            setDraft({ ...draft, note_type: e.target.value as MeetingNoteType })
          }
        >
          {Object.entries(labels).map(([id, label]) => (
            <option key={id} value={id}>
              {label}
            </option>
          ))}
        </select>
      </div>
      <textarea
        aria-label="原始简记"
        rows={2}
        placeholder="回听此刻，补充当时想记录的内容…"
        value={draft.raw_text}
        onChange={(e) => setDraft({ ...draft, raw_text: e.target.value })}
      />
      {dirty && (
        <button
          className="primary"
          disabled={disabled || (!draft.raw_text.trim() && !draft.is_marker)}
          onClick={() => void onSave(draft)}
        >
          保存修改
        </button>
      )}{" "}
      <button disabled={disabled || dirty} onClick={onReview}>
        核对与整理
      </button>
      {note.accepted_text && (
        <p>
          已接受扩写{note.expansion_stale ? "（文稿变化，待复核）" : ""}：
          {note.accepted_text}
        </p>
      )}
    </article>
  );
}
function ReviewPanel({
  note,
  meeting,
  profileId,
  minutesDirty,
  onRefresh,
  onSeek,
  onPause,
  onClose,
  toast,
}: {
  note: MeetingNote;
  meeting: Meeting;
  profileId: string;
  minutesDirty: boolean;
  onRefresh: () => Promise<void>;
  onSeek: (time: number) => void;
  onPause: () => void;
  onClose: () => void;
  toast: (text: string) => void;
}) {
  const [context, setContext] = useState<NoteReview | null>(null),
    [ids, setIds] = useState<string[]>([]),
    [content, setContent] = useState(
      note.accepted_text && !note.expansion_stale
        ? note.accepted_text
        : note.raw_text,
    ),
    [confirmed, setConfirmed] = useState(false),
    [busy, setBusy] = useState(false),
    [message, setMessage] = useState("");
  const suggestion = note.expansion,
    [edited, setEdited] = useState(suggestion?.payload.text || "");
  const [section, setSection] = useState(
    note.note_type === "action"
      ? "action_items"
      : note.note_type === "decision"
        ? "decisions"
        : note.note_type === "question"
          ? "open_questions"
          : "conclusions",
  );
  const minutes = meeting.artifacts.find(
    (a) => a.kind === "meeting_minutes" && a.revision === meeting.revision,
  );
  const job = meeting.jobs.find(
    (j) =>
      j.kind === "note_expansion" && JSON.parse(j.config).note_id === note.id,
  );
  const active = !!job && ["queued", "running"].includes(job.state);
  useEffect(() => {
    let live = true;
    api
      .reviewNote(meeting.id, note.id)
      .then((value) => {
        if (live) {
          setContext(value);
          setIds(
            note.expansion_stale
              ? []
              : note.source_segment_ids.filter((id) =>
                  value.segments.some((s) => s.id === id),
                ),
          );
        }
      })
      .catch((e) => toast((e as Error).message));
    return () => {
      live = false;
    };
  }, [note.id]);
  useEffect(() => {
    setEdited(suggestion?.payload.text || "");
  }, [suggestion?.id]);
  const run = async (operation: () => Promise<unknown>, success: string) => {
    setBusy(true);
    setMessage("");
    try {
      await operation();
      await onRefresh();
      setContext(await api.reviewNote(meeting.id, note.id));
      setConfirmed(false);
      setMessage(success);
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const use = (kind: "minutes" | "todo") =>
    run(
      () =>
        api.useNote(meeting.id, note.id, {
          kind,
          fingerprint: context!.fingerprint,
          revision: context!.revision,
          content,
          source_segment_ids: ids,
          section,
          minutes_id: minutes?.id || null,
          confirmed,
        }),
      kind === "minutes"
        ? "已保存为新的纪要版本，可在会议纪要中查看。"
        : "已转为待办，可在简记下方填写负责人和期限。",
    );
  return (
    <Modal
      className="note-review-dialog"
      onClose={() => {
        onPause();
        onClose();
      }}
      ariaLabel="简记核对与整理"
    >
      <div className="search-heading">
        <h2>简记核对与整理</h2>
        <button
          onClick={() => {
            onPause();
            onClose();
          }}
        >
          关闭
        </button>
      </div>
      <p>先回听原文，再确认用途。扩写是建议，标签不是已核实结论。</p>
      {message && (
        <p role="status" className="note-review-message">
          {message}
        </p>
      )}
      <div className="note-review-grid">
        <section>
          <h3>原始记录</h3>
          <strong>
            {note.note_scope === "meeting"
              ? "会议内容（待核对）"
              : "个人备注 · 不直接进入纪要"}
          </strong>
          <p>{note.raw_text || "⚑ 仅标记了时间，请关闭面板后补充内容。"}</p>
          <button onClick={() => onSeek(note.captured_at)}>
            {clock(note.captured_at)} 回听此刻
          </button>
          <button onClick={onPause}>暂停回听</button>
          <p>标签：{labels[note.note_type]}</p>
          <h3>AI 扩写建议</h3>
          <button
            disabled={
              busy || active || !note.raw_text.trim() || !meeting.revision
            }
            onClick={() =>
              void run(
                () => api.expandNote(meeting.id, note.id, profileId),
                "扩写任务已提交",
              )
            }
          >
            {active ? job?.stage : "生成扩写建议"}
          </button>
          {job?.state === "failed" && <p className="error">{job.error}</p>}
          {suggestion && (
            <>
              <p>{suggestion.payload.reason}</p>
              {suggestion.stale && (
                <p className="error">建议已过期，请重新生成。</p>
              )}
              {suggestion.payload.status === "supported" && (
                <>
                  <textarea
                    aria-label="修改扩写建议"
                    rows={5}
                    value={edited}
                    onChange={(e) => setEdited(e.target.value)}
                  />
                  {suggestion.payload.evidence.map((e, i) => (
                    <blockquote key={i}>{e.quote}</blockquote>
                  ))}
                  <button
                    disabled={busy || suggestion.stale || !edited.trim()}
                    onClick={() =>
                      void run(async () => {
                        await api.acceptNoteExpansion(
                          meeting.id,
                          note.id,
                          suggestion.id,
                          edited,
                        );
                        setContent(edited);
                      }, "扩写已单独保存，原始记录不变。")
                    }
                  >
                    接受当前扩写
                  </button>
                </>
              )}
            </>
          )}
        </section>
        <section>
          <h3>附近文字稿 · v{context?.revision || 0}</h3>
          <p>选择支持内容的片段作为依据，引用不会自动证明结论正确。</p>
          {context && !context.segments.length && (
            <p>附近没有文字稿，可先保存个人待办或完成转写。</p>
          )}
          {context?.segments.map((segment) => (
            <article className="review-source" key={segment.id}>
              <label>
                <input
                  type="checkbox"
                  checked={ids.includes(segment.id)}
                  onChange={(e) => {
                    setIds((old) =>
                      e.target.checked
                        ? [...old, segment.id]
                        : old.filter((id) => id !== segment.id),
                    );
                    setConfirmed(false);
                  }}
                />{" "}
                采用此依据
              </label>
              <button onClick={() => onSeek(segment.start)}>
                {clock(segment.start)} 回听
              </button>
              <p>
                <b>{segment.speaker}</b>：{segment.text}
              </p>
            </article>
          ))}
        </section>
        <section>
          <h3>确认后使用</h3>
          <textarea
            aria-label="核对后的使用内容"
            rows={7}
            value={content}
            onChange={(e) => {
              setContent(e.target.value);
              setConfirmed(false);
            }}
            placeholder="只填写已核对的内容；个人判断保留为个人跟进。"
          />
          <label className="note-confirm">
            <input
              type="checkbox"
              checked={confirmed}
              onChange={(e) => setConfirmed(e.target.checked)}
            />{" "}
            我已核对内容及所选依据，确认本次用途。
          </label>
          <button
            className="primary"
            disabled={busy || !context || !confirmed || !content.trim()}
            onClick={() => void use("todo")}
          >
            转为待办
          </button>
          <h4>采用到当前会议纪要</h4>
          {minutesDirty && (
            <p className="error">纪要有未保存修改，请保存后再采用。</p>
          )}
          <select
            aria-label="采用到纪要章节"
            value={section}
            onChange={(e) => {
              setSection(e.target.value);
              setConfirmed(false);
            }}
          >
            <option value="conclusions">核心结论</option>
            <option value="decisions">已定事项</option>
            <option value="action_items">行动项</option>
            <option value="open_questions">待确认问题</option>
          </select>
          <p>
            {note.note_scope !== "meeting"
              ? "个人备注不能直接采用；如属于会议内容，请先关闭面板修改用途。"
              : !minutes || minutesDirty
                ? "请先生成当前版本纪要，再采用。"
                : "采用将保存为新版本，不覆盖历史纪要。"}
          </p>
          <button
            disabled={
              busy ||
              !context ||
              !confirmed ||
              !ids.length ||
              !content.trim() ||
              note.note_scope !== "meeting" ||
              !minutes ||
              minutesDirty
            }
            onClick={() => void use("minutes")}
          >
            采用到纪要
          </button>
          {context && context.revision !== meeting.revision && (
            <p className="error">文字稿已变化，请关闭后重新核对。</p>
          )}
        </section>
      </div>
    </Modal>
  );
}
function TodoCard({
  todo,
  onSave,
}: {
  todo: NoteTodo;
  onSave: (value: {
    status: "pending" | "done";
    owner: string;
    due_date: string;
  }) => Promise<void>;
}) {
  const [draft, setDraft] = useState({
    status: todo.status,
    owner: todo.owner,
    due_date: todo.due_date,
  });
  useEffect(
    () =>
      setDraft({
        status: todo.status,
        owner: todo.owner,
        due_date: todo.due_date,
      }),
    [todo.status, todo.owner, todo.due_date],
  );
  return (
    <article className="note-todo-card">
      <strong>{todo.content}</strong>
      <small>
        {todo.note_scope === "personal" ? "个人跟进" : "会议内容转出"}
        {todo.stale ? " · 来源变化，待复核" : ""}
      </small>
      <div className="note-compose-options">
        <label>
          <input
            type="checkbox"
            checked={draft.status === "done"}
            onChange={(e) => {
              const next = {
                ...draft,
                status: e.target.checked
                  ? ("done" as const)
                  : ("pending" as const),
              };
              setDraft(next);
              void onSave(next);
            }}
          />{" "}
          已完成
        </label>
        <input
          aria-label="待办负责人"
          placeholder="负责人（手动填写）"
          value={draft.owner}
          onChange={(e) => setDraft({ ...draft, owner: e.target.value })}
        />
        <input
          aria-label="待办期限"
          placeholder="期限（未注明留空）"
          value={draft.due_date}
          onChange={(e) => setDraft({ ...draft, due_date: e.target.value })}
        />
        <button onClick={() => void onSave(draft)}>保存跟进信息</button>
      </div>
    </article>
  );
}
