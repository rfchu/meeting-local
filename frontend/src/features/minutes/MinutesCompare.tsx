import Modal from "../../components/Modal";
import type { Artifact, MeetingMinutes } from "../../types";
export default function MinutesCompare({
  left,
  right,
  onClose,
  onAdopt,
}: {
  left: Artifact<MeetingMinutes>;
  right: Artifact<MeetingMinutes>;
  onClose: () => void;
  onAdopt: () => void;
}) {
  const labels = {
    overview: "会议概述",
    conclusions: "核心讨论",
    decisions: "决策",
    action_items: "待办",
    open_questions: "风险与待确认",
    summary: "会议总结",
  } as const;
  return (
    <Modal id="minutesCompareDialog" onClose={onClose}>
      <div className="compare-header">
        <h2>对比纪要版本</h2>
        <button onClick={onClose}>关闭</button>
      </div>
      <p>
        两个版本独立保存。采用右侧版本后，会议成果和跟进以它为准；原版本仍可查看。
      </p>
      <div className="minutes-compare-grid">
        {[left, right].map((artifact, index) => {
          const original = artifact.payload;
          const document =
            original.style === "ai" && (original.template_version || 1) < 2
              ? {
                  ...original,
                  overview: [],
                  conclusions: original.overview,
                  summary: original.conclusions,
                }
              : original;
          return (
            <section key={artifact.id}>
              <h3>
                {index === 0 ? "原版本" : "待采用版本"} ·{" "}
                {artifact.payload.title}
              </h3>
              <p>
                {document.meeting_date} {document.meeting_form}{" "}
                {document.attendees}
              </p>
              {document.purpose && <p>会议目的：{document.purpose}</p>}
              {Object.entries(labels).map(([key, label]) => (
                <div key={key}>
                  <h4>{label}</h4>
                  {(document[key as keyof typeof labels] || []).map(
                    (item, i) => (
                      <p key={i}>
                        {item.content}
                        {item.owner ? ` · ${item.owner}` : ""}
                        {item.due_date ? ` · ${item.due_date}` : ""}
                        {item.status ? ` · ${item.status}` : ""}
                      </p>
                    ),
                  )}
                </div>
              ))}
            </section>
          );
        })}
      </div>
      <button className="primary" disabled={right.stale} onClick={onAdopt}>
        采用右侧版本
      </button>
    </Modal>
  );
}
