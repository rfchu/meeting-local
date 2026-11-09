import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import Modal from "../../components/Modal";
import { api } from "../../api";
import { clock } from "../../utils";
import type { MeetingOrganization, MeetingSummary } from "../../types";

const kinds = { project: "项目", customer: "客户", series: "会议系列" };
const groupKey = (item: MeetingSummary) =>
  item.library?.group_name
    ? JSON.stringify([item.library.group_kind, item.library.group_name])
    : "";

function formatCreatedDate(isoString?: string) {
  if (!isoString) return "—";
  try {
    return isoString.replace("T", " ").slice(0, 16);
  } catch {
    return isoString;
  }
}

function getMeetingStatusBadge(item: MeetingSummary) {
  if (
    item.job &&
    (item.job.state === "running" || item.job.state === "queued")
  ) {
    return <span className="library-badge badge-running">转写中</span>;
  }
  if (item.revision > 0) {
    return <span className="library-badge badge-success">已转写</span>;
  }
  return <span className="library-badge badge-pending">待转写</span>;
}

function getCategoryTag(item: MeetingSummary) {
  if (!item.library?.group_name) {
    return <span className="library-tag-empty">—</span>;
  }
  const kindLabel = kinds[item.library.group_kind] || "系列";
  return (
    <span
      className="library-tag"
      title={`${kindLabel}: ${item.library.group_name}`}
    >
      <span className="tag-kind">{kindLabel}:</span>
      <span className="tag-name">{item.library.group_name}</span>
    </span>
  );
}

