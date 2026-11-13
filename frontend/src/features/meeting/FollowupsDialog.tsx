import { useEffect, useState } from "react";
import { api } from "../../api";
import { IconCheckSquare } from "../../styles/icons";
import { TodoCard } from "../notes/NotesView";
import type { NoteTodo } from "../../types";

export default function FollowupsDialog({
  onClose,
  onOpen,
}: {
  onClose: () => void;
  onOpen: (id: string) => Promise<void>;
}) {
  const [rows, setRows] = useState<
      (NoteTodo & { meeting_id: string; meeting_title: string })[]
    >([]),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [all, setAll] = useState(false);

  const refresh = async () => setRows(await api.followups());

  useEffect(() => {
    let live = true;
    void api
      .followups()
      .then((value) => live && setRows(value))
      .catch((e) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, []);

  const visibleRows = rows.filter((t) => all || t.status === "pending");

  return (
    <section aria-label="跨会议跟进" className="shell-page followups-page">
      <div className="followups-header">
        <div className="followups-title-group">
          <h2>
            <IconCheckSquare size={18} />
            <span>跨会议跟进</span>
            <span className="meeting-library-header-meta">
              待处理 {rows.filter((t) => t.status === "pending").length} 项
            </span>
          </h2>
          <p className="followups-subtitle">
            集中管理本机会议中已确认的跟进项。完成状态保存在本机，不会自动通知负责人或同步外部系统。
          </p>
        </div>
        <div className="followups-header-actions">
          <label className="followups-filter-toggle">
            <input
              type="checkbox"
              checked={all}
              onChange={(e) => setAll(e.target.checked)}
            />
            包含已完成
          </label>
          <button type="button" onClick={onClose}>
            返回会议
          </button>
        </div>
      </div>

      <div className="followups-body">
        {error && <p role="alert">{error}</p>}
        <fieldset disabled={busy} className="followups-list">
          {visibleRows.map((todo) => (
            <section key={todo.id} className="followups-card">
              <button
                type="button"
                className="text-btn followups-source-btn"
                onClick={() => {
                  setBusy(true);
                  void onOpen(todo.meeting_id)
                    .catch((e) => setError(e.message))
                    .finally(() => setBusy(false));
                }}
              >
                来源会议：{todo.meeting_title} ↗
              </button>
              <TodoCard
                todo={todo}
                onSave={async (value) => {
                  setBusy(true);
                  try {
                    await api.updateNoteTodo(todo.meeting_id, todo.id, value);
                    await refresh();
                  } catch (e) {
                    setError((e as Error).message);
                  } finally {
                    setBusy(false);
                  }
                }}
              />
            </section>
          ))}
        </fieldset>
        {!visibleRows.length && (
          <div className="followups-empty">
            <p>暂无符合条件的跟进事项。可从会后整理或会议成果中核对后加入。</p>
          </div>
        )}
      </div>
    </section>
  );
}
