import { Fragment } from "react";
import { download } from "../../api";
import type {
  Environment,
  Meeting,
  MeetingMinutes,
  MinutesItem,
  MinutesStyle,
} from "../../types";
import {
  clock,
  currentAnalysis,
  currentMinutes,
  latestMinutes,
} from "../../utils";

const layouts: Record<
  MinutesStyle,
  {
    sections: [
      keyof Pick<
        MeetingMinutes,
        | "overview"
        | "conclusions"
        | "decisions"
        | "action_items"
        | "open_questions"
      >,
      string,
    ][];
    hint: string;
  }
> = {
  formal: {
    sections: [
      ["overview", "会议成果"],
      ["decisions", "决策事项"],
      ["action_items", "行动计划"],
      ["open_questions", "风险与待确认"],
    ],
    hint: "适合审核和对外发送，突出结论、决策及责任。",
  },
  brief: {
    sections: [
      ["overview", "会议结果"],
      ["decisions", "关键决策"],
      ["action_items", "行动清单"],
      ["open_questions", "待确认"],
    ],
    hint: "一页式摘要，只保留最关键的结果与下一步。",
  },
  internal: {
    sections: [
      ["overview", "会议摘要"],
      ["conclusions", "讨论要点"],
      ["decisions", "决策与依据"],
      ["action_items", "行动跟踪"],
      ["open_questions", "风险、依赖与待确认"],
    ],
    hint: "保留完整讨论脉络、执行状态和原文依据。",
  },
  ai: {
    sections: [
      ["overview", "核心内容纪要"],
      ["action_items", "待办事项（Action Items）"],
      ["conclusions", "会议总结"],
    ],
    hint: "全文由所选 LLM 直接基于当前会议文字稿整理。",
  },
};

interface Props {
  meeting: Meeting;
  environment?: Environment;
  draft: MeetingMinutes | null;
  dirty: boolean;
  transcriptDirty: boolean;
  style: MinutesStyle;
  mode: "quick" | "full";
  model: string;
  showCitations: boolean;
  generating: boolean;
  onStyle: (value: MinutesStyle) => void;
  onMode: (value: "quick" | "full") => void;
  onModel: (value: string) => void;
  onCitations: (value: boolean) => void;
  onGenerate: () => void;
  onDraft: (value: MeetingMinutes) => void;
  onSave: () => void;
  onPreview: () => void;
  onSeek: (time: number) => void;
}

