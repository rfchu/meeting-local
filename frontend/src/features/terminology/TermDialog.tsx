import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../api";
import Modal from "../../components/Modal";
import type { LLMConfig } from "../../types";
import { useModelChoice } from "../../preferences/useModelChoice";
import type { Term, TermList } from "./terms";
import "./terminology.css";

const categories: Record<string, string> = {
  person: "人名",
  organization: "机构",
  product: "产品",
  technical: "专业术语",
  other: "其他",
};
export default function TermDialog({
  scope,
  canScan = false,
  onClose,
  onLocate,
  suggestion,
}: {
  scope: string;
  canScan?: boolean;
  onClose: () => void;
  suggestion?: { name: string; original: string; category: string };
  onLocate?: (id: string) => void;
}) {
  const [showIgnored, setShowIgnored] = useState(false);
  const [library, setLibrary] = useState(scope === "library");
  const target = library ? "library" : scope;
  const [data, setData] = useState<TermList>({
    terms: [],
    library: [],
    job: null,
  });
  const [name, setName] = useState("");
  const [aliases, setAliases] = useState("");
  const [category, setCategory] = useState("technical");
  const [editing, setEditing] = useState<Term | null>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [profiles, setProfiles] = useState<LLMConfig[]>([]);
  const [profileId, setProfileId] = useModelChoice("terminology");
  const generation = useRef(0);
  const nameInput = useRef<HTMLInputElement>(null);
  const load = useCallback(async () => {
    const current = ++generation.current;
    const result = await api.terms(target);
    if (generation.current === current) setData(result);
  }, [target]);
  const clear = () => {
    setEditing(null);
    setName("");
    setAliases("");
    setCategory("technical");
  };
  useEffect(() => {
    clear();
    if (!library && suggestion) {
      setName(suggestion.name);
      setAliases(
        suggestion.original === suggestion.name ? "" : suggestion.original,
      );
      setCategory(suggestion.category);
    }
    setError("");
    setData({ terms: [], library: [], job: null });
    void load().catch((e) => setError(e.message));
    return () => {
      generation.current++;
    };
  }, [load, library, suggestion]);
  useEffect(() => {
    let live = true;
    void api
      .environment()
      .then((env) => {
        if (!live) return;
        setProfiles(env.analysis.profiles);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, []);
  const active = !!data.job && ["queued", "running"].includes(data.job.state);
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => {
      void load().catch((e) => setError(e.message));
    }, 1800);
    return () => window.clearInterval(timer);
  }, [active, load]);
  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await action();
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const input = () => ({
    name: name.trim(),
    aliases: aliases
      .split(/[、,，;；\n]/)
      .map((s) => s.trim())
      .filter(Boolean),
    category,
    status: "confirmed" as const,
  });
  const copyTo = (term: Term, destination: string) =>
    api.createTerm(destination, {
      name: term.name,
      aliases: term.aliases,
      category: term.category,
      status: "confirmed",
    });
  const edit = (term: Term) => {
    setEditing(term);
    setName(term.name);
    setAliases(term.aliases.join("、"));
    setCategory(term.category);
    nameInput.current?.focus();
  };
  const scan = () => {
    const profile = profiles.find((p) => p.id === profileId);
    if (!profile) {
      setError("请先选择模型配置");
      return;
    }
    const cloud = profile?.provider_type === "cloud";
    if (
      cloud &&
      !window.confirm(
        `将本场完整文字稿发送至 ${profile.name}（${profile.base_url}），仅整理待确认的术语候选。是否继续？`,
      )
    )
      return;
    void run(() =>
      api.scanTerms(
        scope,
        profileId || undefined,
        !!cloud,
        profile
          ? {
              id: profile.id,
              base_url: profile.base_url,
              model: profile.model,
              provider_type: profile.provider_type,
            }
          : undefined,
      ),
    );
  };
  const importable = data.library.filter(
    (term) =>
      !data.terms.some(
        (existing) => existing.name.toLowerCase() === term.name.toLowerCase(),
      ),
  );
  const visible = data.terms.filter(
    (t) => showIgnored || t.status !== "dismissed",
  );
  return (
    <Modal
      id="terminologyDialog"
      className="terminology-dialog"
      ariaLabel="术语管理"
      onClose={onClose}
    >
      <div className="term-heading">
        <div>
          <h2>术语管理</h2>
          <p className="muted">
            确认名称与识别别名，便于搜索和统一用词；原文保持可核对。
          </p>
        </div>
        <button className="text-btn" onClick={onClose}>
          关闭
        </button>
      </div>
      {scope !== "library" && (
        <nav className="term-tabs" aria-label="术语范围">
          <button
            disabled={busy}
            aria-pressed={!library}
            onClick={() => setLibrary(false)}
          >
            本场会议
          </button>
          <button
            disabled={busy}
            aria-pressed={library}
            onClick={() => setLibrary(true)}
          >
            个人词库
          </button>
        </nav>
      )}
      <p className="muted">
        {library
          ? "已确认的个人词库辅助本机 Qwen 实时识别；导入本场后才会用于该会议纪要。"
          : "本场已确认术语用于 Qwen 识别提示、文字搜索及 AI 纪要；候选需人工确认。"}
      </p>
      <form
        className="term-form"
        onSubmit={(event) => {
          event.preventDefault();
          void run(async () => {
            if (editing)
              await api.updateTerm(target, editing.id, {
                ...input(),
                version: editing.version,
              });
            else await api.createTerm(target, input());
            clear();
          });
        }}
      >
        <label>
          规范名称
          <input
            ref={nameInput}
            disabled={busy}
            required
            maxLength={100}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="例如：项目或产品名称"
          />
        </label>
        <label>
          类别
          <select
            disabled={busy}
            value={category}
            onChange={(e) => setCategory(e.target.value)}
          >
            {Object.entries(categories).map(([id, text]) => (
              <option key={id} value={id}>
                {text}
              </option>
            ))}
          </select>
        </label>
        <label className="term-aliases">
          原文别名
          <input
            disabled={busy}
            value={aliases}
            onChange={(e) => setAliases(e.target.value)}
            placeholder="多个写法用顿号分隔"
          />
        </label>
        <div className="term-actions">
          <button
            disabled={
              busy ||
              !name.trim() ||
              !!(editing?.stale && editing.status === "candidate")
            }
          >
            {editing ? "保存并确认" : "添加术语"}
          </button>
          {editing && (
            <button type="button" disabled={busy} onClick={clear}>
              取消编辑
            </button>
          )}
        </div>
      </form>
      {!library && canScan && (
        <div className="term-scan">
          <label>
            整理模型
            <select
              aria-label="术语整理模型"
              value={profileId}
              onChange={(e) => setProfileId(e.target.value)}
            >
              <option value="">请选择模型配置</option>
              {profiles.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} · {p.model}
                  {p.provider_type === "cloud" ? " · 云端" : " · 本地"}
                </option>
              ))}
            </select>
          </label>
          <button disabled={busy || active} onClick={scan}>
            AI 整理候选
          </button>
          <small className="muted">
            仅在点击后整理已保存文字稿，候选不会自动用于识别。
          </small>
        </div>
      )}
      {active && (
        <p role="status">
          {data.job?.stage}
          <button
            disabled={busy}
            onClick={() =>
              void run(() => api.jobAction(data.job!.id, "cancel"))
            }
          >
            取消
          </button>
        </p>
      )}
      {data.job?.state === "failed" && <p role="alert">{data.job.error}</p>}
      {data.job?.state === "completed" && !library && (
        <p className="muted">
          候选整理完成；重复名称已跳过。
          {!visible.length && "本次没有找到可保存的候选。"}
        </p>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {data.terms.some((t) => t.status === "dismissed") && (
        <label>
          <input
            type="checkbox"
            checked={showIgnored}
            onChange={(e) => setShowIgnored(e.target.checked)}
          />{" "}
          显示已忽略候选
        </label>
      )}
      <div className="term-list" aria-busy={busy}>
        {!visible.length && (
          <p className="muted">
            {library
              ? "尚无个人术语，可添加常用的人名、机构或专业词。"
              : "尚无术语，可手动添加、从个人词库导入或整理 AI 候选。"}
          </p>
        )}
        {visible.map((term) => (
          <article key={term.id} className="term-card">
            <div className="term-heading">
              <strong>{term.name}</strong>
              <span
                className={
                  term.status === "candidate" ? "term-candidate" : "muted"
                }
              >
                {term.stale
                  ? "依据已过期"
                  : term.status === "candidate"
                    ? "待确认"
                    : term.status === "dismissed"
                      ? "已忽略"
                      : "已确认"}{" "}
                · {categories[term.category]}
              </span>
            </div>
            {!!term.aliases.length && (
              <p>原文别名：{term.aliases.join("、")}</p>
            )}
            {term.reason && <p className="muted">{term.reason}</p>}
            {term.evidence.map((item, index) => (
              <blockquote key={index}>
                {item.quote}
                {onLocate && !library && (
                  <button
                    className="text-btn"
                    disabled={term.stale}
                    onClick={() => onLocate(item.segment_id)}
                  >
                    回听原文
                  </button>
                )}
              </blockquote>
            ))}
            <div className="term-actions">
              {term.status === "candidate" && (
                <button
                  disabled={busy || term.stale}
                  onClick={() =>
                    void run(() =>
                      api.updateTerm(target, term.id, {
                        ...term,
                        status: "confirmed",
                      }),
                    )
                  }
                >
                  确认采用
                </button>
              )}
              <button disabled={busy} onClick={() => edit(term)}>
                编辑
              </button>
              {!library &&
                term.status === "confirmed" &&
                !data.library.some(
                  (saved) =>
                    saved.name.toLowerCase() === term.name.toLowerCase(),
                ) && (
                  <button
                    disabled={busy}
                    onClick={() => void run(() => copyTo(term, "library"))}
                  >
                    保存到个人词库
                  </button>
                )}
              {term.status === "candidate" ? (
                <button
                  disabled={busy}
                  onClick={() =>
                    void run(() =>
                      api.updateTerm(target, term.id, {
                        ...term,
                        status: "dismissed",
                      }),
                    )
                  }
                >
                  忽略
                </button>
              ) : (
                <button
                  disabled={busy}
                  onClick={() => {
                    if (
                      window.confirm(`删除术语“${term.name}”？原文不会改变。`)
                    )
                      void run(async () => {
                        await api.deleteTerm(target, term.id, term.version);
                        if (editing?.id === term.id) clear();
                      });
                  }}
                >
                  删除
                </button>
              )}
            </div>
          </article>
        ))}
      </div>
      {!library && !!importable.length && (
        <section className="term-import">
          <h3>从个人词库导入</h3>
          <div className="term-actions">
            {importable.map((term) => (
              <button
                disabled={busy}
                key={term.id}
                onClick={() => void run(() => copyTo(term, scope))}
              >
                ＋ {term.name}
              </button>
            ))}
          </div>
        </section>
      )}
    </Modal>
  );
}
