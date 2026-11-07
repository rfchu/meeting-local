import { useEffect, useState } from "react";
import Modal from "../components/Modal";
import { api } from "../api";
import type { MeetingSummary } from "../types";
import { clock } from "../utils";
import CrossMeetingQA from "./CrossMeetingQA";
import {
  semanticLanguageHint,
  type RetrievalLanguageSupport,
} from "./retrievalLanguage";

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
  language_support?: RetrievalLanguageSupport;
  builtin?: {
    installed: boolean;
    loaded: boolean;
    runtime_ready: boolean;
    state: string;
    downloaded: number;
    total: number;
    error: string;
    model: string;
  };
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
  onConfigure,
  onManageIndex,
}: {
  meetings: MeetingSummary[];
  onClose: () => void;
  onOpen: (hit: SearchHit) => Promise<void>;
  onConfigure: () => void;
  onManageIndex: () => void;
}) {
  const [qaReady, setQaReady] = useState(false);
  const [view, setView] = useState<"search" | "qa">("search");
  const [query, setQuery] = useState(""),
    [mode, setMode] = useState("keyword"),
    [meetingId, setMeetingId] = useState(""),
    [kind, setKind] = useState("");
  const [status, setStatus] = useState<SearchStatus | null>(null);
  const [results, setResults] = useState<SearchHit[]>([]),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [searched, setSearched] = useState(false),
    [pending, setPending] = useState(0);
  const poll = async () => {
    setStatus(await api.searchStatus());
  };
  useEffect(() => {
    let live = true;
    Promise.all([api.searchStatus(), api.environment()])
      .then(([s, env]) => {
        if (!live) return;
        setStatus(s);
        setQaReady(env.qa_ready === true);
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
          <h2>跨会议资料</h2>
        </div>
        <button onClick={onClose}>关闭</button>
      </div>
      <div className="tabs" aria-label="资料使用方式">
        <button
          className={view === "search" ? "tab active" : "tab"}
          onClick={() => setView("search")}
        >
          资料搜索
        </button>
        <button
          className={view === "qa" ? "tab active" : "tab"}
          onClick={() => setView("qa")}
        >
          引用问答
        </button>
      </div>
      {view === "qa" ? (
        qaReady ? (
          <CrossMeetingQA
            languageSupport={status?.language_support}
            meetings={meetings}
            onOpen={onOpen}
            onConfigure={onConfigure}
          />
        ) : (
          <p role="status">
            引用问答将在录音结束、服务更新后启用；资料搜索仍可正常使用。
          </p>
        )
      ) : (
        <>
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
          <div className="search-index-summary">
            <span>
              语义索引 · {status?.indexed || 0}/{status?.units || 0} 已索引
            </span>
            <button onClick={onManageIndex}>管理语义索引</button>
          </div>
          {mode !== "keyword" && (
            <p className="muted">
              {semanticLanguageHint(status?.language_support)}
            </p>
          )}
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
            <p>
              没有找到相关资料。可缩短关键词、更改筛选，或建立语义索引后再试。
            </p>
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
        </>
      )}
    </Modal>
  );
}
