import { useState } from "react";
import { api } from "../../api";
import type { Meeting, MeetingSummary } from "../../types";
import MeetingBriefForm, { emptyBrief } from "./MeetingBriefForm";
export default function MeetingPreparation({
  meeting,
  meetings,
  onRefresh,
  onOpen,
  toast,
}: {
  meeting: Meeting;
  meetings: MeetingSummary[];
  onRefresh: () => Promise<void>;
  onOpen: (id: string) => void;
  toast: (text: string) => void;
}) {
  const [value, setValue] = useState({
    ...emptyBrief,
    ...meeting.flow?.brief,
    title: meeting.flow?.brief.title || meeting.title,
  });
  const [busy, setBusy] = useState(false),
    [dirty, setDirty] = useState(false);
  if (!meeting.flow)
    return <p>会议准备将在本次录音和处理任务结束、服务更新后启用。</p>;
  return (
    <section className="meeting-preparation">
      <h3>会议准备与背景</h3>
      <p>回顾本次目标和上次未完成事项，会后核对讨论是否形成结论。</p>
      <MeetingBriefForm
        value={value}
        onChange={(v) => {
          setValue(v);
          setDirty(true);
        }}
        meetings={meetings}
        currentId={meeting.id}
        disabled={busy}
      />
      <button
        className="primary"
        disabled={busy || !dirty}
        onClick={() => {
          setBusy(true);
          void api
            .saveBrief(meeting.id, value)
            .then(onRefresh)
            .then(() => {
              setDirty(false);
              toast("会议准备已保存；已生成的 AI 纪要可能需要重新核对。");
            })
            .catch((e) => toast(e.message))
            .finally(() => setBusy(false));
        }}
      >
        保存会议准备
      </button>
      {dirty && <span role="status">有未保存的准备内容</span>}
      {meeting.flow?.previous && (
        <section>
          <h4>上次会议：{meeting.flow.previous.title}</h4>
          <button onClick={() => onOpen(meeting.flow!.previous!.id)}>
            打开上次会议
          </button>
          <p>未完成事项仅供本次讨论参考，不会自动当作本次决定。</p>
          {meeting.flow.previous.todos.length ? (
            meeting.flow.previous.todos.map((t) => (
              <p key={t.id}>
                {t.content} · {t.owner || "负责人待确认"} ·{" "}
                {t.due_date || "期限待确认"}
                {t.stale ? " · 来源变化，需复核" : ""}
              </p>
            ))
          ) : (
            <p>上次会议没有待完成的跟进项。</p>
          )}
        </section>
      )}
    </section>
  );
}
