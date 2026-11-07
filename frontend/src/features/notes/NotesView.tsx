import { useModelChoice } from "../../preferences/useModelChoice";
import { useEffect, useState, useRef } from "react";
import { api } from "../../api";
import type {
  Meeting,
  MeetingNote,
  MeetingNoteType,
  NoteReview,
  NoteTodo,
} from "../../types";
import { clock, currentMinutes } from "../../utils";
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
  onMinutes,
  inlineReview = false,
  focusNoteId,
  onNoteFocused,
  onLocate,
  onConfigureModel,
}: {
  onConfigureModel: () => void;
  inlineReview?: boolean;
  focusNoteId?: string;
  onNoteFocused?: () => void;
  onLocate?: (time: number) => void;
  meeting: Meeting;
  onMinutes: () => void;
  minutesDirty: boolean;
  onRefresh: () => Promise<void>;
  onSeek: (time: number) => void;
  onPause: () => void;
  toast: (text: string) => void;
}) {
  const [filter, setFilter] = useState("pending");
  const flow = meeting.flow;
  const stateOf = (id: string) =>
    flow?.attention.find((n) => n.note_id === id)?.state || "pending";
  const pending =
    (flow?.counts.pending || 0) +
    (flow?.counts.stale || 0) +
    (flow?.counts.deferred || 0);
  const [text, setText] = useState(""),
    [type, setType] = useState<MeetingNoteType>("note"),
    [scope, setScope] = useState<"personal" | "meeting">("personal"),
    [time, setTime] = useState(0);
  const [profileId] = useModelChoice("notes");
  const [busy, setBusy] = useState(false),
    [selected, setSelected] = useState<string | null>(null);
  useEffect(() => {
    if (focusNoteId && meeting.notes.some((n) => n.id === focusNoteId)) {
      setFilter("all");
      setSelected(focusNoteId);
      onNoteFocused?.();
    }
  }, [focusNoteId, meeting.notes, onNoteFocused]);
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
  if (inlineReview && selectedNote)
    return (
      <ReviewPanel
        key={selectedNote.id}
        inline
        note={selectedNote}
        meeting={meeting}
        profileId={profileId}
        onConfigureModel={onConfigureModel}
        minutesDirty={minutesDirty}
        onPause={onPause}
        onRefresh={onRefresh}
        onSeek={onSeek}
        onClose={() => setSelected(null)}
        toast={toast}
      />
    );
  return (
    <div className="notes-view">
      <div className="attention-summary">
        <div className="attention-heading">
          <h3>会议关注点</h3>
          <span className="muted">
            {pending} 待核对 · {meeting.notes.length} 条简记
          </span>
        </div>
        <label>
          查看关注点
          <select
            aria-label="关注点筛选"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          >
            <option value="pending">待处理／待复核</option>
            <option value="all">全部</option>
            <option value="meeting">已确认可用</option>
            <option value="personal">个人保留</option>
          </select>
        </label>
        <button className="text-btn" onClick={onMinutes}>
          查看会议成果 →
        </button>
      </div>
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
      <details className="attention-add">
        <summary>补充关注点或时间标记</summary>
        <label className="note-position">
          位置（秒）{" "}
          <input
            aria-label="补充简记位置"
            type="number"
            min={0}
            value={time}
            onChange={(e) => setTime(Math.max(0, Number(e.target.value) || 0))}
          />
        </label>
        <button disabled={busy} onClick={() => void add(true)}>
          仅添加时间标记
        </button>
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
              if (
                (e.ctrlKey || e.metaKey) &&
                e.key === "Enter" &&
                text.trim()
              ) {
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
      </details>
      {meeting.notes.length > 0 &&
        filter === "pending" &&
        !meeting.notes.some((note) =>
          ["pending", "stale", "deferred"].includes(stateOf(note.id)),
        ) && (
          <p className="muted">
            没有待核对的关注点。
            <button className="text-btn" onClick={() => setFilter("all")}>
              查看全部
            </button>
          </p>
        )}
      {!meeting.notes.length && (
        <div className="empty">
          <h3>还没有简记</h3>
          <p>会中可以一键标记时间，会后补充并核对。</p>
        </div>
      )}
      <div className="notes-list">
        {meeting.notes
          .filter(
            (note) =>
              filter === "all" ||
              (filter === "pending"
                ? ["pending", "stale", "deferred"].includes(stateOf(note.id))
                : stateOf(note.id) === filter),
          )
          .map((note) => (
            <NoteCard
              key={note.id}
              note={note}
              disposition={stateOf(note.id)}
              resolvedContent={
                flow?.attention.find((n) => n.note_id === note.id)?.review
                  ?.decision === "meeting" &&
                !flow?.attention.find((n) => n.note_id === note.id)?.review
                  ?.stale
                  ? flow?.attention.find((n) => n.note_id === note.id)?.review
                      ?.content
                  : undefined
              }
              disabled={busy}
              onSeek={onSeek}
              onReview={() => {
                setSelected(note.id);
                onLocate?.(note.captured_at);
              }}
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
      <details className="note-todos">
        <summary>
          已转出的跟进项 · {(meeting.note_todos || []).length}
          （也可在会议成果中管理）
        </summary>
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
      </details>
      {selectedNote && (
        <ReviewPanel
          note={selectedNote}
          meeting={meeting}
          profileId={profileId}
          onConfigureModel={onConfigureModel}
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
  disposition,
  resolvedContent,
  disabled,
  onSeek,
  onReview,
  onSave,
  onDelete,
}: {
  note: MeetingNote;
  disposition: string;
  resolvedContent?: string;
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
        <span className="attention-state">
          {
            {
              pending: "待核对",
              meeting: "已确认可用于会议成果",
              personal: "个人保留",
              deferred: "稍后处理",
              stale: "内容变化，待复核",
            }[disposition]
          }
        </span>
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
      <p className="attention-content">
        {resolvedContent || note.raw_text || "仅标记了时间，回听后补充内容。"}
      </p>
      <details>
        <summary>编辑原始记录 · {labels[note.note_type]}</summary>
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
              setDraft({
                ...draft,
                note_type: e.target.value as MeetingNoteType,
              })
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
      </details>
      <button
        className="primary"
        disabled={disabled || dirty}
        onClick={onReview}
      >
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
function ReviewContainer({
  inline,
  children,
  onClose,
  ...props
}: {
  inline: boolean;
  children: React.ReactNode;
  onClose: () => void;
  className: string;
  ariaLabel: string;
}) {
  const panel = useRef<HTMLElement>(null);
  useEffect(() => {
    if (inline) panel.current?.parentElement?.scrollTo({ top: 0 });
  }, [inline]);
  return inline ? (
    <section
      ref={panel}
      className="inline-note-review"
      aria-label={props.ariaLabel}
    >
      {children}
    </section>
  ) : (
    <Modal {...props} onClose={onClose}>
      {children}
    </Modal>
  );
}
function ReviewPanel({
  inline = false,
  note,
  meeting,
  profileId,
  onConfigureModel,
  minutesDirty,
  onRefresh,
  onSeek,
  onPause,
  onClose,
  toast,
}: {
  inline?: boolean;
  note: MeetingNote;
  meeting: Meeting;
  profileId: string;
  onConfigureModel: () => void;
  minutesDirty: boolean;
  onRefresh: () => Promise<void>;
  onSeek: (time: number) => void;
  onPause: () => void;
  onClose: () => void;
  toast: (text: string) => void;
}) {
  const disposition = meeting.flow?.attention.find(
    (n) => n.note_id === note.id,
  )?.review;
  const [context, setContext] = useState<NoteReview | null>(null),
    [ids, setIds] = useState<string[]>([]),
    [content, setContent] = useState(
      disposition?.decision === "meeting" && !disposition.stale
        ? disposition.content
        : note.accepted_text && !note.expansion_stale
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
  const minutes = currentMinutes(meeting);
  const job = meeting.jobs.find(
    (j) =>
      j.kind === "note_expansion" && JSON.parse(j.config).note_id === note.id,
  );
  const active = !!job && ["queued", "running"].includes(job.state);
  useEffect(() => {
    let live = true;
    setConfirmed(false);
    setContext(null);
    setIds([]);
    api
      .reviewNote(meeting.id, note.id)
      .then((value) => {
        if (live) {
          setContext(value);
          setIds(
            note.expansion_stale
              ? []
              : (disposition?.decision === "meeting" && !disposition.stale
                  ? disposition.source_segment_ids
                  : note.source_segment_ids
                ).filter((id) => value.segments.some((s) => s.id === id)),
          );
        }
      })
      .catch((e) => toast((e as Error).message));
    return () => {
      live = false;
    };
  }, [
    note.id,
    meeting.revision,
    note.raw_text,
    note.captured_at,
    note.note_scope,
  ]);
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
    <ReviewContainer
      inline={inline}
      className="note-review-dialog"
      onClose={() => {
        onPause();
        onClose();
      }}
      ariaLabel="简记核对与整理"
    >
      <div className="search-heading">
        <h2>核对关注点 · 原声、表述与去向</h2>
        <button
          onClick={() => {
            onPause();
            onClose();
          }}
        >
          {inline ? "返回关注点列表" : "关闭"}
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
          {!profileId && (
            <button className="text-btn" onClick={onConfigureModel}>
              在运行管理中配置扩写模型
            </button>
          )}
          <button
            disabled={
              busy ||
              active ||
              !profileId ||
              !note.raw_text.trim() ||
              !meeting.revision
            }
            onClick={async () => {
              try {
                const selected = (await api.configs()).find(
                  (p) => p.id === profileId,
                );
                if (!selected) return toast("请选择有效模型配置");
                if (
                  selected.provider_type === "cloud" &&
                  !window.confirm(
                    `本条简记及用于关联的会议文字稿将发送至 ${selected.name}（${selected.base_url}），是否继续？`,
                  )
                )
                  return;
                void run(
                  () => api.expandNote(meeting.id, note.id, profileId),
                  "扩写任务已提交",
                );
              } catch (e) {
                toast((e as Error).message);
              }
            }}
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
          {meeting.flow && (
            <div className="attention-decisions">
              <button
                className="primary"
                disabled={
                  busy ||
                  !context ||
                  !confirmed ||
                  !content.trim() ||
                  !ids.length
                }
                onClick={() =>
                  void run(
                    () =>
                      api.attentionDecision(meeting.id, note.id, {
                        decision: "meeting",
                        fingerprint: context!.fingerprint,
                        revision: context!.revision,
                        content,
                        source_segment_ids: ids,
                        confirmed,
                      }),
                    "已确认可用于会议成果；下次生成 AI 纪要会使用核对后的表述与原文依据。",
                  )
                }
              >
                确认纳入会议成果
              </button>
              <button
                disabled={busy || !context}
                onClick={() =>
                  void run(
                    () =>
                      api.attentionDecision(meeting.id, note.id, {
                        decision: "personal",
                        fingerprint: context!.fingerprint,
                        revision: context!.revision,
                        content: "",
                        source_segment_ids: [],
                        confirmed: true,
                      }),
                    "已保留为个人备注，不进入 AI 纪要。已有导出和转出内容请另行核对。",
                  )
                }
              >
                保留个人备注
              </button>
              <button
                disabled={busy || !context}
                onClick={() =>
                  void run(
                    () =>
                      api.attentionDecision(meeting.id, note.id, {
                        decision: "deferred",
                        fingerprint: context!.fingerprint,
                        revision: context!.revision,
                        content: "",
                        source_segment_ids: [],
                        confirmed: true,
                      }),
                    "已标记稍后处理。",
                  )
                }
              >
                暂不处理
              </button>
            </div>
          )}
          <small>
            “纳入会议成果”保存可用内容，不要求先生成纪要，也不会自动发送。个人备注与暂不处理不会提供给纪要模型。
          </small>
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
            {note.note_scope !== "meeting" &&
            !(disposition?.decision === "meeting" && !disposition.stale)
              ? "请先在此面板确认纳入会议成果，再采用到纪要。"
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
              (note.note_scope !== "meeting" &&
                !(disposition?.decision === "meeting" && !disposition.stale)) ||
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
    </ReviewContainer>
  );
}
export function TodoCard({
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
        {todo.carried
          ? `沿用：${todo.source_meeting_title || "上次会议"}`
          : todo.note_scope === "personal"
            ? "个人跟进"
            : "会议跟进"}
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
