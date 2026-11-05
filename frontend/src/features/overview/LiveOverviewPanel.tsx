import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../api";
import Modal from "../../components/Modal";
import type { LLMConfig } from "../../types";
import { clock } from "../../utils";
import { useModelChoice } from "../../preferences/useModelChoice";
import type { OverviewItem, OverviewStatus, OverviewEvidence } from "./types";
import "./overview.css";
const labels = {
  topic: "当前议题",
  point: "关键观点",
  question: "待确认问题",
  action: "可能的待办",
};
export default function LiveOverviewPanel({
  recordingId,
  engine = "qwen",
  active = false,
  profiles = [],
  onModels,
  onLocate,
  locateLabel = "查看原文",
}: {
  recordingId: string;
  engine?: "qwen" | "whisper";
  active?: boolean;
  profiles?: LLMConfig[];
  onModels?: () => void;
  onLocate?: (e: OverviewEvidence) => void;
  locateLabel?: string;
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
  const [detailsOpen, setDetailsOpen] = useState(false);
  const seq = useRef(0);
  const load = useCallback(async () => {
    if (!recordingId) return;
    const token = ++seq.current;
    const value = await api.liveOverview(recordingId, version || undefined);
    if (token === seq.current) setData(value);
  }, [recordingId, version]);
  useEffect(() => {
    setData(null);
    setError("");
    void load().catch((e) => setError(e.message));
    const timer = window.setInterval(
      () => void load().catch((e) => setError(e.message)),
      5000,
    );
    return () => {
      seq.current++;
      window.clearInterval(timer);
    };
  }, [load]);
  useEffect(() => {
    setVersion(0);
    setEditing(null);
  }, [recordingId]);
  const run = async (action: () => Promise<OverviewStatus>) => {
    setBusy(true);
    setError("");
    try {
      const result = await action();
      seq.current++;
      setData(result);
      return true;
    } catch (e) {
      setError((e as Error).message);
      void load().catch(() => {});
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
  const historical = !!version,
    items = (data?.items || []).filter(
      (i) => showIgnored || i.decision !== "dismissed",
    );
  return (
    <section className="live-overview-panel" aria-label="AI 会中概览">
      <header className="overview-heading">
        <div>
          <h3>AI 会中概览</h3>
          <small>AI 整理 · 会中参考</small>
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
          {active && data.enabled && (
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
            条候选缺少有效依据，已过滤；原有有效重点保留。
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
      {historical && (
        <p className="overview-history-label">
          正在查看历史快照。请在“详情与设置”中切回最新概览后核对。
        </p>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <div className="overview-groups">
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
              <section key={kind} className="overview-group" data-kind={kind}>
                <h4>{labels[kind]}</h4>
                {group.map((item) => (
                  <article
                    key={item.id}
                    className={`overview-item ${item.decision}${item.stale ? " stale" : ""}`}
                  >
                    <div className="overview-item-main">
                      <p>{item.content}</p>
                      <button
                        className="overview-review-toggle text-btn"
                        aria-label="查看依据与核对"
                        aria-expanded={expanded === item.id}
                        onClick={() =>
                          setExpanded(expanded === item.id ? null : item.id)
                        }
                      >
                        ···
                      </button>
                    </div>
                    {(item.stale || item.decision !== "pending") && (
                      <small>
                        {item.stale
                          ? "依据或关联简记已变化，需重新核对"
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
                        {item.evidence.map((e, index) => (
                          <blockquote key={index}>
                            <p>{e.quote}</p>
                            {onLocate && (
                              <button
                                className="text-btn"
                                disabled={item.stale}
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
                ))}
              </section>
            )
          );
        })}
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
