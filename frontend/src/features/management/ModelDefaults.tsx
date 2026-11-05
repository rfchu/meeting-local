import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  loadModelPreferences,
  saveModelPreference,
} from "../../preferences/useModelChoice";
import type { ModelChoice } from "../../preferences/modelChoices";
const labels: [ModelChoice, string][] = [
  ["live_transcription", "实时语音识别"],
  ["transcription", "精细语音转写"],
  ["overview", "会中 AI 概览"],
  ["correction", "实时智能纠错"],
  ["minutes", "AI 会议纪要"],
  ["analysis", "会议分析"],
  ["notes", "简记整理"],
  ["speakers", "内容发言人复核"],
  ["terminology", "术语提取"],
  ["qa", "跨会议引用问答"],
];
export default function ModelDefaults() {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: ["model-preferences"],
    queryFn: loadModelPreferences,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const data = query.data;
  const name = (role: ModelChoice, value: string) =>
    !value
      ? "未设置"
      : role.includes("transcription")
        ? value === "qwen"
          ? "Qwen-ASR"
          : value === "whisper"
            ? "Whisper"
            : "已失效引擎"
        : data?.profiles.find((p) => p.id === value)?.name || "已删除的配置";
  async function save(
    role: ModelChoice,
    kind: "default" | "last",
    value: string,
  ) {
    if (!data) return;
    setBusy(true);
    setError("");
    try {
      const result = await saveModelPreference(
        role,
        kind,
        value,
        data.roles[role].version,
      );
      client.setQueryData(["model-preferences"], result);
    } catch (e) {
      setError((e as Error).message);
      void query.refetch();
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="management-defaults">
      <p className="management-notice">
        选择顺序：本场明确选择 → 上次有效选择 →
        功能默认。修改默认后，已有上次选择仍优先；可清除上次选择以改用默认。已打开功能的本场选择及已排队任务保持原模型。
      </p>
      <p className="muted">
        选择和默认方案保存在本机工作空间，浏览器旧选择会在首次升级时导入。此处不会启用功能或授权云端发送。
      </p>
      {(error || query.error) && (
        <p role="alert" className="management-error">
          {error || query.error?.message}
        </p>
      )}
      {!data && <p role="status">正在读取模型方案…</p>}
      {data &&
        labels.map(([role, label]) => {
          const value = data.roles[role];
          const engine = role.includes("transcription");
          return (
            <article className="management-section" key={role}>
              <div className="management-default-heading">
                <h3>{label}</h3>
                <span>
                  当前建议：{name(role, value.effective_value)}{" "}
                  <small>
                    ·{" "}
                    {
                      (
                        {
                          last: "上次选择",
                          default: "功能默认",
                          local_fallback: "本地回退",
                          unset: "尚未设置",
                        } as Record<string, string>
                      )[value.source]
                    }
                  </small>
                </span>
              </div>
              <div className="management-default-controls">
                <label>
                  功能默认
                  <select
                    aria-label={`${label}默认模型`}
                    value={value.default_value}
                    disabled={busy}
                    onChange={(e) => void save(role, "default", e.target.value)}
                  >
                    <option value="">使用本地回退</option>
                    {value.invalid_default && (
                      <option value={value.default_value}>已失效配置</option>
                    )}
                    {engine ? (
                      <>
                        <option value="whisper">Whisper</option>
                        <option value="qwen">Qwen-ASR</option>
                      </>
                    ) : (
                      data.profiles.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name} ·{" "}
                          {p.provider_type === "cloud" ? "云端" : "本地"}
                        </option>
                      ))
                    )}
                  </select>
                </label>
                <div>
                  <span>上次选择：{name(role, value.last_value)}</span>
                  <button
                    className="text-btn"
                    disabled={busy || !value.last_value}
                    onClick={() => void save(role, "last", "")}
                  >
                    清除上次选择
                  </button>
                </div>
              </div>
              {(value.invalid_last || value.invalid_default) && (
                <p className="management-warning">
                  原模型已失效，请重新选择；不会自动改用云端。
                </p>
              )}
            </article>
          );
        })}
    </section>
  );
}
