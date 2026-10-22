import { useEffect, useState } from "react";
import { api } from "../../api";
import Modal from "../../components/Modal";
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
  return (
    <Modal className="search-dialog" onClose={onClose} ariaLabel="跨会议跟进">
      <div className="search-heading">
        <h2>跨会议跟进</h2>
        <button onClick={onClose}>关闭</button>
      </div>
      <p>
        集中管理本机会议中已确认的跟进项。完成状态保存在本机，不会自动通知负责人或同步外部系统。
      </p>
      <label>
        <input
          type="checkbox"
          checked={all}
          onChange={(e) => setAll(e.target.checked)}
        />
        包含已完成
      </label>
      {error && <p role="alert">{error}</p>}
      <fieldset disabled={busy}>
        {rows
          .filter((t) => all || t.status === "pending")
          .map((todo) => (
            <section key={todo.id}>
              <button
                className="text-btn"
                onClick={() => {
                  setBusy(true);
                  void onOpen(todo.meeting_id)
                    .then(onClose)
                    .catch((e) => setError(e.message))
                    .finally(() => setBusy(false));
                }}
              >
                来源会议：{todo.meeting_title}
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
      {!rows.some((t) => all || t.status === "pending") && (
        <p>暂无符合条件的跟进事项。可从会后整理或会议成果中核对后加入。</p>
      )}
    </Modal>
  );
}