export default function MinutesView(props: Props) {
  const {
    meeting,
    environment,
    draft,
    dirty,
    transcriptDirty,
    style,
    mode,
    model,
    showCitations,
    generating,
  } = props;
  const analysis = currentAnalysis(meeting),
    artifact = latestMinutes(meeting),
    current = currentMinutes(meeting),
    profiles = environment?.analysis.profiles || [],
    models = environment?.analysis.models || [];
  const notice =
    transcriptDirty && artifact
      ? "文字稿正在修改；保存后当前会议纪要将过期，请重新生成。"
      : artifact && artifact.revision !== meeting.revision
        ? `该纪要基于文字稿 v${artifact.revision}，当前为 v${meeting.revision}，请重新生成纪要。`
        : current &&
            current.payload.style !== "ai" &&
            analysis &&
            current.provenance?.source_analysis_id !== analysis.id
          ? "会议分析已经更新，建议重新生成纪要；当前人工编辑内容仍会保留。"
          : "";
  const update = <K extends keyof MeetingMinutes>(
    key: K,
    value: MeetingMinutes[K],
  ) => draft && props.onDraft({ ...draft, [key]: value });
  const itemUpdate = (
    section: keyof Pick<
      MeetingMinutes,
      | "overview"
      | "conclusions"
      | "decisions"
      | "action_items"
      | "open_questions"
    >,
    index: number,
    patch: Partial<MinutesItem>,
  ) =>
    draft &&
    update(
      section,
      draft[section].map((item, i) =>
        i === index ? { ...item, ...patch } : item,
      ),
    );
  const byId = new Map(meeting.segments.map((item) => [item.id, item]));
  const layout = layouts[draft?.style || style];
  return (
    <div id="minutesView">
      <div className="minutes-toolbar">
        <label>
          纪要样式
          <select
            aria-label="纪要样式"
            value={style}
            onChange={(e) => props.onStyle(e.target.value as MinutesStyle)}
          >
            <option value="formal">正式版 · 对外发送</option>
            <option value="brief">精简版 · 一页摘要</option>
            <option value="internal">内部工作版 · 完整依据</option>
            <option value="ai">AI 会议纪要 · 全文整理</option>
          </select>
        </label>
        {style === "ai" && (
          <label>
            生成方式
            <select
              aria-label="AI 纪要生成方式"
              value={mode}
              onChange={(e) => props.onMode(e.target.value as "quick" | "full")}
            >
              <option value="quick">快速生成 · 直接整理全文</option>
              <option value="full">完整处理 · 先分析再生成</option>
            </select>
          </label>
        )}
        <button
          className="primary"
          disabled={generating || (style === "ai" ? !model : !analysis)}
          onClick={props.onGenerate}
        >
          {generating
            ? "正在生成…"
            : style === "ai"
              ? current?.payload.style === "ai"
                ? "重新生成 AI 会议纪要"
                : "生成 AI 会议纪要"
              : current
                ? "重新生成会议纪要"
                : "生成会议纪要"}
        </button>
        {style === "ai" && (
          <label>
            纪要生成模型
            <select
              aria-label="纪要生成模型"
              value={model}
              onChange={(e) => props.onModel(e.target.value)}
            >
              {profiles.length ? (
                profiles.map((item) => (
                  <option value={item.id} key={item.id}>
                    {item.name} · {item.model}
                  </option>
                ))
              ) : models.length ? (
                models.map((item) => <option key={item}>{item}</option>)
              ) : (
                <option value="">请先配置模型</option>
              )}
            </select>
          </label>
        )}
        <button disabled={!draft || !dirty} onClick={props.onSave}>
          {dirty ? "保存修改" : "已保存"}
        </button>
        <button disabled={!draft} onClick={props.onPreview}>
          导出预览
        </button>
        <label className="minutes-check">
          <input
            type="checkbox"
            checked={showCitations}
            onChange={(e) => props.onCitations(e.target.checked)}
          />{" "}
          显示原文引用
        </label>
        <select
          id="minutesExport"
          aria-label="导出会议纪要"
          value=""
          onChange={(e) => {
            if (e.target.value)
              download(
                `/meetings/${meeting.id}/minutes/export/${e.target.value}?include_citations=${showCitations}`,
              );
          }}
        >
          <option value="">导出纪要 ↓</option>
          <option value="docx">Word 文档</option>
          <option value="pdf">PDF 文档</option>
          <option value="md">Markdown</option>
        </select>
      </div>
      {notice && <div className="analysis-notice">{notice}</div>}
      {!draft ? (
        <div className="empty">
          <div>▤</div>
          <h3>还没有会议纪要</h3>
          <p>
            {analysis
              ? "选择样式后生成适合审核和对外发送的纪要。"
              : "AI 快速模式可直接从文字稿生成；其他样式需要先完成会议分析。"}
          </p>
        </div>
      ) : (
        <div className={`minutes-document minutes-${draft.style}`}>
          <div className="minutes-style-badge">
            {draft.style === "formal"
              ? "正式纪要"
              : draft.style === "brief"
                ? "一页纪要"
                : draft.style === "internal"
                  ? "内部工作纪要"
                  : "AI 会议纪要 · 全文整理"}
          </div>
          <input
            className="minutes-title"
            aria-label="纪要标题"
            value={draft.title}
            onChange={(e) => update("title", e.target.value)}
          />
          <div className="minutes-meta">
            {(
              [
                ["日期", "meeting_date"],
                ["会议形式", "meeting_form"],
                ["地点", "location"],
                ["参会人", "attendees"],
                ["记录人", "recorder"],
              ] as [string, keyof MeetingMinutes][]
            ).map(([label, key]) => (
              <label key={key}>
                <span>{label}</span>
                <input
                  value={String(draft[key] || "")}
                  onChange={(e) => update(key, e.target.value as never)}
                />
              </label>
            ))}
          </div>
          <div className="minutes-purpose">
            <label>
              <span>{draft.style === "ai" ? "会议目的" : "会议目标"}</span>
              <textarea
                rows={2}
                value={draft.purpose}
                onChange={(e) => update("purpose", e.target.value)}
              />
            </label>
          </div>
          {layout.sections.map(([section, label]) => {
            const items = draft[section],
              table =
                draft.style !== "internal" &&
                (section === "decisions" || section === "action_items");
            return (
              <section
                className={`minutes-section minutes-section-${section}`}
                key={section}
              >
                <div className="minutes-section-head">
                  <h3>{label}</h3>
                  <button
                    onClick={() =>
                      update(section, [
                        ...items,
                        {
                          content: "新增事项",
                          source_segment_ids: [],
                          owner: null,
                          due_date: null,
                          status: section === "action_items" ? "待开始" : null,
                        },
                      ])
                    }
                  >
                    ＋ 添加
                  </button>
                </div>
                {!items.length && <p className="minutes-empty">无明确内容</p>}
                <div
                  className={
                    table
                      ? `minutes-grid ${section === "action_items" ? "action-grid" : "decision-grid"}`
                      : ""
                  }
                >
                  {table &&
                    (section === "action_items"
                      ? ["行动项", "负责人", "截止时间", "状态", ""]
                      : ["决策事项", "负责人", "生效/完成时间", ""]
                    ).map((value) => (
                      <b className="minutes-grid-head" key={value}>
                        {value}
                      </b>
                    ))}
                  {items.map((item, index) =>
                    table ? (
                      <Fragment key={index}>
                        <textarea
                          rows={2}
                          value={item.content}
                          onChange={(e) =>
                            itemUpdate(section, index, {
                              content: e.target.value,
                            })
                          }
                        />
                        <input
                          placeholder="待确认"
                          value={item.owner || ""}
                          onChange={(e) =>
                            itemUpdate(section, index, {
                              owner: e.target.value || null,
                            })
                          }
                        />
                        <input
                          placeholder="待确认"
                          value={item.due_date || ""}
                          onChange={(e) =>
                            itemUpdate(section, index, {
                              due_date: e.target.value || null,
                            })
                          }
                        />
                        {section === "action_items" && (
                          <select
                            value={item.status || "待开始"}
                            onChange={(e) =>
                              itemUpdate(section, index, {
                                status: e.target.value,
                              })
                            }
                          >
                            {["待开始", "进行中", "已完成", "已延期"].map(
                              (value) => (
                                <option key={value}>{value}</option>
                              ),
                            )}
                          </select>
                        )}
                        <button
                          onClick={() =>
                            update(
                              section,
                              items.filter((_, i) => i !== index),
                            )
                          }
                        >
                          删除
                        </button>
                      </Fragment>
                    ) : (
                      <article className="minutes-item" key={index}>
                        <textarea
                          rows={Math.max(
                            2,
                            Math.ceil(item.content.length / 45),
                          )}
                          value={item.content}
                          onChange={(e) =>
                            itemUpdate(section, index, {
                              content: e.target.value,
                            })
                          }
                        />
                        <div className="minutes-item-tools">
                          {(section === "action_items" ||
                            section === "decisions") && (
                            <>
                              <input
                                placeholder="负责人待确认"
                                value={item.owner || ""}
                                onChange={(e) =>
                                  itemUpdate(section, index, {
                                    owner: e.target.value || null,
                                  })
                                }
                              />
                              <input
                                placeholder="时间待确认"
                                value={item.due_date || ""}
                                onChange={(e) =>
                                  itemUpdate(section, index, {
                                    due_date: e.target.value || null,
                                  })
                                }
                              />
                            </>
                          )}
                          <button
                            onClick={() =>
                              update(
                                section,
                                items.filter((_, i) => i !== index),
                              )
                            }
                          >
                            删除
                          </button>
                        </div>
                        {showCitations && (
                          <div className="citations">
                            {item.source_segment_ids
                              .map((id) => byId.get(id))
                              .filter(Boolean)
                              .map((segment) => (
                                <button
                                  key={segment!.id}
                                  className="citation"
                                  title={`${segment!.speaker}：${segment!.text}`}
                                  onClick={() => props.onSeek(segment!.start)}
                                >
                                  {clock(segment!.start)}
                                </button>
                              ))}
                          </div>
                        )}
                      </article>
                    ),
                  )}
                </div>
              </section>
            );
          })}
          <p className="minutes-reference-note">
            {layout.hint} 发送前请确认参会人、负责人和时间。
          </p>
        </div>
      )}
    </div>
  );
}

export { layouts };
