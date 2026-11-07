import { useEffect, useRef, useState } from "react";
import { api } from "../../api";
import { semanticLanguageHint } from "../retrievalLanguage";
import type { LLMConfig } from "../../types";
import type { SearchStatus } from "../SearchDialog";
export default function IndexManagement({
  onTasks,
  active: activePage,
  onDirtyChange,
}: {
  onTasks: () => void;
  active: boolean;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const visible = useRef(activePage);
  visible.current = activePage;
  const [status, setStatus] = useState<SearchStatus | null>(null),
    [profiles, setProfiles] = useState<LLMConfig[]>([]),
    [profileId, setProfileId] = useState(""),
    [model, setModel] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [dirty, setDirty] = useState(false);
  useEffect(() => onDirtyChange(dirty), [dirty, onDirtyChange]);
  const active =
    status?.job && ["queued", "running"].includes(status.job.state);
  const poll = async () => setStatus(await api.searchStatus());
  useEffect(() => {
    let live = true;
    void Promise.all([api.searchStatus(), api.configs()])
      .then(([s, configs]) => {
        if (!live) return;
        setStatus(s);
        const local = configs.filter((p) => p.provider_type === "local");
        setProfiles(local);
        setProfileId(s.config?.profile_id || local[0]?.id || "");
        setModel(s.config?.model || "");
      })
      .catch((e) => live && setError(e.message));
    const timer = window.setInterval(() => {
      if (visible.current) void poll().catch(() => {});
    }, 5000);
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
  return (
    <>
      <p className="muted">
        这里维护语义检索能力；关键词搜索始终可用。实时识别期间，索引任务排队等待。
      </p>
      {error && (
        <p role="alert" className="management-error">
          {error}
        </p>
      )}
      <section className="management-section search-index">
        <h3>
          本地语义索引 · {status?.indexed || 0}/{status?.units || 0} 已索引
        </h3>
        <p className="muted">
          {semanticLanguageHint(status?.language_support)}
        </p>
        <section className="builtin-embedding" aria-label="内置中文检索模型">
          <h3>内置中文检索 · BGE-small INT8</h3>
          <p>
            约 24 MB，安装后无需 LM Studio，检索文本仅在本机处理。首次下载访问
            Hugging Face，也可导入离线 ZIP 包。
          </p>
          {status?.builtin ? (
            <>
              <p role="status">
                {status.builtin.installed ? "模型已安装" : "尚未安装"} ·{" "}
                {status.builtin.loaded ? "已加载" : "按需加载"}
                {["downloading", "importing"].includes(status.builtin.state) &&
                  ` · 安装中 ${Math.round((status.builtin.downloaded / Math.max(1, status.builtin.total)) * 100)}%`}
              </p>
              {!status.builtin.runtime_ready && (
                <p role="alert">
                  运行依赖未就绪，请运行 scripts/setup.sh 更新依赖。
                </p>
              )}
              {status.builtin.error && (
                <p role="alert">{status.builtin.error}</p>
              )}
              <div className="search-index-actions">
                <button
                  disabled={
                    busy ||
                    ["downloading", "importing"].includes(
                      status.builtin.state,
                    ) ||
                    status.builtin.installed
                  }
                  onClick={() => void action(() => api.installEmbedding())}
                >
                  下载安装模型
                </button>
                {status.builtin.state === "downloading" && (
                  <button
                    onClick={() =>
                      void action(() => api.cancelEmbeddingInstall())
                    }
                  >
                    取消下载
                  </button>
                )}
                <label>
                  导入离线包{" "}
                  <input
                    type="file"
                    accept=".zip"
                    aria-label="导入向量模型离线包"
                    disabled={
                      busy ||
                      ["downloading", "importing"].includes(
                        status.builtin.state,
                      )
                    }
                    onChange={(event) => {
                      const file = event.target.files?.[0];
                      event.target.value = "";
                      if (file) void action(() => api.importEmbedding(file));
                    }}
                  />
                </label>
                <button
                  disabled={
                    busy ||
                    !!active ||
                    !status.builtin.installed ||
                    !status.builtin.runtime_ready ||
                    ["downloading", "importing"].includes(status.builtin.state)
                  }
                  onClick={() =>
                    void action(() => api.configureBuiltinSearch())
                  }
                >
                  启用内置模型
                </button>
              </div>
            </>
          ) : (
            <p>请在录音和处理任务结束后更新服务，使用内置模型管理。</p>
          )}
        </section>
        <details>
          <summary>高级：使用 LM Studio 等本机向量服务</summary>
          <p>
            选择支持 /embeddings
            的模型。英文或中英跨语言检索应选择支持相应语言的 Embedding
            模型；模型可用不等于跨语言质量已验证。数据仅发送到所选本机服务。
          </p>
          <label>
            本机服务{" "}
            <select
              aria-label="向量模型本机服务"
              disabled={busy}
              value={profileId}
              onChange={(event) => {
                setProfileId(event.target.value);
                setDirty(true);
              }}
            >
              {!profiles.length && (
                <option value="">请先添加本地模型配置</option>
              )}
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
              disabled={busy}
              value={model}
              onChange={(event) => {
                setModel(event.target.value);
                setDirty(true);
              }}
              placeholder="填写 LM Studio 中的向量模型标识"
            />
          </label>
          <div className="search-index-actions">
            <button
              disabled={busy || !!active || !profileId || !model.trim()}
              onClick={() =>
                void action(async () => {
                  await api.configureSearch(profileId, model.trim());
                  setDirty(false);
                })
              }
            >
              测试并保存
            </button>
          </div>
        </details>
        <p>
          启用或更换模型后请更新索引；不会自动替换已有配置。已有模型同名权重变化时使用“全部重建”。
        </p>
        <div className="search-index-actions">
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
          <p role="alert">
            索引任务失败，已保存的会议资料保留。
            <button onClick={onTasks}>查看任务与诊断</button>
          </p>
        )}
      </section>
    </>
  );
}
