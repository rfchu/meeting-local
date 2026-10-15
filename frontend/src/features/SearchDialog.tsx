import { useEffect, useState } from "react";
import Modal from "../components/Modal";
import { api } from "../api";
import type { LLMConfig, MeetingSummary } from "../types";
import { clock } from "../utils";

export interface SearchHit {
  id: string;
  fingerprint: string;
  note_scope?: string;
  meeting_id: string;
  title: string;
  date: string;
  revision: number;
  kind: string;
  content: string;
  start: number | null;
  end: number | null;
  source_segment_ids: string[];
  match: string;
}
export interface SearchStatus {
  config: { profile_id: string; model: string; dimension: number } | null;
  units: number;
  indexed: number;
  pending: number;
  vec_ready: boolean;
  error: string;
  job: {
    id: string;
    state: string;
    stage: string;
    progress: number;
    error: string;
  } | null;
}
const labels: Record<string, string> = {
  transcript: "文字稿",
  note: "原始简记",
  expanded_note: "已接受扩写",
  minutes: "已保存纪要",
};
export default function SearchDialog({
  meetings,
  onClose,
  onOpen,
}: {
  meetings: MeetingSummary[];
  onClose: () => void;
  onOpen: (hit: SearchHit) => Promise<void>;
}) {
  const [query, setQuery] = useState(""),
    [mode, setMode] = useState("keyword"),
    [meetingId, setMeetingId] = useState(""),
    [kind, setKind] = useState("");
  const [status, setStatus] = useState<SearchStatus | null>(null),
    [profiles, setProfiles] = useState<LLMConfig[]>([]),
    [profileId, setProfileId] = useState(""),
    [model, setModel] = useState("");
  const [results, setResults] = useState<SearchHit[]>([]),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [searched, setSearched] = useState(false),
    [pending, setPending] = useState(0);
  const active =
    status?.job && ["queued", "running"].includes(status.job.state);
  const poll = async () => {
    setStatus(await api.searchStatus());
  };
  useEffect(() => {
    let live = true;
    Promise.all([api.searchStatus(), api.environment()])
      .then(([s, env]) => {
        if (!live) return;
        setStatus(s);
        const local = env.analysis.profiles.filter(
          (p) => p.provider_type === "local",
        );
        setProfiles(local);
        setProfileId(s.config?.profile_id || local[0]?.id || "");
        setModel(s.config?.model || "");
      })
      .catch((e) => live && setError((e as Error).message));
    const timer = window.setInterval(() => void poll().catch(() => {}), 3000);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, []);
  const action = async (run: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await run();
      await poll();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const search = () =>
    action(async () => {
      const response = await api.searchLibrary({
        query,
        mode,
        meeting_id: meetingId || null,
        kind: kind || null,
      });
      setResults(response.results);
      setPending(response.pending);
      setSearched(true);
    });
  return (
    <Modal onClose={onClose} className="search-dialog">
      <div className="search-heading">
        <div>
          <small>本机会议资料库</small>
          <h2>跨会议搜索</h2>
        </div>
        <button onClick={onClose}>关闭</button>
      </div>
      <p className="muted">
        找到讨论、决定和简记，打开原文核对。关键词搜索无需模型；语义搜索仅使用本机向量模型，不生成回答。
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void search();
        }}
      >
        <div className="library-query">
          <input
            aria-label="搜索会议资料"
            value={query}
            maxLength={500}
            placeholder="搜索项目、人名、事项或相关讨论…"
            onChange={(event) => setQuery(event.target.value)}
          />
          <button className="primary" disabled={busy || !query.trim()}>
            搜索
          </button>
        </div>
        <div className="library-filters">
          <select
            aria-label="检索方式"
            value={mode}
            onChange={(event) => setMode(event.target.value)}
          >
            <option value="keyword">关键词</option>
            <option value="semantic">语义相近</option>
            <option value="hybrid">关键词 + 语义</option>
          </select>
          <select
            aria-label="搜索会议范围"
            value={meetingId}
            onChange={(event) => setMeetingId(event.target.value)}
          >
            <option value="">全部会议</option>
            {meetings.map((m) => (
              <option key={m.id} value={m.id}>
                {m.title}
              </option>
            ))}
          </select>
          <select
            aria-label="搜索资料类型"
            value={kind}
            onChange={(event) => setKind(event.target.value)}
          >
            <option value="">全部资料类型</option>
            {Object.entries(labels).map(([id, label]) => (
              <option key={id} value={id}>
                {label}
              </option>
            ))}
          </select>
        </div>
      </form>
      <details className="search-index">
        <summary>
          本地语义索引 · {status?.indexed || 0}/{status?.units || 0} 已索引
        </summary>
        <p>
          请选择支持 /embeddings
          的模型，聊天模型通常不能直接用于向量检索。数据仅提交至所选本机服务。更换模型后需重建；同名模型权重变化请点“全部重建”。
        </p>
        <label>
          本机服务{" "}
          <select
            aria-label="向量模型本机服务"
            value={profileId}
            onChange={(event) => setProfileId(event.target.value)}
          >
            {!profiles.length && <option value="">请先添加本地模型配置</option>}
            {profiles.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Embedding 模型名称{" "}
          <input
            aria-label="Embedding 模型名称"
            value={model}
            onChange={(event) => setModel(event.target.value)}
            placeholder="填写 LM Studio 中的向量模型标识"
          />
        </label>
        <div className="search-index-actions">
          <button
            disabled={busy || !!active || !profileId || !model.trim()}
            onClick={() =>
              void action(() => api.configureSearch(profileId, model.trim()))
            }
          >
            测试并保存
          </button>
          <button
            disabled={busy || !!active || !status?.config || !status.vec_ready}
            onClick={() => void action(() => api.buildSearch(false))}
          >
            更新索引
          </button>
          <button
            disabled={busy || !!active || !status?.config || !status.vec_ready}
            onClick={() => void action(() => api.buildSearch(true))}
          >
            全部重建
          </button>
        </div>
        {status?.config && (
          <p>
            当前模型：{status.config.model} · {status.config.dimension}{" "}
            维；待索引 {status.pending} 条。
          </p>
        )}
        {status?.error && <p role="alert">{status.error}</p>}
        {status?.job && (
          <p role="status">
            {status.job.stage} · {Math.round(status.job.progress * 100)}%{" "}
            {active && (
              <button
                disabled={busy}
                onClick={() =>
                  void action(() => api.jobAction(status.job!.id, "cancel"))
                }
              >
                取消索引
              </button>
            )}
          </p>
        )}
        {status?.job?.state === "failed" && (
          <p role="alert">{status.job.error}</p>
        )}
      </details>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {busy && <p role="status">正在处理…</p>}
      {searched && (
        <p className="muted">
          找到 {results.length} 条，最多显示 20 条。
          {mode !== "keyword" &&
            pending > 0 &&
            ` ${pending} 条资料尚未建立语义索引，可先用关键词搜索。`}{" "}
          语义相近不代表内容正确或已形成决策。
        </p>
      )}
      {searched && !results.length && (
        <p>没有找到相关资料。可缩短关键词、更改筛选，或建立语义索引后再试。</p>
      )}
      <div className="library-results">
        {results.map((hit) => (
          <article key={hit.id}>
            <div>
              <strong>{hit.title}</strong>
              <small>
                {hit.date} · v{hit.revision} · {labels[hit.kind]}
                {hit.note_scope === "personal" ? " · 个人备注" : ""}
              </small>
            </div>
            <p>{hit.content}</p>
            <button
              disabled={busy}
              onClick={() => void action(() => onOpen(hit))}
            >
              {hit.start !== null
                ? clock(hit.start) + " 打开并回听"
                : "打开会议原文"}
            </button>
          </article>
        ))}
      </div>
    </Modal>
  );
}
