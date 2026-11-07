import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../api";
import TermDialog from "../terminology/TermDialog";
import Modal from "../../components/Modal";
import type { LLMConfig } from "../../types";
import { clock } from "../../utils";
import { useModelChoice } from "../../preferences/useModelChoice";
import type {
  OverviewItem,
  OverviewStatus,
  OverviewEvidence,
  OverviewTerm,
} from "./types";
import "./overview.css";
const labels = {
  topic: "当前议题",
  decision: "会议结论",
  action: "下一步",
  point: "关键观点",
  question: "待确认问题",
};
export default function LiveOverviewPanel({
  recordingId,
  focusItemId,
  engine = "qwen",
  active = false,
  profiles = [],
  onModels,
  onLocate,
  locateLabel = "查看原文",
  timeline = false,
  onVersion,
}: {
  recordingId: string;
  focusItemId?: string;
  engine?: "qwen" | "whisper";
  active?: boolean;
  profiles?: LLMConfig[];
  onModels?: () => void;
  onLocate?: (e: OverviewEvidence) => void;
  locateLabel?: string;
  timeline?: boolean;
  onVersion?: (version: number) => void;
}) {
  const [data, setData] = useState<OverviewStatus | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const [setup, setSetup] = useState(false),
    [profileId, setProfileId] = useModelChoice("overview");
  const [version, setVersion] = useState(0),
    [expanded, setExpanded] = useState<string | null>(null),
    [showIgnored, setShowIgnored] = useState(false);
  const [editing, setEditing] = useState<OverviewItem | null>(null),
    [content, setContent] = useState("");
  const [section, setSection] = useState<"agenda" | "outcomes" | "terms">(
    "agenda",
  );
  const [openTopics, setOpenTopics] = useState<Record<string, boolean>>({});
  const [termDialog, setTermDialog] = useState<OverviewTerm | null | undefined>(
    undefined,
  );
  const [detailsOpen, setDetailsOpen] = useState(false);
  const seq = useRef(0);
  const view = useRef("");
  const viewKey = JSON.stringify([recordingId, version]);
  view.current = viewKey;
  const load = useCallback(async () => {
    if (!recordingId) return;
    const token = ++seq.current;
    try {
      const value = await api.liveOverview(recordingId, version || undefined);
      if (token === seq.current && view.current === viewKey) {
        setData(value);
        setError("");
      }
    } catch (e) {
      if (token === seq.current && view.current === viewKey)
        setError((e as Error).message);
    }
  }, [recordingId, version, viewKey]);
  useEffect(() => {
    setData(null);
    setError("");
    void load();
    const timer = window.setInterval(() => void load(), 5000);
    return () => {
      seq.current++;
      window.clearInterval(timer);
    };
  }, [load]);
  useEffect(() => {
    setVersion(0);
    setSection("agenda");
    setOpenTopics({});
    setTermDialog(undefined);
    setEditing(null);
    setExpanded(null);
  }, [recordingId]);
  const locatedItem = useRef("");
  useEffect(() => {
    if (!focusItemId || !data || locatedItem.current === focusItemId) return;
    setExpanded(focusItemId);
    setShowIgnored(true);
    const timer = window.setTimeout(() => {
      const item = Array.from(
        document.querySelectorAll<HTMLElement>("[data-overview-item]"),
      ).find((el) => el.dataset.overviewItem === focusItemId);
      if (item) {
        item.scrollIntoView({ block: "center" });
        locatedItem.current = focusItemId;
      }
    }, 0);
    return () => window.clearTimeout(timer);
  }, [focusItemId, data]);
  const run = async (action: () => Promise<OverviewStatus>) => {
    setBusy(true);
    setError("");
    try {
      const result = await action();
      if (view.current !== viewKey) return false;
      seq.current++;
      setData(result);
      return true;
    } catch (e) {
      if (view.current === viewKey) setError((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  };
  const enable = () => {
    const profile = profiles.find((p) => p.id === profileId);
    if (!profile) {
      setError("请选择模型配置");
      return;
    }
    const cloud = profile.provider_type === "cloud";
    const sameConsent =
      data?.consent_scope === "live-overview-v1" &&
      data.provider?.id === profile.id &&
      data.provider.base_url === profile.base_url &&
      data.provider.model === profile.model &&
      data.provider.provider_type === profile.provider_type &&
      data.provider.engine === engine;
    if (
      cloud &&
      !sameConsent &&
      !window.confirm(
        `开启本场持续概览后，将陆续发送已确认文字、会议准备、已确认术语以及明确选入的简记至 ${profile.name}（${profile.base_url}，${profile.model}）。未选入的个人备注不会发送。是否同意？`,
      )
    )
      return;
    void run(() =>
      api.enableOverview(recordingId, {
        profile_id: profile.id,
        engine,
        confirm_cloud: cloud,
        expected_provider: {
          id: profile.id,
          provider_type: profile.provider_type,
          base_url: profile.base_url,
          model: profile.model,
        },
      }),
    );
    setSetup(false);
  };
  const decide = (
    item: OverviewItem,
    state: OverviewItem["decision"],
    text?: string,
  ) =>
    run(() =>
      api.reviewOverview(recordingId, item.id, {
        state,
        fingerprint: item.fingerprint,
        version: item.review_version,
        ...(text === undefined ? {} : { content: text }),
      }),
    );
  useEffect(() => {
    onVersion?.(data?.latest_version ?? data?.version ?? 0);
  }, [data?.latest_version, data?.version, onVersion]);
  const historical = !!version,
    items = (data?.items || []).filter(
      (i) => showIgnored || i.decision !== "dismissed",
    );
  const agenda = (data?.agenda || []).filter(
    (item) => showIgnored || item.decision !== "dismissed",
  );
  const latestEvidence = (item: OverviewItem) =>
    Math.max(...item.evidence.map((entry) => entry.start));
  const topics = agenda
    .filter((item) => item.kind === "topic")
    .sort(
      (a, b) =>
        (a.topic_start ?? latestEvidence(a)) -
        (b.topic_start ?? latestEvidence(b)),
    );
  const currentTopic = topics
    .filter((item) => !item.stale && item.decision !== "dismissed")
    .reduce<OverviewItem | undefined>(
      (current, item) =>
        !current || latestEvidence(item) >= latestEvidence(current)
          ? item
          : current,
      undefined,
    );
  const topicIds = new Set(topics.map((topic) => topic.id));
  const entries = [
    ...new Map([...items, ...agenda].map((item) => [item.id, item])).values(),
  ].filter((item) => item.kind !== "topic");
  const outcomes = entries
    .filter((item) => ["decision", "action", "question"].includes(item.kind))
    .sort((a, b) => latestEvidence(b) - latestEvidence(a));
  const digest = ["decision", "question"].flatMap(
    (kind) =>
      outcomes.find(
        (item) =>
          item.kind === kind && !item.stale && item.decision !== "dismissed",
      ) || [],
  );
  const unassigned = entries.filter(
    (item) => !item.topic_ref || !topicIds.has(item.topic_ref),
  );
  const agendaView = timeline && !historical && !!topics.length;
  const renderItem = (item: OverviewItem) => {
    const evidence = item.evidence.reduce<OverviewEvidence | undefined>(
      (latest, entry) =>
        !(entry.stale ?? item.stale) && (!latest || entry.start > latest.start)
          ? entry
          : latest,
      undefined,
    );
    return (
      <article
        key={item.id}
        data-overview-item={item.id}
        className={`overview-item ${item.decision}${item.stale ? " stale" : ""}`}
      >
        <div className="overview-item-main">
          {timeline && item.kind !== "topic" && (
            <span className="overview-kind">{labels[item.kind]}</span>
          )}
          <p>{item.content}</p>
          {onLocate && evidence && (
            <button
              className="text-btn overview-locate"
              aria-label={`${locateLabel} ${clock(evidence.start)}`}
              onClick={() => onLocate(evidence)}
            >
              {clock(evidence.start)} {timeline ? "›" : "· " + locateLabel}
            </button>
          )}
          <button
            className="overview-review-toggle text-btn"
            aria-label="查看依据与核对"
            aria-expanded={expanded === item.id}
            onClick={() => setExpanded(expanded === item.id ? null : item.id)}
          >
            ···
          </button>
        </div>
        {timeline && item.previous_decision && (
          <small>
            已替代 · {item.previous_decision.content}
            {onLocate &&
              item.previous_decision.evidence.map((entry) => (
                <button
                  className="text-btn"
                  key={entry.segment_id + entry.quote}
                  onClick={() => onLocate(entry)}
                >
                  {clock(entry.start)} ›
                </button>
              ))}
          </small>
        )}
        {(item.stale || item.decision !== "pending") && (
          <small>
            {item.stale
              ? item.stale_reasons?.length === 1
                ? item.stale_reasons[0] === "source"
                  ? "引用原文已变化，需重新核对"
                  : "关联简记已变化，需重新核对"
                : "依据或关联简记已变化，需重新核对"
              : item.decision === "confirmed"
                ? "已确认 · 不随 AI 刷新覆盖"
                : item.decision === "dismissed"
                  ? "已忽略"
                  : "AI 草稿 · 待核对"}
            {item.note_ids.length
              ? ` · 关联 ${item.note_ids.length} 条简记`
              : ""}
          </small>
        )}
        {expanded === item.id && (
          <div className="overview-item-actions">
            {!historical && (
              <>
                {item.decision !== "confirmed" && (
                  <button
                    disabled={busy || item.stale}
                    onClick={() => void decide(item, "confirmed")}
                  >
                    确认
                  </button>
                )}
                <button
                  disabled={busy || item.stale}
                  onClick={() => {
                    setEditing(item);
                    setContent(item.content);
                  }}
                >
                  编辑确认
                </button>
                {item.decision === "confirmed" ? (
                  <button
                    disabled={busy}
                    onClick={() => void decide(item, "pending")}
                  >
                    撤回确认
                  </button>
                ) : item.decision !== "dismissed" ? (
                  <button
                    disabled={busy}
                    onClick={() => void decide(item, "dismissed")}
                  >
                    忽略
                  </button>
                ) : (
                  <button
                    disabled={busy}
                    onClick={() => void decide(item, "pending")}
                  >
                    重新考虑
                  </button>
                )}
              </>
            )}
          </div>
        )}
        {expanded === item.id && (
          <div className="overview-evidence">
            <small>
              {item.note_ids.length
                ? `关联 ${item.note_ids.length} 条简记 · `
                : ""}
              以下为原文依据
            </small>
            {item.review_trace && (
              <p>
                最近人工核对 ·{" "}
                {
                  {
                    confirmed: "已确认",
                    dismissed: "已忽略",
                    pending: "已恢复待核对",
                  }[item.review_trace.state]
                }
                {item.review_trace.snapshot_version
                  ? ` · 基于 v${item.review_trace.snapshot_version}`
                  : ""}
                {" · "}
                {item.review_trace.updated}
              </p>
            )}
            {item.evidence.map((e, index) => (
              <blockquote key={index}>
                <p>{e.quote}</p>
                {onLocate && (
                  <button
                    className="text-btn"
                    disabled={e.stale ?? item.stale}
                    onClick={() => onLocate(e)}
                  >
                    {clock(e.start)} {locateLabel}
                  </button>
                )}
              </blockquote>
            ))}
          </div>
        )}
      </article>
    );
  };
  return (
    <section
      className={
        timeline
          ? "live-overview-panel overview-timeline"
          : "live-overview-panel"
      }
      aria-label="AI 会中概览"
    >
      <header className="overview-heading">
        <div>
          <h3>AI 会中概览</h3>
          {!timeline && <small>AI 整理 · 会中参考</small>}
        </div>
        <div className="overview-header-actions">
          <span
            className={`overview-live-state${data?.error ? " attention" : ""}`}
            role="status"
          >
            {data?.state === "running"
              ? "更新中"
              : data?.error
                ? "更新待重试"
                : data?.enabled
                  ? data.version
                    ? "持续整理中"
                    : "等待首版"
                  : data?.version
                    ? "已暂停更新"
                    : "未开启"}
          </span>
          <button
            className="text-btn"
            aria-expanded={detailsOpen}
            onClick={() => setDetailsOpen(!detailsOpen)}
          >
            详情与设置
          </button>
          {active && !historical && (!data?.enabled || detailsOpen) && (
            <button
              className="text-btn"
              disabled={busy || !recordingId}
              onClick={() =>
                data?.enabled
                  ? void run(() => api.pauseOverview(recordingId))
                  : setSetup(true)
              }
            >
              {data?.enabled
                ? "暂停概览"
                : data?.failures
                  ? "重新开启概览"
                  : "开启概览"}
            </button>
          )}
        </div>
      </header>
      {data?.error && !detailsOpen && (
        <div className="overview-quiet-warning">
          <span>
            {data.version ? "本轮未更新，继续显示上一版。" : "首版暂未生成。"}
          </span>
          {active && data.enabled && !historical && (
            <button
              className="text-btn"
              disabled={busy || data.state === "running"}
              onClick={() => void run(() => api.refreshOverview(recordingId))}
            >
              重试
            </button>
          )}
          <button className="text-btn" onClick={() => setDetailsOpen(true)}>
            查看原因
          </button>
        </div>
      )}
      <div className="overview-details" hidden={!detailsOpen}>
        <div className="overview-status" role="status">
          {data?.state === "running"
            ? `正在整理 · 已等待 ${data.elapsed_seconds || 0} 秒（本轮最多 ${data.timeout_seconds || 180} 秒）；录音与转写独立继续`
            : data?.error ||
              (!data?.version
                ? active
                  ? "开启后，积累约30秒确认文字生成首版，随后按新增内容更新。"
                  : "本次尚无会中概览；可在会议成果中整理正式纪要。"
                : data?.enabled
                  ? "约45秒检查更新；无新增内容不调用模型。"
                  : "已保留概览，可继续核对。")}
        </div>
        {!!data?.filtered_count && (
          <p className="overview-status">
            本轮有 {data.filtered_count}{" "}
            条候选缺少有效依据，已过滤；上一版与人工确认内容保留，当前重点按预算展示。
          </p>
        )}
        {!!data?.quality?.overflow_count && (
          <p className="overview-status">
            已收拢 {data.quality.overflow_count}{" "}
            条较早或超出当前重点预算的内容。人工确认内容保留；完整讨论请查看原文和正式纪要。
          </p>
        )}
        {!!data?.quality?.duplicates_removed && (
          <p className="overview-status">
            本轮合并 {data.quality.duplicates_removed} 条同源重复内容。
          </p>
        )}
        {!!data?.failures && (
          <p className="overview-status" role="alert">
            已失败 {data.failures} 次。
            {data.enabled
              ? `约 ${data.retry_in_seconds || 0} 秒后重试，也可点击“立即重试”。`
              : data.published
                ? "录音已结束，可在会议成果中整理正式纪要。"
                : "自动重试已暂停，可重新开启概览。"}
          </p>
        )}
        {data?.enabled && !data.version && (
          <p className="overview-status">
            已收到 {data.confirmed_count} 条确认文字，最新位置{" "}
            {clock(data.confirmed_until || 0)}。
            {!data.confirmed_count
              ? "请先确认实时识别正在运行。"
              : "首版通常积累约 30 秒确认文字后开始；也可手动生成。"}
          </p>
        )}
        {(data?.snapshot_provider || data?.provider) && (
          <p className="overview-provider">
            {data.snapshot_provider ? "本版生成：" : "更新模型："}
            {(data.snapshot_provider || data.provider)!.name} ·{" "}
            {(data.snapshot_provider || data.provider)!.model} ·{" "}
            {(data.snapshot_provider || data.provider)!.provider_type ===
            "cloud"
              ? "云端"
              : "本地"}
          </p>
        )}
        {!!data && (!!data.version || (active && data.enabled)) && (
          <div className="overview-toolbar">
            {!!data.version && (
              <span>
                v{data.version} · 已整理 {data.covered_count}/
                {data.confirmed_count} 条确认文字
                {data.gap_count ? ` · 有 ${data.gap_count} 处音频缺段待补` : ""}
              </span>
            )}
            {!!data.version && (
              <select
                aria-label="概览历史版本"
                value={version}
                onChange={(e) => setVersion(Number(e.target.value))}
              >
                <option value={0}>最新概览</option>
                {version > 0 &&
                  !data.history.some((item) => item.version === version) && (
                    <option value={version}>历史快照 v{version}</option>
                  )}
                {data.history.map((h) => (
                  <option key={h.version} value={h.version}>
                    v{h.version} ·{" "}
                    {new Date(h.created.replace(" ", "T") + "Z").toLocaleString(
                      "zh-CN",
                      { hour12: false },
                    )}
                  </option>
                ))}
              </select>
            )}
            {active && data.enabled && !historical && (
              <button
                disabled={busy || data.state === "running"}
                onClick={() => void run(() => api.refreshOverview(recordingId))}
              >
                {data.failures
                  ? "立即重试"
                  : data.version
                    ? "更新一次"
                    : "生成首版"}
              </button>
            )}
          </div>
        )}
      </div>
      {!timeline && !!data?.topics?.length && (
        <label>
          历史议题{" "}
          <select
            aria-label="按议题回看"
            value={version}
            onChange={(event) => setVersion(Number(event.target.value))}
          >
            <option value={0}>最新概览</option>
            {version > 0 &&
              !data.topics.some((topic) => topic.version === version) && (
                <option value={version}>历史快照 v{version}</option>
              )}
            {data.topics.map((topic) => (
              <option key={topic.id} value={topic.version}>
                {topic.start != null ? `${clock(topic.start)} · ` : ""}
                {topic.title}
              </option>
            ))}
          </select>
        </label>
      )}
      {historical && (
        <p className="overview-history-label">
          正在查看历史快照 v{version}
          {data?.latest_version ? ` · 最新为 v${data.latest_version}` : ""}。
          <button className="text-btn" onClick={() => setVersion(0)}>
            返回最新概览
          </button>
        </p>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {timeline && !historical && (
        <nav className="overview-mode-tabs" aria-label="概览内容">
          <button
            aria-pressed={section === "agenda"}
            onClick={() => setSection("agenda")}
          >
            议题时间线
          </button>
          <button
            aria-pressed={section === "outcomes"}
            onClick={() => setSection("outcomes")}
          >
            全场结论与待办
          </button>
          <button
            aria-pressed={section === "terms"}
            onClick={() => setSection("terms")}
          >
            人名与术语
          </button>
        </nav>
      )}
      {termDialog !== undefined && (
        <TermDialog
          scope={recordingId}
          suggestion={termDialog ?? undefined}
          onClose={() => {
            setTermDialog(undefined);
            void load();
          }}
        />
      )}
      <div className="overview-groups">
        {timeline && !historical && section === "terms" ? (
          <section className="overview-term-list">
            <button onClick={() => setTermDialog(null)}>管理本场术语</button>
            {!data?.agenda_terms?.length && (
              <p className="muted">暂无有原话依据的术语建议。</p>
            )}
            {data?.agenda_terms?.map((term) => (
              <article key={term.category + ":" + term.original}>
                <div>
                  <strong>{term.name}</strong>
                  <small>原文写法 · {term.original}</small>
                </div>
                {onLocate && (
                  <button
                    className="text-btn"
                    onClick={() => onLocate(term.evidence)}
                  >
                    {clock(term.evidence.start)} ›
                  </button>
                )}
                <button onClick={() => setTermDialog(term)}>核对写法</button>
              </article>
            ))}
          </section>
        ) : timeline && !historical && section === "outcomes" ? (
          <section aria-label="全场结论与待办">
            {!outcomes.length && (
              <p className="muted">暂无结论、待办或待确认事项。</p>
            )}
            {outcomes.map((item) => {
              const topic = topics.find((entry) => entry.id === item.topic_ref);
              return (
                <div key={item.id}>
                  {topic && (
                    <button
                      className="text-btn overview-topic-link"
                      onClick={() => {
                        setSection("agenda");
                        setOpenTopics((old) => ({ ...old, [topic.id]: true }));
                        requestAnimationFrame(() =>
                          document
                            .getElementById("overview-topic-" + topic.id)
                            ?.scrollIntoView({ block: "nearest" }),
                        );
                      }}
                    >
                      {topic.content} ↗
                    </button>
                  )}
                  {renderItem(item)}
                </div>
              );
            })}
          </section>
        ) : agendaView ? (
          <>
            <section className="overview-brief">
              <strong>会议速览</strong>
              {data?.covered_until != null && (
                <small>已整理至 {clock(data.covered_until)}</small>
              )}
              <p>{currentTopic?.content}</p>
              {digest.map((item) => (
                <p key={item.id}>
                  {labels[item.kind]} · {item.content}
                </p>
              ))}
            </section>
            {topics.map((topic) => {
              const rows = entries.filter(
                (item) => item.topic_ref === topic.id,
              );
              const selected = topic.id === currentTopic?.id;
              const reopened = selected && topics.at(-1)?.id !== topic.id;
              const clues = rows.filter(
                (item) =>
                  item.kind === "point" &&
                  !item.stale &&
                  item.decision !== "dismissed",
              );
              const question = rows.find(
                (item) =>
                  item.kind === "question" &&
                  !item.stale &&
                  item.decision !== "dismissed",
              );
              return (
                <section
                  className="overview-agenda-topic"
                  key={topic.id}
                  id={"overview-topic-" + topic.id}
                >
                  <button
                    className="overview-topic-toggle"
                    aria-expanded={openTopics[topic.id] ?? selected}
                    onClick={() =>
                      setOpenTopics((old) => ({
                        ...old,
                        [topic.id]: !(old[topic.id] ?? selected),
                      }))
                    }
                  >
                    <time>
                      {clock(topic.topic_start ?? latestEvidence(topic))}
                    </time>
                    <strong>{topic.content}</strong>
                    <small>
                      {reopened ? "重新讨论" : selected ? "正在讨论" : "已讨论"}{" "}
                      · {rows.length} 条
                    </small>
                  </button>
                  {(openTopics[topic.id] ?? selected) && (
                    <div className="overview-topic-content">
                      {renderItem(topic)}
                      {rows.map(renderItem)}
                      {question && clues.length >= 2 && (
                        <details className="overview-fishbone">
                          <summary>展开议题分析图</summary>
                          <p>{question.content}</p>
                          <ul>
                            {clues.map((clue) => {
                              const evidence = clue.evidence.find(
                                (entry) => !entry.stale,
                              );
                              return (
                                <li key={clue.id}>
                                  {clue.content}
                                  {onLocate && evidence && (
                                    <button
                                      className="text-btn"
                                      onClick={() => onLocate(evidence)}
                                    >
                                      {clock(evidence.start)} ›
                                    </button>
                                  )}
                                </li>
                              );
                            })}
                          </ul>
                        </details>
                      )}
                    </div>
                  )}
                </section>
              );
            })}
            {!!unassigned.length && (
              <details className="overview-past-topics">
                <summary>未归类 · {unassigned.length} 条</summary>
                {unassigned.map(renderItem)}
              </details>
            )}
          </>
        ) : (
          <>
            {!data?.items.length && (
              <div className="overview-empty">
                <strong>
                  {data?.enabled ? "正在听取会议内容" : "把讨论脉络留在这里"}
                </strong>
                <p>
                  {data?.state === "running"
                    ? "首版正在整理，内容就绪后会自动显示。"
                    : data?.enabled
                      ? "积累约 30 秒确认文字后自动整理。"
                      : "开启后，自动梳理重点、分歧与后续行动。"}
                </p>
                {active &&
                  data?.enabled &&
                  data.state !== "running" &&
                  !data.error && (
                    <button
                      className="text-btn"
                      disabled={busy || !data.confirmed_count}
                      onClick={() =>
                        void run(() => api.refreshOverview(recordingId))
                      }
                    >
                      现在整理
                    </button>
                  )}
              </div>
            )}
            {(Object.keys(labels) as (keyof typeof labels)[]).map((kind) => {
              const group = items.filter((i) => i.kind === kind);
              return (
                !!group.length && (
                  <section
                    key={kind}
                    className="overview-group"
                    data-kind={kind}
                  >
                    {(!timeline || kind === "topic") && (
                      <h4>
                        {historical && kind === "topic"
                          ? "历史议题"
                          : labels[kind]}
                      </h4>
                    )}
                    {group.map(renderItem)}
                  </section>
                )
              );
            })}
          </>
        )}
        {timeline &&
          section === "agenda" &&
          data?.topics?.some(
            (topic) =>
              topic.version !== data.latest_version && !topicIds.has(topic.id),
          ) && (
            <details className="overview-past-topics">
              <summary>历史议题</summary>
              {data.topics
                .filter(
                  (topic) =>
                    topic.version !== data.latest_version &&
                    !topicIds.has(topic.id),
                )
                .map((topic) => (
                  <button
                    key={topic.id}
                    className="text-btn"
                    aria-pressed={version === topic.version}
                    onClick={() => setVersion(topic.version)}
                  >
                    {topic.start != null && <time>{clock(topic.start)}</time>}
                    {topic.title}
                  </button>
                ))}
            </details>
          )}
      </div>
      {detailsOpen && data?.items.some((i) => i.decision === "dismissed") && (
        <label className="overview-ignored">
          <input
            type="checkbox"
            checked={showIgnored}
            onChange={(e) => setShowIgnored(e.target.checked)}
          />{" "}
          显示已忽略条目
        </label>
      )}
      {!active && !!data?.version && (
        <p className="muted">
          已确认且能匹配当前文字稿的内容，会作为正式纪要整理的关注点；不会直接变成会议事实。
        </p>
      )}
      {setup && (
        <Modal
          className="small-dialog"
          ariaLabel="开启会中概览"
          onClose={() => setSetup(false)}
        >
          <div className="overview-heading">
            <h2>开启会中概览</h2>
            <button onClick={() => setSetup(false)}>关闭</button>
          </div>
          <p>
            本场开启，记住模型选择；下一场仍需主动开启。仅整理已确认文字，不影响原音保存。
          </p>
          <label>
            概览模型
            <select
              value={profileId}
              onChange={(e) => setProfileId(e.target.value)}
              aria-label="概览模型"
            >
              <option value="">选择已配置服务</option>
              {profiles.map((p) => (
                <option value={p.id} key={p.id}>
                  {p.name} · {p.model} ·{" "}
                  {p.provider_type === "cloud" ? "云端" : "本地"}
                </option>
              ))}
            </select>
          </label>
          <p className="muted">
            个人备注默认不参与；明确选入的简记是关注点，不能替代原文证据。
          </p>
          <div className="overview-item-actions">
            <button
              className="primary"
              disabled={busy || !profileId}
              onClick={enable}
            >
              开启本场概览
            </button>
            {onModels && (
              <button
                onClick={() => {
                  setSetup(false);
                  onModels();
                }}
              >
                管理模型配置
              </button>
            )}
          </div>
        </Modal>
      )}
      {editing && (
        <Modal
          className="small-dialog"
          ariaLabel="编辑概览条目"
          onClose={() => setEditing(null)}
        >
          <h2>编辑并确认</h2>
          <p>以原文为依据修改；保存后不会被 AI 覆盖。</p>
          <textarea
            aria-label="概览内容"
            maxLength={350}
            value={content}
            onChange={(e) => setContent(e.target.value)}
          />
          <div className="overview-item-actions">
            <button
              disabled={busy || !content.trim()}
              onClick={() =>
                void decide(editing, "confirmed", content.trim()).then((ok) => {
                  if (ok) setEditing(null);
                })
              }
            >
              确认保存
            </button>
            <button onClick={() => setEditing(null)}>取消</button>
          </div>
          {error && <p role="alert">{error}</p>}
        </Modal>
      )}
    </section>
  );
}
