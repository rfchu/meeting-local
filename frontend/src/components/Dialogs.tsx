import { useEffect, useState } from "react";
import { api } from "../api";
import type {
  Environment,
  LLMConfig,
  LLMConfigInput,
  MeetingMinutes,
} from "../types";
import Modal from "./Modal";

export function RenameDialog({
  title,
  onClose,
  onSave,
}: {
  title: string;
  onClose: () => void;
  onSave: (title: string) => Promise<void>;
}) {
  const [value, setValue] = useState(title),
    [saving, setSaving] = useState(false);
  return (
    <Modal id="renameDialog" onClose={onClose}>
      <form
        id="renameForm"
        onSubmit={async (e) => {
          e.preventDefault();
          if (!value.trim()) return;
          setSaving(true);
          try {
            await onSave(value.trim());
          } finally {
            setSaving(false);
          }
        }}
      >
        <h2>重命名会议</h2>
        <label>
          会议名称
          <input
            autoFocus
            required
            maxLength={200}
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
        </label>
        <div className="config-actions">
          <button type="button" onClick={onClose}>
            取消
          </button>
          <button className="primary" disabled={saving}>
            {saving ? "保存中…" : "保存"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

export function EnvironmentDialog({
  environment,
  onClose,
}: {
  environment: Environment;
  onClose: () => void;
}) {
  const lines = [
    ...environment.engines.map(
      (item) => `${item.label}：${item.ready ? "已就绪" : "待安装"}`,
    ),
    `音频处理：${environment.ffmpeg_ready ? "FFmpeg 已就绪" : "缺少 FFmpeg"}`,
    `自动发言人分析：${environment.diarization_ready ? "已就绪" : "待配置"}`,
    `中文标点恢复：${environment.punctuation_ready ? "已就绪" : "待配置"}`,
    `LLM 分析配置：${environment.analysis.profiles.length} 个`,
    `数据目录：${environment.data_dir}`,
  ];
  return (
    <Modal onClose={onClose}>
      <h2>本机运行环境</h2>
      <div>
        {lines.map((line) => (
          <p key={line}>{line}</p>
        ))}
        <p>语音转写始终在本机运行；会议分析是否联网取决于所选 LLM 配置。</p>
      </div>
      <button className="primary" onClick={onClose}>
        知道了
      </button>
    </Modal>
  );
}

const emptyConfig = (hasConfigs: boolean): LLMConfigInput => ({
  name: "本机 LM Studio",
  provider_type: "local",
  base_url: "http://127.0.0.1:1234/v1",
  model: "",
  context_window: 32768,
  analysis_chunk_mode: "auto",
  analysis_chunk_chars: 0,
  api_key: "",
  is_default: !hasConfigs,
});

const safeChunkChars = (contextWindow: number) =>
  Math.max(
    8000,
    Math.floor(((contextWindow * 0.6 - 8192) * 0.75) / 1000) * 1000,
  );

const estimatedChunkChars = (form: LLMConfigInput) => {
  const safe = safeChunkChars(form.context_window);
  if (form.analysis_chunk_mode === "quality") return Math.min(safe, 48000);
  if (form.analysis_chunk_mode === "speed") return Math.min(safe, 96000);
  if (form.analysis_chunk_mode === "custom")
    return form.analysis_chunk_chars || 8000;
  const cap =
    form.context_window <= 262144
      ? 48000
      : form.context_window <= 524288
        ? 64000
        : 96000;
  return Math.min(safe, cap);
};
export function ModelConfigDialog({
  onClose,
  onChanged,
  toast,
}: {
  onClose: () => void;
  onChanged: () => void;
  toast: (text: string) => void;
}) {
  const [configs, setConfigs] = useState<LLMConfig[]>([]),
    [form, setForm] = useState<LLMConfigInput>(emptyConfig(false)),
    [result, setResult] = useState(""),
    [busy, setBusy] = useState(false);
  const refresh = async () => {
    const values = await api.configs();
    setConfigs(values);
    if (values.length)
      select(values.find((item) => item.is_default) || values[0]);
    else setForm(emptyConfig(false));
  };
  useEffect(() => {
    void refresh();
  }, []);
  const select = (config: LLMConfig) =>
    setForm({
      config_id: config.id,
      name: config.name,
      provider_type: config.provider_type,
      base_url: config.base_url,
      model: config.model,
      context_window: config.context_window,
      analysis_chunk_mode: config.analysis_chunk_mode,
      analysis_chunk_chars: config.analysis_chunk_chars,
      api_key: "",
      is_default: config.is_default,
    });
  const field = <K extends keyof LLMConfigInput>(
    key: K,
    value: LLMConfigInput[K],
  ) => setForm((old) => ({ ...old, [key]: value }));
  return (
    <Modal id="modelConfigDialog" onClose={onClose}>
      <div className="config-header">
        <div>
          <h2>LLM 模型配置</h2>
          <p>会议文字稿只会发送到你选择的分析接口。</p>
        </div>
        <button className="text-btn" onClick={onClose}>
          关闭
        </button>
      </div>
      <div className="config-layout">
        <section>
          <div className="config-list-head">
            <b>配置列表</b>
            <button
              onClick={() => {
                setForm(emptyConfig(configs.length > 0));
                setResult("");
              }}
            >
              ＋ 新建
            </button>
          </div>
          <div>
            {!configs.length && <p className="muted">还没有配置</p>}
            {configs.map((item) => (
              <button
                key={item.id}
                className={`config-item${form.config_id === item.id ? " active" : ""}`}
                onClick={() => select(item)}
              >
                <b>
                  {item.is_default ? "默认 · " : ""}
                  {item.name}
                </b>
                <small>
                  {item.provider_type === "local" ? "本地" : "云端"} ·{" "}
                  {item.model}
                </small>
              </button>
            ))}
          </div>
        </section>
        <form
          id="modelConfigForm"
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            try {
              const saved = await api.saveConfig(form);
              await refresh();
              select(saved);
              onChanged();
              toast("模型配置已保存");
            } catch (error) {
              toast((error as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <label>
            配置名称
            <input
              required
              maxLength={100}
              value={form.name}
              onChange={(e) => field("name", e.target.value)}
            />
          </label>
          <label>
            模型位置
            <select
              value={form.provider_type}
              onChange={(e) => {
                const local = e.target.value === "local";
                setForm((old) => ({
                  ...old,
                  provider_type: local ? "local" : "cloud",
                  base_url: local
                    ? "http://127.0.0.1:1234/v1"
                    : "https://api.openai.com/v1",
                  name: old.config_id
                    ? old.name
                    : local
                      ? "本机 LM Studio"
                      : "云端模型",
                }));
              }}
            >
              <option value="local">本地模型</option>
              <option value="cloud">云端模型</option>
            </select>
          </label>
          <label>
            接口地址
            <input
              required
              value={form.base_url}
              onChange={(e) => field("base_url", e.target.value)}
            />
          </label>
          <label>
            模型名称
            <input
              required
              placeholder="例如：qwen3-8b"
              value={form.model}
              onChange={(e) => field("model", e.target.value)}
            />
          </label>
          <label>
            上下文窗口（tokens）
            <input
              type="number"
              required
              min={4096}
              max={2000000}
              step={1024}
              value={form.context_window}
              onChange={(e) => field("context_window", Number(e.target.value))}
            />
          </label>
          <small>测试连接会在接口提供元数据时自动回填。</small>
          <details>
            <summary>高级设置</summary>
            <label>
              最大分析分块
              <select
                value={form.analysis_chunk_mode}
                onChange={(e) => {
                  const mode = e.target
                    .value as LLMConfigInput["analysis_chunk_mode"];
                  setForm((old) => ({
                    ...old,
                    analysis_chunk_mode: mode,
                    analysis_chunk_chars:
                      mode === "custom" && !old.analysis_chunk_chars
                        ? Math.min(48000, safeChunkChars(old.context_window))
                        : old.analysis_chunk_chars,
                  }));
                }}
              >
                <option value="auto">自动 · 按上下文窗口调整</option>
                <option value="quality">质量优先 · 最高 48,000 字符</option>
                <option value="speed">速度优先 · 最高 96,000 字符</option>
                <option value="custom">自定义</option>
              </select>
            </label>
            {form.analysis_chunk_mode === "custom" && (
              <label>
                自定义字符数
                <input
                  type="number"
                  required
                  min={8000}
                  max={safeChunkChars(form.context_window)}
                  step={1000}
                  value={form.analysis_chunk_chars || 8000}
                  onChange={(e) =>
                    field("analysis_chunk_chars", Number(e.target.value))
                  }
                />
              </label>
            )}
            <small>
              当前将按约 {estimatedChunkChars(form).toLocaleString()}{" "}
              个字符分块；当前上下文的安全预算不超过{" "}
              {safeChunkChars(form.context_window).toLocaleString()}{" "}
              字符。自动模式在 256K、512K 和更长上下文下最高分别使用 48K、64K 和
              96K 字符。
            </small>
          </details>
          <label>
            API Key
            <input
              type="password"
              autoComplete="new-password"
              value={form.api_key || ""}
              onChange={(e) => field("api_key", e.target.value)}
            />
          </label>
          <small>
            {form.config_id
              ? "留空将保持已保存的 API Key 不变。"
              : "密钥只保存在本机数据库，不会回显。"}
          </small>
          <label className="config-check">
            <input
              type="checkbox"
              checked={form.is_default}
              onChange={(e) => field("is_default", e.target.checked)}
            />{" "}
            设为默认分析模型
          </label>
          <div className="config-result">{result}</div>
          <div className="config-actions">
            {form.config_id && (
              <button
                type="button"
                className="danger"
                onClick={async () => {
                  if (!confirm("删除这个模型配置？")) return;
                  try {
                    await api.deleteConfig(form.config_id!);
                    await refresh();
                    onChanged();
                    toast("模型配置已删除");
                  } catch (error) {
                    toast((error as Error).message);
                  }
                }}
              >
                删除
              </button>
            )}
            <button
              type="button"
              onClick={async () => {
                setResult("正在连接模型服务…");
                try {
                  const value = await api.testConfig(form);
                  if (value.detected_context_window)
                    field("context_window", value.detected_context_window);
                  setResult(
                    value.model_available
                      ? value.detected_context_window
                        ? `连接成功，已自动识别 ${value.detected_context_window.toLocaleString()} tokens 上下文窗口；分析分块约 ${value.analysis_max_chars.toLocaleString()} 字符`
                        : `连接成功，已找到模型；接口未返回上下文长度，继续使用填写值 ${value.effective_context_window.toLocaleString()} tokens`
                      : `连接成功，但模型列表中没有“${form.model}”`,
                  );
                } catch (error) {
                  setResult((error as Error).message);
                }
              }}
            >
              测试连接
            </button>
            <button className="primary" disabled={busy}>
              保存配置
            </button>
          </div>
        </form>
      </div>
    </Modal>
  );
}

export function MinutesPreview({
  draft,
  onClose,
  onExport,
}: {
  draft: MeetingMinutes;
  onClose: () => void;
  onExport: (format: "docx" | "pdf") => void;
}) {
  const labels: Record<string, string> = {
    overview: "核心内容纪要",
    conclusions: "会议总结",
    decisions: "决策事项",
    action_items: "待办事项（Action Items）",
    open_questions: "风险与待确认",
  };
  const sections =
    draft.style === "ai"
      ? ["overview", "action_items", "conclusions"]
      : draft.style === "brief"
        ? ["overview", "decisions", "action_items", "open_questions"]
        : [
            "overview",
            "conclusions",
            "decisions",
            "action_items",
            "open_questions",
          ];
  return (
    <Modal id="minutesPreviewDialog" onClose={onClose}>
      <div className="preview-header">
        <div>
          <h2>纪要导出预览</h2>
          <p>预览内容与当前保存的纪要一致。</p>
        </div>
        <button className="text-btn" onClick={onClose}>
          关闭
        </button>
      </div>
      <div id="minutesPreviewContent">
        <div className={`minutes-document minutes-${draft.style}`}>
          <div className="minutes-style-badge">会议纪要</div>
          <h1 className="minutes-title">{draft.title}</h1>
          <div className="minutes-meta">
            {[
              ["日期", draft.meeting_date],
              ["会议形式", draft.meeting_form],
              ["地点", draft.location],
              ["参会人", draft.attendees],
              ["记录人", draft.recorder],
            ].map(([label, value]) => (
              <div key={label} className="preview-value">
                <b>{label}</b>　{value || "待确认"}
              </div>
            ))}
          </div>
          <div className="minutes-purpose preview-value">
            <b>会议目的</b>
            <br />
            {draft.purpose}
          </div>
          {sections.map((section) => (
            <section className="minutes-section" key={section}>
              <h3>{labels[section]}</h3>
              {(
                draft[section as keyof MeetingMinutes] as unknown as {
                  content: string;
                  owner?: string | null;
                  due_date?: string | null;
                }[]
              ).map((item, index) => (
                <div className="preview-value" key={index}>
                  {index + 1}. {item.content}
                  {item.owner
                    ? `（负责人：${item.owner}${item.due_date ? `；截止：${item.due_date}` : ""}）`
                    : ""}
                </div>
              ))}
            </section>
          ))}
        </div>
      </div>
      <div className="preview-actions">
        <button onClick={() => onExport("docx")}>导出 Word</button>
        <button className="primary" onClick={() => onExport("pdf")}>
          导出 PDF
        </button>
      </div>
    </Modal>
  );
}
