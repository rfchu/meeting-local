import InterviewMinutesBody from "./InterviewMinutesBody";
import {
  hasMinutesFollowup,
  pendingMinutesDrafts,
  minutesVersionDescription,
} from "./presentation";
import type { MinutesOptions } from "./composition";
import { Fragment } from "react";
import { download } from "../../api";
import type {
  Artifact,
  Environment,
  Meeting,
  MeetingMinutes,
  MinutesItem,
  MinutesTemplate,
  MinutesTemplateSection,
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
        | "summary"
        | "summary"
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
  options: MinutesOptions;
  templates: MinutesTemplate[];
  withAnalysis: boolean;
  selectedId: string | null;
  model: string;
  showCitations: boolean;
  referenceMode?: boolean;
  generating: boolean;
  onOptions: (value: MinutesOptions) => void;
  onWithAnalysis: (value: boolean) => void;
  onPreparation: () => void;
  onSelect: (id: string) => void;
  onAdopt: () => void;
  onCompare: (left: string, right: string) => void;
  onExport: (format: "docx" | "pdf" | "md") => void;
  onModel: (value: string) => void;
  onCitations: (value: boolean) => void;
  onGenerate: () => void;
  onDraft: (value: MeetingMinutes) => void;
  onSave: () => void;
  onJobAction: (id: string, action: "cancel" | "retry") => void;
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
    options,
    templates,
    withAnalysis,
    selectedId,
    model,
    showCitations,
    generating: locallyGenerating,
  } = props;
  const generation = meeting.minutes_generation;
  const automaticJob = meeting.jobs.find((job) => job.kind === "minutes");
  const automaticBusy =
    automaticJob && ["queued", "running"].includes(automaticJob.state);
  const analysisFollowup = meeting.jobs.find(
    (job) =>
      ["queued", "running"].includes(job.state) &&
      job.kind === "analysis" &&
      hasMinutesFollowup(job.config),
  );
  const generating =
    locallyGenerating ||
    generation?.state === "running" ||
    generation?.state === "waiting" ||
    !!analysisFollowup ||
    !!automaticBusy;
  const versions = meeting.artifacts.filter(
    (a) => a.kind === "meeting_minutes",
  ) as Artifact<MeetingMinutes>[];
  const artifact = versions.find((a) => a.id === selectedId);
  const current =
    artifact && !artifact.stale && artifact.revision === meeting.revision
      ? artifact
      : undefined;
  const readOnly = !!artifact && !current;
  const coverage = (artifact?.provenance.chinese_record as { counts?: { original: number; translated: number; pending: number; unknown: number } } | undefined)?.counts;
  const generatedOptions = artifact?.provenance.composition as
    MinutesOptions | undefined;
  const analysis = currentAnalysis(meeting),
    profiles = environment?.analysis.profiles || [],
    models = environment?.analysis.models || [];
  const templateId = options.template_id,
    style = draft?.style || "ai";
  const previousFollowup = meeting.jobs.find(
    (job) => job.kind === "analysis" && hasMinutesFollowup(job.config),
  );
  const newDraft = pendingMinutesDrafts(meeting).find(
    (a) => a.id !== selectedId,
  );
  const notice =
    transcriptDirty && artifact
      ? "文字稿正在修改；保存后当前会议纪要将过期，请重新生成。"
      : artifact && artifact.revision !== meeting.revision
        ? `该纪要基于文字稿 v${artifact.revision}，当前为 v${meeting.revision}，请重新生成纪要。`
        : artifact?.stale
          ? "会议准备、关注点或原文依据已变化，此纪要需重新核对或生成。"
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
      | "summary"
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
  const byId = new Map(
    artifact?.revision === meeting.revision && !transcriptDirty
      ? meeting.segments.map((item) => [item.id, item])
      : [],
  );
  const translations = artifact?.provenance.citation_translations as Record<string, string> | undefined;
  const itemSources = (item: MinutesItem, required: boolean) => {
    if (!required && !showCitations && !props.referenceMode) return null;
    const ids = [...new Set(item.source_segment_ids)];
    const missing = ids.filter((sid) => !byId.has(sid));
    return (
      <div className="citations minutes-table-citations">
        {(!ids.length || missing.length > 0) && (
          <small className="muted">{!ids.length ? "原文依据待核对" : "部分或全部原文依据待核对"}</small>
        )}
        {ids.map((sid) => {
          const segment = byId.get(sid);
          if (!segment) return null;
          return (
            <details key={sid}>
              <summary>原文依据 {clock(segment.start)}</summary>
              <p>{segment.speaker}：{segment.text}</p>
              {translations?.[sid] && <p>生成时的中文译文：{translations[sid]}</p>}
              <button className="citation" onClick={() => props.onSeek(segment.start)}>
                回听原话 {clock(segment.start)}
              </button>
            </details>
          );
        })}
      </div>
    );
  };
  const selectedTemplate = templates.find((item) => item.id === templateId);
  const template = draft
    ? draft.template_snapshot || undefined
    : selectedTemplate;
  const fallback = layouts[draft?.style || style];
  const layoutSections: MinutesTemplateSection[] = template
    ? template.sections
    : fallback.sections.map(([source, title]) => ({
        id: source,
        title,
        source,
        kind:
          source === "action_items"
            ? "action_table"
            : source === "decisions"
              ? "decision_table"
              : "list",
        required: false,
        evidence_required: true,
        max_items: 100,
      }));
  return (
    <div id="minutesView">
      {automaticJob && automaticJob.state !== "completed" && (
        <div
          className="analysis-notice"
          role={automaticJob.state === "failed" ? "alert" : "status"}
        >
          纪要任务：{automaticJob.stage}
          {automaticJob.error ? ` · ${automaticJob.error}` : ""}
          <button
            onClick={() =>
              props.onJobAction(
                automaticJob.id,
                automaticBusy ? "cancel" : "retry",
              )
            }
          >
            {automaticBusy ? "取消纪要任务" : "重试纪要任务"}
          </button>
          <small>文字稿仍保留；来源已变化时请用当前文字稿重新生成。</small>
        </div>
      )}
      {generation?.state === "waiting" && !automaticBusy && (
        <p role="status">
          {generation.message ||
            "纪要任务等待继续；已有纪要和检查点保留，可在运行管理中取消。"}
        </p>
      )}
      {generation?.state === "running" && (
        <p role="status">
          AI 纪要正在整理文字稿，已等待 {generation.elapsed_seconds}{" "}
          秒。完整结果返回后会自动保存；刷新页面可继续查看状态。
        </p>
      )}
      {generation?.state === "failed" && (
        <p role="alert" className="error">
          上次 AI 纪要生成失败：{generation.error}
        </p>
      )}
      {previousFollowup &&
        ["failed", "cancelled"].includes(previousFollowup.state) && (
          <p role="alert" className="error">
            上次根据分析成稿
            {previousFollowup.state === "cancelled" ? "已取消" : "失败"}：
            {previousFollowup.error || previousFollowup.stage}
            。可点击生成按钮重新处理。
          </p>
        )}
      {analysisFollowup && (
        <p role="status">
          会议分析后将自动生成纪要草稿：{analysisFollowup.stage}
          。刷新页面仍可继续查看。
        </p>
      )}
      <details
        open={!draft}
        className="minutes-generation-settings"
        aria-label="生成纪要设置"
      >
        <summary>
          {draft ? "生成新草稿" : "生成会议纪要"} ·{" "}
          {selectedTemplate?.name || "选择模板"}
        </summary>
        <div className="minutes-toolbar">
          <label>
            会议模板
            <select
              aria-label="纪要模板"
              value={options.template_id}
              onChange={(e) => {
                props.onOptions({
                  ...options,
                  template_id: e.target.value,
                  ...(e.target.value === "online-interview"
                    ? { method: "ai", audience: "internal" }
                    : {}),
                });
                if (e.target.value === "online-interview")
                  props.onWithAnalysis(false);
              }}
            >
              {templates.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
          {options.method !== "analysis" && (
            <label>
              纪要输出语言
              <select
                aria-label="纪要输出语言"
                disabled={options.method === "current"}
                value={
                  options.output_language ||
                  meeting.flow?.brief.output_language ||
                  "zh"
                }
                onChange={(event) =>
                  props.onOptions({
                    ...options,
                    output_language: event.target.value as "zh" | "en",
                  })
                }
              >
                <option value="zh">中文</option>
                <option value="en">英文</option>
              </select>
            </label>
          )}
          {options.method === "analysis" && (
            <p className="muted">
              沿用会议分析的文字；需要转换语言时选择 AI 整理。
            </p>
          )}
          <label>
            内容详略
            <select
              aria-label="内容详略"
              disabled={options.method === "analysis"}
              value={options.detail_level}
              onChange={(e) =>
                props.onOptions({
                  ...options,
                  detail_level: e.target
                    .value as MinutesOptions["detail_level"],
                })
              }
            >
              <option value="brief">精简</option>
              <option value="standard">标准</option>
              <option value="detailed">详细</option>
            </select>
          </label>
          <label>
            发送用途
            <select
              aria-label="发送用途"
              disabled={options.method === "analysis"}
              value={options.audience}
              onChange={(e) =>
                props.onOptions({
                  ...options,
                  audience: e.target.value as MinutesOptions["audience"],
                })
              }
            >
              <option value="external">对外发送</option>
              <option value="internal">内部工作</option>
            </select>
          </label>
          <button
            className="primary"
            disabled={
              generating ||
              !meeting.segments.length ||
              ((options.method !== "analysis" || !analysis) && !model) ||
              (options.method === "current" && !current)
            }
            onClick={props.onGenerate}
          >
            {generating
              ? "正在生成…"
              : options.method === "analysis" && !analysis
                ? "分析并生成纪要"
                : draft
                  ? "生成新草稿"
                  : "生成会议纪要"}
          </button>
        </div>
        {options.template_id === "online-interview" && (
          <p>
            <button onClick={props.onPreparation}>填写面试准备</button>
            可选填岗位与考察重点；本模板仅使用 AI 整理全文。
          </p>
        )}
        <p>生成后另存草稿，核对后采用。</p>
        <details className="minutes-advanced">
          <summary>
            高级设置 ·{" "}
            {options.method === "ai"
              ? "AI 整理全文"
              : options.method === "analysis"
                ? "根据分析成稿"
                : "从当前纪要派生"}{" "}
            ·{" "}
            {profiles.find((p) => p.id === model)?.name ||
              model ||
              "尚未配置模型"}
          </summary>
          <label>
            生成方法
            <select
              aria-label="纪要生成方法"
              value={options.method}
              onChange={(e) =>
                props.onOptions({
                  ...options,
                  method: e.target.value as MinutesOptions["method"],
                  ...(e.target.value === "current"
                    ? { output_language: draft?.output_language || "zh" }
                    : {}),
                })
              }
            >
              <option value="ai">AI 整理全文（推荐）</option>
              <option
                value="analysis"
                disabled={options.template_id === "online-interview"}
              >
                根据会议分析快速成稿
              </option>
              <option
                value="current"
                disabled={
                  !current || options.template_id === "online-interview"
                }
              >
                基于当前显示的纪要整理
              </option>
            </select>
          </label>
          <label>
            使用模型
            <select
              aria-label="纪要生成模型"
              value={model}
              onChange={(e) => props.onModel(e.target.value)}
            >
              <option value="">请选择模型配置</option>
              {profiles.length ? (
                profiles.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} · {p.provider_type === "cloud" ? "云端" : "本地"}
                  </option>
                ))
              ) : models.length ? (
                models.map((m) => <option key={m}>{m}</option>)
              ) : (
                <option value="">请先配置模型</option>
              )}
            </select>
          </label>
          {options.method === "ai" &&
            options.template_id !== "online-interview" && (
              <label>
                <input
                  type="checkbox"
                  checked={withAnalysis}
                  onChange={(e) => props.onWithAnalysis(e.target.checked)}
                />
                同时生成会议分析，作为独立复盘资料
              </label>
            )}
          <p>
            {options.method === "analysis"
              ? analysis
                ? "将复用当前文字稿的分析，不再次调用模型。只按模板编排，详略和用途设置仅对 AI 整理生效；可成稿后再从本稿整理精简版。"
                : "尚无当前版本分析。点击主按钮后先分析，再自动保存纪要草稿，无需跳转。"
              : options.method === "current"
                ? "以当前纪要为唯一内容范围整理叙述，保留全部决策、待办、负责人、期限和人工补充；篇幅过长时允许多页。"
                : "使用完整文字稿、会议准备和已核对关注点；个人保留或暂不处理的简记不进入模型。"}
          </p>
          <p>
            云端模型会接收本次整理资料，操作前会确认。对外发送前仍需人工核对事实及个人信息。
          </p>
        </details>
      </details>
      {newDraft && (
        <div className="analysis-notice">
          新草稿已保存，当前采用稿未改变。
          <button onClick={() => props.onSelect(newDraft.id)}>
            查看新草稿
          </button>
          {artifact && (
            <button onClick={() => props.onCompare(artifact.id, newDraft.id)}>
              对比版本
            </button>
          )}
        </div>
      )}
      <details className="minutes-version-options">
        <summary>版本管理与草稿导出{dirty ? " · 正在保存修改" : ""}</summary>
        <div className="minutes-toolbar minutes-version-toolbar">
          <label>
            当前显示版本
            <select
              aria-label="纪要版本"
              value={selectedId || ""}
              onChange={(e) => props.onSelect(e.target.value)}
            >
              {!selectedId && <option value="">尚未选择</option>}
              {versions.map((a, i) => (
                <option key={a.id} value={a.id}>
                  版本 {versions.length - i} ·{" "}
                  {a.id === meeting.active_minutes_id
                    ? "当前采用"
                    : "历史／草稿"}{" "}
                  · {minutesVersionDescription(a)} · 文字稿 v{a.revision}
                  {a.stale ? " · 已过期" : ""}
                </option>
              ))}
            </select>
          </label>
          {artifact && artifact.id !== meeting.active_minutes_id && (
            <button
              className="primary"
              disabled={!current || generating || transcriptDirty}
              onClick={props.onAdopt}
            >
              采用此版本
            </button>
          )}
          <button
            disabled={!draft || !dirty || readOnly}
            onClick={props.onSave}
          >
            {dirty ? "保存为新版本" : "已保存"}
          </button>
          {artifact &&
            meeting.active_minutes_id &&
            artifact.id !== meeting.active_minutes_id && (
              <button
                onClick={() =>
                  props.onCompare(meeting.active_minutes_id!, artifact.id)
                }
              >
                与采用稿对比
              </button>
            )}
          <button
            disabled={
              !current || dirty || transcriptDirty || !!draft?.interview
            }
            onClick={() => {
              if (draft) {
                props.onOptions({
                  ...options,
                  method: "current",
                  detail_level: "brief",
                });
              }
            }}
          >
            从本稿整理精简版
          </button>
          <button disabled={!draft} onClick={props.onPreview}>
            导出预览
          </button>
          <label>
            <input
              type="checkbox"
              checked={showCitations}
              onChange={(e) => props.onCitations(e.target.checked)}
            />
            {props.referenceMode ? "导出包含原文引用" : "显示原文引用"}
          </label>
          <select
            aria-label="导出会议纪要"
            value=""
            disabled={!draft}
            onChange={(e) => {
              if (e.target.value)
                props.onExport(e.target.value as "docx" | "pdf" | "md");
            }}
          >
            <option value="">导出当前显示版本 ↓</option>
            <option value="docx">Word 文档</option>
            <option value="pdf">PDF 文档</option>
            <option value="md">Markdown</option>
          </select>
        </div>
      </details>
      {coverage && (
        <p className="muted">
          生成时的中文记录：中文原文 {coverage.original} 段 · 译文 {coverage.translated} 段 ·
          待翻译 {coverage.pending} 段 · 语言待确认 {coverage.unknown} 段。
          {coverage.pending + coverage.unknown > 0 && "缺少译文的片段使用原文，请核对。"}
        </p>
      )}
      {readOnly && (
        <p className="analysis-notice">
          这个版本的来源已变化，仅供查看和导出；请根据当前原文生成新草稿，旧稿仍保留。
        </p>
      )}
      {notice && <div className="analysis-notice">{notice}</div>}
      {!draft ? (
        <div className="empty">
          <div>▤</div>
          <h3>还没有会议纪要</h3>
          <p>
            选择模板后点击“生成会议纪要”。默认直接整理全文，无需先生成分析。
          </p>
        </div>
      ) : (
        <fieldset
          disabled={readOnly}
          className={`minutes-document minutes-${draft.style}`}
        >
          <div className="minutes-style-badge">
            {template?.name ||
              (draft.style === "formal"
                ? "正式纪要"
                : draft.style === "brief"
                  ? "一页纪要"
                  : draft.style === "internal"
                    ? "内部工作纪要"
                    : "AI 会议纪要 · 全文整理")}
            {generatedOptions && (
              <span>
                {" "}
                ·{" "}
                {generatedOptions.method === "analysis"
                  ? "规则编排"
                  : generatedOptions.detail_level === "brief"
                    ? "精简"
                    : generatedOptions.detail_level === "detailed"
                      ? "详细"
                      : "标准"}
              </span>
            )}
          </div>
          <input
            className="minutes-title"
            aria-label="纪要标题"
            value={draft.title}
            onChange={(e) => update("title", e.target.value)}
          />
          <details className="minutes-basic-info">
            <summary>
              {draft.interview ? "面试基本信息" : "会议基本信息"}
              {draft.meeting_date ? ` · ${draft.meeting_date}` : " · 待补充"}
            </summary>
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
          </details>
          {draft.interview && (
            <InterviewMinutesBody
              document={draft}
              onPreparation={props.onPreparation}
              segments={Array.from(byId.values())}
              translations={translations}
              onChange={props.onDraft}
              onSeek={props.onSeek}
            />
          )}
          {!draft.interview &&
            layoutSections.map(
              ({ id, source: section, title: label, kind }) => {
                const items = draft[section],
                  table = kind === "decision_table" || kind === "action_table";
                return (
                  <section
                    className={`minutes-section minutes-section-${section}`}
                    key={id}
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
                              status:
                                section === "action_items" ? "待开始" : null,
                            },
                          ])
                        }
                      >
                        ＋ 添加
                      </button>
                    </div>
                    {!items.length && (
                      <p className="minutes-empty">无明确内容</p>
                    )}
                    <div
                      className={
                        table
                          ? `minutes-grid ${section === "action_items" ? "action-grid" : "decision-grid"}`
                          : ""
                      }
                    >
                      {table &&
                        (section === "action_items"
                          ? ["行动项", "负责人", "截止时间", "会议记录状态", ""]
                          : ["决策事项", "负责人", "生效/完成时间", ""]
                        ).map((value) => (
                          <b className="minutes-grid-head" key={value}>
                            {value}
                          </b>
                        ))}
                      {items.map((item, index) =>
                        table ? (
                          <Fragment key={index}>
                            {section === "action_items" &&
                              meeting.action_tasks?.[selectedId || ""]?.[
                                String(index)
                              ] && (
                                <div className="minutes-table-citations task-live-badge">
                                  当前跟进：
                                  {meeting.note_todos.find(
                                    (t) =>
                                      t.id ===
                                      meeting.action_tasks?.[
                                        selectedId || ""
                                      ]?.[String(index)],
                                  )?.status === "done"
                                    ? "已完成"
                                    : "待完成"}{" "}
                                  · 在下方跟进清单更新
                                </div>
                              )}
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
                                disabled={
                                  !!meeting.action_tasks?.[selectedId || ""]?.[
                                    String(index)
                                  ]
                                }
                                title="会议记录中的状态；当前执行进度在跟进清单更新"
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
                            {itemSources(item, section === "decisions" || section === "action_items")}
                          </Fragment>
                        ) : (
                          <article className="minutes-item" key={index}>
                            {section === "action_items" &&
                              meeting.action_tasks?.[selectedId || ""]?.[
                                String(index)
                              ] && (
                                <small className="task-live-badge">
                                  当前跟进：
                                  {meeting.note_todos.find(
                                    (t) =>
                                      t.id ===
                                      meeting.action_tasks?.[
                                        selectedId || ""
                                      ]?.[String(index)],
                                  )?.status === "done"
                                    ? "已完成"
                                    : "待完成"}{" "}
                                  · 在下方跟进清单更新
                                </small>
                              )}
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
                            {itemSources(item, section === "decisions" || section === "action_items")}
                          </article>
                        ),
                      )}
                    </div>
                  </section>
                );
              },
            )}
          <p className="minutes-reference-note">
            {template?.description || fallback.hint}{" "}
            {draft.interview
              ? "导出前核对问答归属、依据和待追问事项。"
              : "发送前请确认参会人、负责人和时间。"}
          </p>
        </fieldset>
      )}
    </div>
  );
}

export { layouts };