export default function MeetingLibrary({
  meetings,
  currentMeetingId,
  onOpen,
  onPrepare,
  onClose,
  onDeletedActiveMeeting,
}: {
  meetings: MeetingSummary[];
  currentMeetingId?: string;
  onOpen: (id: string, segmentId?: string) => void;
  onPrepare: (id: string) => Promise<void>;
  onClose: () => void;
  onDeletedActiveMeeting?: () => void;
}) {
  const client = useQueryClient();
  const [filter, setFilter] = useState("");
  const [search, setSearch] = useState("");
  const [archived, setArchived] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [group, setGroup] = useState("");
  const [kind, setKind] = useState<MeetingOrganization["group_kind"]>("series");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);

  const activeCount = meetings.filter((item) => !item.library?.archived).length;
  const archivedCount = meetings.filter((item) => !!item.library?.archived).length;

  const normalizedSearch = search.trim().toLowerCase();
  const visible = meetings.filter((item) => {
    if (!!item.library?.archived !== archived) return false;
    if (filter && groupKey(item) !== filter) return false;
    if (normalizedSearch) {
      const titleMatch = item.title.toLowerCase().includes(normalizedSearch);
      const dateMatch = item.created.toLowerCase().includes(normalizedSearch);
      const groupMatch =
        item.library?.group_name &&
        item.library.group_name.toLowerCase().includes(normalizedSearch);
      if (!titleMatch && !dateMatch && !groupMatch) return false;
    }
    return true;
  });
  const groups = [
    ...new Map(
      meetings
        .filter(groupKey)
        .map((item) => [
          groupKey(item),
          `${kinds[item.library!.group_kind]} · ${item.library!.group_name}`,
        ]),
    ).entries(),
  ];
  const followups = useQuery({
    queryKey: ["followups"],
    queryFn: api.followups,
    enabled: !!filter,
  });
  const members = meetings.filter(
    (item) => !!filter && groupKey(item) === filter,
  );
  const tasks = followups.data?.filter(
    (task) =>
      task.status === "pending" &&
      members.some((item) => item.id === task.meeting_id),
  );
  async function update(body: { group?: string; archived?: boolean }) {
    if (busy || !selected.length) return;
    setBusy(true);
    setMessage("");
    try {
      const result = await api.organizeMeetings({
        meeting_ids: selected,
        group_kind: kind,
        ...body,
      });
      await client.invalidateQueries({ queryKey: ["meetings"] });
      setSelected([]);
      setMessage(`已更新 ${result.updated} 场会议`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }
  async function prepare(id: string) {
    setBusy(true);
    setMessage("");
    try {
      await onPrepare(id);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "关联上次会议失败");
    } finally {
      setBusy(false);
    }
  }

  const allSelected =
    visible.length > 0 && visible.every((item) => selected.includes(item.id));

  function toggleSelectAll() {
    if (allSelected) {
      const visibleSet = new Set(visible.map((item) => item.id));
      setSelected((prev) => prev.filter((id) => !visibleSet.has(id)));
    } else {
      const merged = new Set([...selected, ...visible.map((item) => item.id)]);
      setSelected([...merged]);
    }
  }

  async function deleteSelected() {
    if (busy || !selected.length) return;
    setBusy(true);
    setMessage("");
    const toDelete = [...selected];
    const errors: string[] = [];
    let deletedCount = 0;
    let deletedActive = false;

    for (const id of toDelete) {
      try {
        await api.remove(id);
        deletedCount++;
        if (currentMeetingId && id === currentMeetingId) {
          deletedActive = true;
        }
      } catch (err) {
        const target = meetings.find((m) => m.id === id);
        const title = target?.title || id;
        const reason = err instanceof Error ? err.message : "未知错误";
        errors.push(`《${title}》: ${reason}`);
      }
    }

    await client.invalidateQueries({ queryKey: ["meetings"] });
    setSelected([]);
    setDeleteConfirmOpen(false);

    if (deletedActive) {
      onDeletedActiveMeeting?.();
    }

    if (errors.length === 0) {
      setMessage(`已成功永久删除 ${deletedCount} 场会议`);
    } else {
      setMessage(
        `已删除 ${deletedCount} 场会议，${errors.length} 场删除失败: ${errors.join("; ")}`,
      );
    }
    setBusy(false);
  }
  return (
    <Modal
      ariaLabel="会议资料库"
      onClose={onClose}
      className="meeting-library-dialog"
    >
      <div className="meeting-library-header">
        <h2>
          <span>📁 会议资料库</span>
          <span className="meeting-library-header-meta">
            共 {visible.length} 场
          </span>
        </h2>
        <button
          className="text-btn"
          style={{ fontSize: 16, cursor: "pointer" }}
          onClick={onClose}
          aria-label="关闭"
        >
          ✕
        </button>
      </div>

      <div className="meeting-library-body">
        <div className="library-top-bar">
          <div className="library-filter-group">
            <div className="library-segmented" role="tablist">
              <button
                type="button"
                role="tab"
                aria-selected={!archived}
                className={`library-segment-tab ${!archived ? "active" : ""}`}
                disabled={busy}
                onClick={() => {
                  setArchived(false);
                  setSelected([]);
                }}
              >
                全部会议 ({activeCount})
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={archived}
                className={`library-segment-tab ${archived ? "active" : ""}`}
                disabled={busy}
                onClick={() => {
                  setArchived(true);
                  setSelected([]);
                }}
              >
                已归档 ({archivedCount})
              </button>
            </div>

            <label
              style={{
                fontSize: 13,
                fontWeight: 500,
                color: "#475569",
                display: "flex",
                alignItems: "center",
                gap: 6,
                marginLeft: 4,
              }}
            >
              归类筛选:
              <select
                value={filter}
                disabled={busy}
                onChange={(event) => {
                  setFilter(event.target.value);
                  setSelected([]);
                }}
                style={{ padding: "4px 8px", borderRadius: 6, fontSize: 13 }}
              >
                <option value="">全部归类</option>
                {groups.map(([id, label]) => (
                  <option key={id} value={id}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <div className="library-search-box">
            <input
              type="text"
              value={search}
              placeholder="搜索会议名称、归类或日期..."
              onChange={(e) => {
                setSearch(e.target.value);
                setSelected([]);
              }}
              aria-label="搜索会议"
            />
            {search && (
              <button
                type="button"
                className="library-search-clear"
                onClick={() => setSearch("")}
                aria-label="清除搜索"
              >
                ✕
              </button>
            )}
          </div>
        </div>

        {filter && (
          <section className="library-group-card">
            <div className="library-group-card-header">
              <span>📌 归类专属跟进与关联</span>
              <div className="library-group-card-actions">
                {members[0] && (
                  <button
                    className="library-action-btn"
                    onClick={() => onOpen(members[0].id)}
                  >
                    打开最近会议 · {members[0].title}
                  </button>
                )}
                {members[0] && (
                  <button
                    className="library-action-btn"
                    disabled={busy}
                    onClick={() => void prepare(members[0].id)}
                  >
                    关联最近会议并准备下一场
                  </button>
                )}
              </div>
            </div>
            {followups.isPending && (
              <p style={{ margin: 0, color: "#64748b", fontSize: 12 }}>
                正在读取跟进事项…
              </p>
            )}
            {followups.isError && (
              <button
                className="text-btn"
                onClick={() => void followups.refetch()}
              >
                重试读取跟进事项
              </button>
            )}
            {tasks?.length === 0 && (
              <p style={{ margin: 0, color: "#64748b", fontSize: 12 }}>
                暂无未完成事项
              </p>
            )}
            {tasks && tasks.length > 0 && (
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 4,
                  fontSize: 12.5,
                }}
              >
                {tasks.map((task) => (
                  <div key={task.id}>
                    <button
                      className="text-btn"
                      style={{ textAlign: "left", padding: "2px 0" }}
                      onClick={() =>
                        onOpen(
                          task.meeting_id,
                          task.stale ? undefined : task.source_segment_ids?.[0],
                        )
                      }
                    >
                      • {task.content}{" "}
                      <span style={{ color: "#64748b" }}>
                        ({task.meeting_title}
                        {task.stale ? " · 来源待核对" : ""})
                      </span>
                    </button>
                  </div>
                ))}
              </div>
            )}
          </section>
        )}

        {selected.length > 0 && (
          <div className="library-batch-bar">
            <div className="library-batch-left">
              <span className="library-batch-count">
                已选中 {selected.length} 场
              </span>
              <button
                type="button"
                className="text-btn"
                style={{ fontSize: 12, padding: "2px 6px" }}
                onClick={() => setSelected([])}
              >
                取消勾选
              </button>
            </div>
            <div className="library-batch-right">
              <select
                aria-label="归类类型"
                value={kind}
                disabled={busy}
                onChange={(event) => setKind(event.target.value as typeof kind)}
                style={{ padding: "4px 8px", borderRadius: 6, fontSize: 12 }}
              >
                {Object.entries(kinds).map(([id, label]) => (
                  <option key={id} value={id}>
                    {label}
                  </option>
                ))}
              </select>
              <input
                aria-label="归类名称"
                placeholder="输入归类名称"
                value={group}
                maxLength={100}
                disabled={busy}
                onChange={(event) => setGroup(event.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && group.trim()) {
                    void update({ group });
                  }
                }}
                style={{
                  padding: "4px 8px",
                  borderRadius: 6,
                  fontSize: 12,
                  width: 130,
                }}
              />
              <button
                disabled={busy || !group.trim()}
                onClick={() => void update({ group })}
              >
                设置归类
              </button>
              <button
                disabled={busy}
                onClick={() => void update({ group: "" })}
              >
                清除归类
              </button>
              <button
                disabled={busy}
                onClick={() => void update({ archived: !archived })}
              >
                {archived ? "恢复到资料库" : "归档所选"}
              </button>
              <button
                type="button"
                className="danger-btn"
                disabled={busy}
                onClick={() => setDeleteConfirmOpen(true)}
              >
                删除所选 ({selected.length})
              </button>
            </div>
          </div>
        )}

        {message && (
          <div
            role="status"
            style={{
              margin: "8px 24px 0",
              padding: "8px 14px",
              borderRadius: 6,
              background: message.includes("失败") ? "#fef2f2" : "#f0fdf4",
              color: message.includes("失败") ? "#991b1b" : "#166534",
              border: `1px solid ${message.includes("失败") ? "#fecaca" : "#bbf7d0"}`,
              fontSize: 13,
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <span>{message}</span>
            <button
              type="button"
              className="text-btn"
              style={{ fontSize: 12, padding: "0 4px" }}
              onClick={() => setMessage("")}
            >
              ✕
            </button>
          </div>
        )}

        <div className="library-table-container">
          {!visible.length ? (
            <div className="library-empty-state">
              {search ? (
                <div>
                  <p>🔍 未搜索到匹配「{search}」的会议</p>
                  <button
                    type="button"
                    className="library-action-btn"
                    style={{ marginTop: 8 }}
                    onClick={() => setSearch("")}
                  >
                    清除搜索条件
                  </button>
                </div>
              ) : archived ? (
                <p>📭 暂无已归档会议</p>
              ) : (
                <p>📭 暂无会议记录</p>
              )}
            </div>
          ) : (
            <table className="library-table">
              <thead>
                <tr>
                  <th className="library-col-checkbox">
                    <input
                      type="checkbox"
                      aria-label="全选当前列表"
                      checked={allSelected}
                      onChange={toggleSelectAll}
                    />
                  </th>
                  <th className="library-col-title">会议名称</th>
                  <th className="library-col-tag">系列 / 归类</th>
                  <th className="library-col-duration">时长</th>
                  <th className="library-col-status">状态</th>
                  <th className="library-col-date">创建时间</th>
                  <th className="library-col-actions">操作</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((item) => {
                  const isChecked = selected.includes(item.id);
                  return (
                    <tr
                      key={item.id}
                      className={isChecked ? "selected" : undefined}
                    >
                      <td className="library-col-checkbox">
                        <input
                          type="checkbox"
                          aria-label={`选择 ${item.title}`}
                          checked={isChecked}
                          disabled={
                            busy ||
                            (selected.length >= 100 &&
                              !selected.includes(item.id))
                          }
                          onChange={(e) => {
                            if (e.target.checked) {
                              setSelected([...selected, item.id]);
                            } else {
                              setSelected(
                                selected.filter((id) => id !== item.id),
                              );
                            }
                          }}
                        />
                      </td>
                      <td className="library-col-title">
                        <button
                          type="button"
                          className="library-title-btn"
                          onClick={() => onOpen(item.id)}
                          title={`打开会议: ${item.title}`}
                        >
                          {item.title}
                        </button>
                      </td>
                      <td className="library-col-tag">
                        {getCategoryTag(item)}
                      </td>
                      <td className="library-col-duration">
                        {item.duration > 0 ? clock(item.duration) : "—"}
                      </td>
                      <td className="library-col-status">
                        {getMeetingStatusBadge(item)}
                      </td>
                      <td className="library-col-date">
                        {formatCreatedDate(item.created)}
                      </td>
                      <td className="library-col-actions">
                        <button
                          type="button"
                          className="library-action-btn"
                          onClick={() => onOpen(item.id)}
                        >
                          打开 ↗
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>
      {deleteConfirmOpen && (
        <Modal
          ariaLabel="确认批量删除"
          className="small-dialog"
          onClose={() => !busy && setDeleteConfirmOpen(false)}
        >
          <div className="search-heading">
            <h3 style={{ margin: 0, color: "#dc2626" }}>⚠️ 确认永久删除会议</h3>
            <button
              disabled={busy}
              type="button"
              onClick={() => setDeleteConfirmOpen(false)}
            >
              ✕
            </button>
          </div>
          <p style={{ marginTop: 12, lineHeight: 1.6 }}>
            您已选择 <strong>{selected.length}</strong> 场会议。
            删除后将<strong>永久清除</strong>
            本地录音音频文件、转写文字稿、AI 纪要与分析数据，
            <strong>此操作无法撤销</strong>。
          </p>
          <div
            style={{
              maxHeight: 160,
              overflowY: "auto",
              padding: "8px 12px",
              background: "#f8fafc",
              borderRadius: 6,
              border: "1px solid #e2e8f0",
              fontSize: 13,
            }}
          >
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {meetings
                .filter((m) => selected.includes(m.id))
                .map((m) => (
                  <li key={m.id} style={{ marginBottom: 4 }}>
                    {m.title}
                  </li>
                ))}
            </ul>
          </div>
          <div
            style={{
              display: "flex",
              justifyContent: "flex-end",
              gap: 10,
              marginTop: 16,
            }}
          >
            <button
              type="button"
              disabled={busy}
              onClick={() => setDeleteConfirmOpen(false)}
            >
              取消
            </button>
            <button
              type="button"
              className="danger-btn"
              disabled={busy}
              onClick={() => void deleteSelected()}
            >
              {busy ? "正在删除..." : `确认删除 (${selected.length})`}
            </button>
          </div>
        </Modal>
      )}
    </Modal>
  );
}
