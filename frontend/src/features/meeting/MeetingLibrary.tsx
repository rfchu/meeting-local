import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import Modal from "../../components/Modal";
import { api } from "../../api";
import type { MeetingOrganization, MeetingSummary } from "../../types";

const kinds = { project: "项目", customer: "客户", series: "会议系列" };
const groupKey = (item: MeetingSummary) =>
  item.library?.group_name
    ? JSON.stringify([item.library.group_kind, item.library.group_name])
    : "";

export default function MeetingLibrary({
  meetings,
  onOpen,
  onPrepare,
  onClose,
}: {
  meetings: MeetingSummary[];
  onOpen: (id: string, segmentId?: string) => void;
  onPrepare: (id: string) => Promise<void>;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const [filter, setFilter] = useState("");
  const [archived, setArchived] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [group, setGroup] = useState("");
  const [kind, setKind] = useState<MeetingOrganization["group_kind"]>("series");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const visible = meetings.filter(
    (item) =>
      !!item.library?.archived === archived &&
      (!filter || groupKey(item) === filter),
  );
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
  return (
    <Modal
      ariaLabel="会议资料库"
      onClose={onClose}
      className="handoff-sources-dialog"
    >
      <div className="overview-heading">
        <h2>会议资料库</h2>
        <button onClick={onClose}>关闭</button>
      </div>
      <label>
        归类筛选{" "}
        <select
          value={filter}
          disabled={busy}
          onChange={(event) => {
            setFilter(event.target.value);
            setSelected([]);
          }}
        >
          <option value="">全部归类</option>
          {groups.map(([id, label]) => (
            <option key={id} value={id}>
              {label}
            </option>
          ))}
        </select>
      </label>
      <label>
        <input
          type="checkbox"
          checked={archived}
          disabled={busy}
          onChange={(event) => {
            setArchived(event.target.checked);
            setSelected([]);
          }}
        />{" "}
        查看归档
      </label>
      {filter && (
        <section>
          <h3>最近会议与未完成事项</h3>
          {members[0] && (
            <button onClick={() => onOpen(members[0].id)}>
              打开最近会议 · {members[0].title}
            </button>
          )}
          {members[0] && (
            <button disabled={busy} onClick={() => void prepare(members[0].id)}>
              关联最近会议并准备下一场
            </button>
          )}
          {followups.isPending && <p>正在读取跟进事项…</p>}
          {followups.isError && (
            <button onClick={() => void followups.refetch()}>
              重试读取跟进事项
            </button>
          )}
          {tasks?.length === 0 && <p>暂无未完成事项</p>}
          {tasks?.map((task) => (
            <p key={task.id}>
              <button
                className="text-btn"
                onClick={() =>
                  onOpen(
                    task.meeting_id,
                    task.stale ? undefined : task.source_segment_ids?.[0],
                  )
                }
              >
                {task.content} · {task.meeting_title}
                {task.stale ? " · 来源待核对" : ""}
              </button>
            </p>
          ))}
        </section>
      )}
      <fieldset disabled={busy}>
        <legend>已选 {selected.length} 场</legend>
        <select
          aria-label="归类类型"
          value={kind}
          onChange={(event) => setKind(event.target.value as typeof kind)}
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
          onChange={(event) => setGroup(event.target.value)}
        />
        <button
          disabled={!selected.length || !group.trim()}
          onClick={() => void update({ group })}
        >
          设置归类
        </button>
        <button
          disabled={!selected.length}
          onClick={() => void update({ group: "" })}
        >
          清除归类
        </button>
        <button
          disabled={!selected.length}
          onClick={() => void update({ archived: !archived })}
        >
          {archived ? "恢复到资料库" : "归档所选"}
        </button>
      </fieldset>
      {message && <p role="status">{message}</p>}
      {!visible.length && <p>此视图没有会议</p>}
      {visible.map((item) => (
        <div className="management-row" key={item.id}>
          <label>
            <input
              type="checkbox"
              aria-label={`选择 ${item.title}`}
              disabled={
                busy || (selected.length >= 100 && !selected.includes(item.id))
              }
              checked={selected.includes(item.id)}
              onChange={(event) =>
                setSelected(
                  event.target.checked
                    ? [...selected, item.id]
                    : selected.filter((id) => id !== item.id),
                )
              }
            />
          </label>
          <button className="text-btn" onClick={() => onOpen(item.id)}>
            {item.title}
          </button>
          <span>
            {item.created.slice(0, 10)}
            {item.library?.group_name ? ` · ${item.library.group_name}` : ""}
          </span>
        </div>
      ))}
    </Modal>
  );
}
