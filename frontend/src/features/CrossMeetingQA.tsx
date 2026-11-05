import { useModelChoice } from "../preferences/useModelChoice";
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { LLMConfig, MeetingSummary } from "../types";
import type { SearchHit } from "./SearchDialog";
import type { QARun } from "./qaTypes";
import { clock } from "../utils";

export default function CrossMeetingQA({
  meetings,
  onOpen,
  onConfigure,
}: {
  meetings: MeetingSummary[];
  onOpen: (source: SearchHit) => Promise<void>;
  onConfigure: () => void;
}) {
  const [question, setQuestion] = useState(""),
    [query, setQuery] = useState("");
  const [profiles, setProfiles] = useState<LLMConfig[]>([]),
    [profileId, setProfileId] = useModelChoice("qa");
  const [mode, setMode] = useState("keyword"),
    [meetingId, setMeetingId] = useState(""),
    [kind, setKind] = useState("transcript");
  const [personal, setPersonal] = useState(false),
    [cloud, setCloud] = useState(false);
  const [run, setRun] = useState<QARun | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [verified, setVerified] = useState(true),
    [history, setHistory] = useState<
      { id: string; question: string; created: string }[]
    >([]);
  const epoch = useRef(0);
  const refreshHistory = async () => setHistory(await api.qaHistory());
  useEffect(() => {
    let live = true;
    void Promise.all([api.environment(), api.qaHistory()])
      .then(([env, rows]) => {
        if (!live) return;
        setProfiles(env.analysis.profiles);
        setHistory(rows);
      })
      .catch((e) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, []);
  useEffect(() => {
    if (!run?.id || busy) return;
    const id = run.id;
    const generation = epoch.current;
    let live = true,
      polling = false;
    const poll = async () => {
      if (!live || polling) return;
      polling = true;
      try {
        const fresh = await api.qaRun(id);
        if (live && generation === epoch.current) {
          setRun(fresh);
          setVerified(true);
        }
      } catch (e) {
        if (live && generation === epoch.current) {
          setVerified(false);
          setError((e as Error).message);
        }
      } finally {
        polling = false;
      }
    };
    const timer = window.setInterval(() => void poll(), 5000);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [run?.id, busy]);
  const invalidate = () => {
    epoch.current += 1;
    setRun(null);
    setCloud(false);
    setError("");
    setVerified(true);
  };
  const act = async (work: () => Promise<void>) => {
    epoch.current += 1;
    setBusy(true);
    setError("");
    try {
      await work();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const openSource = async (source: SearchHit) => {
    try {
      await onOpen(source);
    } catch (error) {
      setVerified(false);
      throw error;
    }
  };
  const active = !!run?.job && ["queued", "running"].includes(run.job.state);
  const selected = profiles.find((p) => p.id === profileId);
  const prepare = () =>
    act(async () => {
      const generation = epoch.current;
      const next = await api.qaPrepare({
        question: question.trim(),
        query: query.trim(),
        profile_id: profileId,
        mode,
        meeting_id: meetingId || null,
        kind: kind || null,
        include_personal: personal,
      });
      if (generation !== epoch.current) return;
      setRun(next);
      setCloud(false);
      setVerified(true);
      await refreshHistory();
    });
  return (
    <section className="cross-meeting-qa" aria-label="跨会议引用问答">
      <p>
        单次提问，只使用本次检索依据，不自动读取旧问答作为上下文。默认只检索文字稿，个人备注默认排除。
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void prepare();
        }}
      >
        <fieldset disabled={busy || active}>
          <label>
            问题
            <textarea
              aria-label="跨会议问题"
              maxLength={500}
              rows={3}
              value={question}
              onChange={(e) => {
                invalidate();
                setQuestion(e.target.value);
              }}
              placeholder="例如：项目交付时间在哪些会议里发生过变化？"
            />
          </label>
          <label>
            检索关键词（可选）
            <input
              aria-label="问答检索关键词"
              value={query}
              maxLength={500}
              onChange={(e) => {
                invalidate();
                setQuery(e.target.value);
              }}
              placeholder="关键词模式可填写：项目名称 交付 排期"
            />
          </label>
          <div className="library-filters">
            <label>
              回答模型
              <select
                aria-label="问答模型配置"
                value={profileId}
                onChange={(e) => {
                  invalidate();
                  setProfileId(e.target.value);
                }}
              >
                <option value="">请选择模型配置</option>
                {profiles.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} · {p.provider_type === "local" ? "本地" : "云端"} ·{" "}
                    {p.model}
                  </option>
                ))}
              </select>
            </label>
            <label>
              检索方式
              <select
                aria-label="问答检索方式"
                value={mode}
                onChange={(e) => {
                  invalidate();
                  setMode(e.target.value);
                }}
              >
                <option value="keyword">关键词（无需向量模型）</option>
                <option value="hybrid">关键词 + 语义</option>
                <option value="semantic">语义相近</option>
              </select>
            </label>
            <label>
              会议范围
              <select
                aria-label="问答会议范围"
                value={meetingId}
                onChange={(e) => {
                  invalidate();
                  setMeetingId(e.target.value);
                }}
              >
                <option value="">全部会议</option>
                {meetings.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.title}
                  </option>
                ))}
              </select>
            </label>
            <label>
              依据类型
              <select
                aria-label="问答依据类型"
                value={kind}
                onChange={(e) => {
                  invalidate();
                  setKind(e.target.value);
                }}
              >
                <option value="transcript">仅文字稿</option>
                <option value="">文字稿、已保存纪要与简记</option>
              </select>
            </label>
          </div>
          {!kind && (
            <label className="qa-consent">
              <input
                type="checkbox"
                checked={personal}
                onChange={(e) => {
                  invalidate();
                  setPersonal(e.target.checked);
                }}
              />
              允许使用个人备注作为线索（不会视为会议共识）
            </label>
          )}
          <div className="qa-actions">
            <button
              className="primary"
              disabled={busy || !question.trim() || !profileId || active}
            >
              1. 检索并预览依据
            </button>
            <button type="button" className="text-btn" onClick={onConfigure}>
              配置 LLM
            </button>
          </div>
          {selected && (
            <small>
              回答使用{selected.provider_type === "cloud" ? "云端" : "本地"}
              模型。语义检索另用本机 Embedding
              索引；未配置时可用关键词。问题范围可能大于检索范围，回答不代表已穷尽所有会议。
            </small>
          )}
        </fieldset>
      </form>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {busy && <p role="status">正在处理…</p>}
      {run && (
        <section className="qa-run" aria-label="本次问答">
          <h3>{run.question}</h3>
          {run.stale ? (
            <p role="alert" className="health-warning">
              引用资料已修改或删除，旧回答已隐藏。请重新检索依据。
            </p>
          ) : !verified ? (
            <p role="alert">暂时无法核验来源，回答已隐藏，请恢复连接后重试。</p>
          ) : (
            <>
              <p>
                本次选用 {run.sources.length} 条依据，覆盖{" "}
                {new Set(run.sources.map((s) => s.meeting_id)).size}{" "}
                场会议；检索候选 {run.candidate_count} 条。使用{" "}
                {run.provider.name} · {run.provider.model}。
              </p>
              {run.pending_index > 0 && mode !== "keyword" && (
                <p>
                  有 {run.pending_index}{" "}
                  条资料尚未建立语义索引；可更新索引或改用关键词。
                </p>
              )}
              {run.sources.length === 0 ? (
                <p role="status">
                  依据不足，没有调用回答模型。可补充项目关键词、扩大会议范围或使用语义检索。
                </p>
              ) : (
                <>
                  <details className="qa-evidence" open={!run.answer}>
                    <summary>
                      查看本次将使用的依据（{run.evidence_chars} /{" "}
                      {run.context_budget} 字符预算）
                    </summary>
                    {run.sources.map((source) => (
                      <article key={source.label}>
                        <strong>
                          {source.label} · {source.title} · {source.date} · v
                          {source.revision}
                        </strong>
                        <small>
                          {source.kind === "transcript"
                            ? "文字稿"
                            : source.kind === "minutes"
                              ? "已保存纪要 · 派生记录"
                              : "简记线索 · 非会议共识"}
                          {source.note_scope === "personal"
                            ? " · 个人备注"
                            : ""}
                        </small>
                        <blockquote>{source.content}</blockquote>
                        <button
                          className="text-btn"
                          disabled={busy}
                          onClick={() => void act(() => openSource(source))}
                        >
                          {source.start !== null
                            ? clock(source.start) + " 打开并回听"
                            : "打开原文"}
                        </button>
                      </article>
                    ))}
                  </details>
                  {!run.job && (
                    <>
                      {run.provider.provider_type === "cloud" ? (
                        <label className="qa-consent">
                          <input
                            type="checkbox"
                            checked={cloud}
                            onChange={(e) => setCloud(e.target.checked)}
                          />
                          我确认将上述问题和预览的 {run.sources.length}{" "}
                          条资料发送到 {run.provider.name}（
                          {run.provider.base_url}）。
                        </label>
                      ) : (
                        <p>
                          问题和以上依据只发送到本机服务 {run.provider.base_url}
                          。
                        </p>
                      )}
                      <button
                        className="primary"
                        disabled={
                          busy ||
                          (run.provider.provider_type === "cloud" && !cloud)
                        }
                        onClick={() =>
                          void act(async () => {
                            setRun(await api.qaGenerate(run.id, cloud));
                          })
                        }
                      >
                        2. 生成带引用回答
                      </button>
                    </>
                  )}
                </>
              )}
              {run.job && (
                <p role="status">
                  {run.job.stage} · {Math.round(run.job.progress * 100)}%{" "}
                  {active && (
                    <button
                      disabled={busy}
                      onClick={() =>
                        void act(async () => {
                          await api.jobAction(run.job!.id, "cancel");
                          setRun(await api.qaRun(run.id));
                        })
                      }
                    >
                      取消问答
                    </button>
                  )}
                </p>
              )}
              {run.job?.state === "failed" && (
                <p className="error">{run.job.error}</p>
              )}
              {run.answer?.status === "insufficient" && (
                <p role="status">
                  当前检索依据不足以回答，没有形成确定结论。可调整检索词或范围后重新提问。
                </p>
              )}
              {run.answer?.status === "answered" && (
                <section className="qa-answer" aria-label="带引用回答">
                  <h3>回答</h3>
                  {run.answer.claims.map((claim, index) => (
                    <article key={index}>
                      <span className="qa-kind">
                        {
                          {
                            record: "资料明确记载",
                            inference: "AI 推断，需核对",
                            note: "简记线索，非会议共识",
                          }[claim.kind]
                        }
                      </span>
                      <p>{claim.text}</p>
                      {claim.citations.map((citation, i) => {
                        const source = run.sources.find(
                          (s) => s.label === citation.source,
                        );
                        return source ? (
                          <div className="qa-citation" key={i}>
                            <blockquote>“{citation.quote}”</blockquote>
                            <button
                              className="text-btn"
                              disabled={busy}
                              onClick={() => void act(() => openSource(source))}
                            >
                              {source.label} · {source.title} · {source.date}
                              {source.note_scope === "personal"
                                ? " · 个人备注"
                                : ""}{" "}
                              ·{" "}
                              {source.start !== null
                                ? clock(source.start)
                                : "原文"}{" "}
                              核对
                            </button>
                          </div>
                        ) : null;
                      })}
                    </article>
                  ))}
                  <small>
                    已核验引用
                    ID、连续摘录和来源版本；这不能机械保证结论得到充分支持，请回听关键内容。
                  </small>
                </section>
              )}
            </>
          )}
          <small>
            问答与转写共用处理队列。关闭窗口后任务继续，可从记录返回；失败或取消后请重新检索再提问。
          </small>
        </section>
      )}
      <details className="qa-history">
        <summary>本机问答记录 · 最近 {history.length} 条</summary>
        {history.map((item) => (
          <div key={item.id}>
            <button
              className="text-btn"
              disabled={busy}
              onClick={() =>
                void act(async () => {
                  invalidate();
                  const next = await api.qaRun(item.id);
                  setQuestion(next.question);
                  setQuery(next.user_query);
                  setMode(next.mode);
                  setMeetingId(next.meeting_id || "");
                  setKind(next.kind || "");
                  setPersonal(next.include_personal);
                  setProfileId(next.profile_id);
                  setRun(next);
                  setCloud(false);
                  setVerified(true);
                })
              }
            >
              {item.question}
            </button>
            <button
              className="text-btn danger"
              disabled={busy}
              onClick={() => {
                if (confirm("删除这条问答记录？不会删除会议。"))
                  void act(async () => {
                    await api.qaDelete(item.id);
                    if (run?.id === item.id) invalidate();
                    await refreshHistory();
                  });
              }}
            >
              删除
            </button>
          </div>
        ))}
      </details>
    </section>
  );
}
