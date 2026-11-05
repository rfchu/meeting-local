import { useEffect, useState } from "react";
import Modal from "../../components/Modal";
import { api } from "../../api";
import { clock } from "../../utils";
import type { MeetingMinutes, Segment } from "../../types";
import "./onboarding.css";
export interface SampleMeeting {
  title: string;
  segments: Segment[];
  minutes: MeetingMinutes;
  note: { captured_at: number; text: string; scope: string };
}
export default function SampleMeetingDialog({
  onClose,
}: {
  onClose: () => void;
}) {
  const [sample, setSample] = useState<SampleMeeting | null>(null),
    [error, setError] = useState("");
  const [checked, setChecked] = useState<string[]>([]),
    [selected, setSelected] = useState<string[]>([]);
  const [tab, setTab] = useState("transcript");
  useEffect(() => {
    let live = true;
    void api
      .sampleMeeting()
      .then((value) => {
        if (live) setSample(value);
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, []);
  return (
    <Modal
      className="sample-meeting-dialog"
      ariaLabel="样例会议体验"
      onClose={onClose}
    >
      <div className="guide-heading">
        <div>
          <span className="eyebrow">无需录音 · 无需模型</span>
          <h2>体验一次会议整理</h2>
        </div>
        <button onClick={onClose}>关闭</button>
      </div>
      <p className="muted">
        虚构样例，仅供体验；不含录音，不写入会议资料库或跨会议搜索。
      </p>
      {error && <p role="alert">{error}</p>}
      {!sample && !error && <p role="status">正在加载样例…</p>}
      {sample && (
        <>
          <nav className="guide-tabs" aria-label="样例步骤">
            <button
              aria-pressed={tab === "transcript"}
              onClick={() => setTab("transcript")}
            >
              1 原文与简记
            </button>
            <button
              aria-pressed={tab === "minutes"}
              onClick={() => setTab("minutes")}
            >
              2 核对与导出
            </button>
          </nav>
          <h3>{sample.title}</h3>
          {tab === "transcript" ? (
            <>
              <div className="sample-source-list">
                {sample.segments.map((s) => (
                  <article
                    key={s.id}
                    className={selected.includes(s.id) ? "sample-selected" : ""}
                  >
                    <strong>
                      {clock(s.start)} · {s.speaker}
                    </strong>
                    <p>{s.text}</p>
                    <label>
                      <input
                        type="checkbox"
                        checked={checked.includes(s.id)}
                        onChange={(e) =>
                          setChecked((old) =>
                            e.target.checked
                              ? [...old, s.id]
                              : old.filter((id) => id !== s.id),
                          )
                        }
                      />{" "}
                      我已核对这一段
                    </label>
                  </article>
                ))}
              </div>
              <aside className="sample-note">
                <strong>{clock(sample.note.captured_at)} · 个人简记</strong>
                <p>{sample.note.text}</p>
                <small>简记记录你的关注点，是否纳入正式纪要由你决定。</small>
              </aside>
              <p className="muted">
                已核对 {checked.length}/{sample.segments.length}{" "}
                段；这些勾选仅在本次体验中有效。
              </p>
              <button className="primary" onClick={() => setTab("minutes")}>
                查看纪要与待办 →
              </button>
            </>
          ) : (
            <>
              <p className="muted">
                这是预先整理的样例纪要。点击来源可对照原文；不会调用 AI。
              </p>
              {(
                [
                  ["decisions", "已定事项"],
                  ["action_items", "行动项"],
                  ["open_questions", "待确认问题"],
                ] as const
              ).map(([key, label]) => (
                <section className="sample-section" key={key}>
                  <h3>{label}</h3>
                  {sample.minutes[key].map((item, index) => (
                    <article key={index}>
                      <p>{item.content}</p>
                      {item.owner && (
                        <small>
                          负责人：{item.owner} · 期限：
                          {item.due_date || "未明确"}
                        </small>
                      )}
                      <button
                        className="text-btn"
                        onClick={() => {
                          setSelected(item.source_segment_ids);
                          setTab("transcript");
                        }}
                      >
                        对照原文
                      </button>
                    </article>
                  ))}
                </section>
              ))}
              <div className="sample-exports" aria-label="导出样例纪要">
                <strong>导出样例</strong>
                {["md", "docx", "pdf"].map((format) => (
                  <a
                    key={format}
                    href={`/api/onboarding/sample/export?format=${format}`}
                    download
                  >
                    {format === "md"
                      ? "Markdown"
                      : format === "docx"
                        ? "Word"
                        : "PDF"}
                  </a>
                ))}
              </div>
              <p className="muted">
                导出文件也会标注“虚构样例”。正式会议使用同一套排版引擎。
              </p>
            </>
          )}
        </>
      )}
    </Modal>
  );
}
