import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api";
import {
  readRecordingPreferences,
  saveRecordingPreferences,
  type RecordingPreferences,
} from "../notes/recordingPreferences";
import WorkspaceReadinessPanel from "./WorkspaceReadinessPanel";
import QualitySamplesPanel from "./QualitySamplesPanel";
import QualityBaselinePanel from "./QualityBaselinePanel";
import ReleaseReadinessPanel from "./ReleaseReadinessPanel";
import ServiceLifecyclePanel from "./ServiceLifecyclePanel";
import RecordingValidationPanel from "./RecordingValidationPanel";
import type { RuntimeOverview, ManagementPage } from "./types";

const recordingDefaults = [
  ["keepAwake", "录音期间保持系统唤醒", "防止屏幕休眠或系统挂起导致录音中断"],
  ["silenceReminder", "持续静音检测提醒", "连续静音 10 秒以上时在状态栏提示"],
  ["streamEnabled", "Qwen 实时流式输出", "开启后逐字打字机推送到实时界面"],
  ["responsiveMode", "短片段极速预览", "提前返回 0.5s 快速预览并支持尾部修订"],
  ["prefixEnabled", "上下文稳定前缀锁定", "提升长时间连续会议的上下文连贯度"],
  ["contextEnabled", "会议准备提纲辅助", "使用会前填写的议程与目标纠偏专有名词"],
  ["correctionEnabled", "本地模型实时校对", "在后台使用小型本地模型校对已转写片段"],
] as const;

function RecordingDefaults() {
  const [preferences, setPreferences] = useState(() =>
    readRecordingPreferences(window.localStorage),
  );
  const [saveError, setSaveError] = useState(false);
  const { data: environment } = useQuery({
    queryKey: ["environment"],
    queryFn: api.environment,
    enabled: preferences.correctionEnabled,
  });
  const localProfiles =
    environment?.analysis.profiles.filter((profile) => profile.provider_type === "local") || [];

  function update(patch: Partial<RecordingPreferences>) {
    const next = { ...readRecordingPreferences(window.localStorage), ...patch };
    const saved = saveRecordingPreferences(window.localStorage, next);
    setSaveError(!saved);
    if (saved) setPreferences(next);
  }

  return (
    <section className="management-section">
      <div className="section-title-row">
        <div>
          <h3>🎛️ 录音与识别偏好配置</h3>
          <p className="section-desc">
            应用于本机发起的录音会话。音源与交付目标可在每次会前就绪舱中即时调整。
          </p>
        </div>
      </div>

      <div className="pref-toggle-list">
        {recordingDefaults.map(([key, label, desc]) => (
          <label className="pref-toggle-item" key={key}>
            <div className="pref-toggle-info">
              <strong>{label}</strong>
              <small>{desc}</small>
            </div>
            <input
              type="checkbox"
              className="pref-switch"
              checked={preferences[key as keyof RecordingPreferences] as boolean}
              onChange={(event) => update({ [key]: event.target.checked })}
            />
          </label>
        ))}
      </div>

      {preferences.correctionEnabled && (
        <div className="management-row pref-extra-row">
          <div className="pref-row-label">
            <strong>智能校对模型</strong>
            <small>选择用于后台静默纠错的本地模型</small>
          </div>
          <select
            className="pref-select"
            value={preferences.correctionProfileId}
            onChange={(event) => update({ correctionProfileId: event.target.value })}
          >
            <option value="">请选择本地模型服务</option>
            {localProfiles.map((profile) => (
              <option key={profile.id} value={profile.id}>
                {profile.name}
              </option>
            ))}
          </select>
        </div>
      )}

      <div className="management-row pref-extra-row">
        <div className="pref-row-label">
          <strong>通用常驻参考词 (Hotwords)</strong>
          <small>英文缩写、公司术语或业务专有名词（逗号或空格分隔）</small>
        </div>
        <input
          className="pref-input"
          placeholder="例如：Kubernetes, ROI, 增长飞轮"
          value={preferences.recognitionTerms}
          maxLength={1000}
          onChange={(event) => update({ recognitionTerms: event.target.value })}
        />
      </div>

      {saveError && (
        <p role="alert" className="management-error-inline">
          当前环境无法保存录音偏好，请检查浏览器 Storage 权限。
        </p>
      )}
    </section>
  );
}

const bytes = (n: number | null) =>
  n === null ? "未知" : `${(n / 1024 ** 3).toFixed(1)} GB`;

export default function RuntimeEnvironmentPage({
  data,
}: {
  data: RuntimeOverview;
  onPage?: (page: ManagementPage) => void;
}) {
  const [advanced, setAdvanced] = useState(false);
  const [copied, setCopied] = useState(false);

  const usedBytes =
    data.storage.total_bytes && data.storage.free_bytes !== null
      ? Math.max(0, data.storage.total_bytes - data.storage.free_bytes)
      : null;

  const usedPercent =
    data.storage.total_bytes && usedBytes !== null
      ? Math.min(100, Math.round((usedBytes / data.storage.total_bytes) * 100))
      : 0;

  const copyPath = () => {
    void navigator.clipboard.writeText(data.storage.data_dir).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  return (
    <div className="runtime-environment-container">
      {/* 采集授权与系统权限 */}
      <section className="management-section">
        <div className="section-title-row">
          <div>
            <h3>🔐 系统授权与音频采集</h3>
            <p className="section-desc">macOS 屏幕声音采集与麦克风录制权限状态</p>
          </div>
        </div>

        <div className="env-status-grid">
          <div className="env-status-item">
            <div className="env-status-info">
              <span className="env-status-icon">🖥️</span>
              <div>
                <strong>屏幕与系统声音录制</strong>
                <small>用于内录腾讯会议、飞书、Zoom 或网页声音</small>
              </div>
            </div>
            <span
              className={`status-pill ${
                data.native.screen_permission ? "success" : "warning"
              }`}
            >
              {data.native.screen_permission ? "✓ 已授权" : "未授权 · 需系统允许"}
            </span>
          </div>

          <div className="env-status-item">
            <div className="env-status-info">
              <span className="env-status-icon">🎙️</span>
              <div>
                <strong>麦克风设备访问</strong>
                <small>用于采录本机发言与线下现场会议声音</small>
              </div>
            </div>
            <span
              className={`status-pill ${
                data.native.microphone_permission ? "success" : "warning"
              }`}
            >
              {data.native.microphone_permission ? "✓ 已授权" : "未授权 · 需系统允许"}
            </span>
          </div>

          <div className="env-status-item">
            <div className="env-status-info">
              <span className="env-status-icon">⚙️</span>
              <div>
                <strong>本机原生采集守护进程</strong>
                <small>独立二进制沙箱采集器，低延迟多音轨独立保存</small>
              </div>
            </div>
            <span
              className={`status-pill ${
                data.native.available ? "success" : "danger"
              }`}
            >
              {data.native.available ? "✓ 采集器就绪" : "未构建或不可用"}
            </span>
          </div>
        </div>
      </section>

      {/* 媒体处理依赖 */}
      <section className="management-section">
        <div className="section-title-row">
          <div>
            <h3>📦 媒体与模型处理依赖</h3>
            <p className="section-desc">音频格式转换、声学分析与标点恢复组件</p>
          </div>
        </div>

        <div className="env-status-grid">
          {(
            [
              ["ffmpeg", "FFmpeg 多媒体转码库", "音频切片、重采样与双轨合成", "🎵"],
              ["diarization", "声学发言人分离模型", "根据声纹特征切分不同发言人", "👥"],
              ["punctuation", "中文自然标点恢复", "离线实时断句与中英文标点修正", "📝"],
            ] as const
          ).map(([key, label, desc, icon]) => (
            <div className="env-status-item" key={key}>
              <div className="env-status-info">
                <span className="env-status-icon">{icon}</span>
                <div>
                  <strong>{label}</strong>
                  <small>{desc}</small>
                </div>
              </div>
              <span
                className={`status-pill ${
                  data.dependencies[key] ? "success" : "neutral"
                }`}
              >
                {data.dependencies[key] ? "✓ 依赖已就绪" : "待安装或可选"}
              </span>
            </div>
          ))}
        </div>
      </section>

      {/* 数据存储空间 */}
      <section className="management-section">
        <div className="section-title-row">
          <div>
            <h3>💾 数据存储与磁盘占用</h3>
            <p className="section-desc">录音原始音频、文字稿、AI 纪要与数据库位置</p>
          </div>
        </div>

        <div className="storage-card">
          <div className="storage-path-box">
            <div className="path-text-group">
              <span className="path-label">本地数据目录</span>
              <code className="path-code">{data.storage.data_dir}</code>
            </div>
            <button
              type="button"
              className="btn-copy-path"
              onClick={copyPath}
              title="复制完整路径"
            >
              {copied ? "✓ 已复制" : "📋 复制路径"}
            </button>
          </div>

          <div className="storage-metrics-row">
            <div className="storage-metric-col">
              <span className="metric-sub">目录写权限</span>
              <strong className={data.storage.writable ? "text-success" : "text-danger"}>
                {data.storage.writable ? "✓ 可正常读写" : "✕ 不可写 (权限受阻)"}
              </strong>
            </div>
            <div className="storage-metric-col">
              <span className="metric-sub">数据盘剩余容量</span>
              <strong>{bytes(data.storage.free_bytes)}</strong>
            </div>
            <div className="storage-metric-col">
              <span className="metric-sub">磁盘总容量</span>
              <strong>{bytes(data.storage.total_bytes)}</strong>
            </div>
          </div>

          {data.storage.total_bytes !== null && (
            <div className="storage-progress-container">
              <div className="storage-progress-track">
                <div
                  className="storage-progress-fill"
                  style={{ width: `${usedPercent}%` }}
                />
              </div>
              <div className="storage-progress-labels">
                <small>已使用空间约 {usedPercent}%</small>
                <small>剩余 {bytes(data.storage.free_bytes)} 可用</small>
              </div>
            </div>
          )}
        </div>
      </section>

      {/* 录音偏好配置 */}
      <RecordingDefaults />

      {/* 开发者与系统验收诊断（默认折叠收拢） */}
      <details
        className="management-advanced-accordion"
        onToggle={(event) => setAdvanced(event.currentTarget.open)}
      >
        <summary className="advanced-accordion-summary">
          <div className="summary-left">
            <span className="summary-icon">🛠️</span>
            <div>
              <strong>开发者与系统验收诊断</strong>
              <small>包含基线评测、发布就绪检查、质量样本及录音契约校验</small>
            </div>
          </div>
          <span className="summary-arrow">{advanced ? "收起 ▲" : "展开 ▼"}</span>
        </summary>

        {advanced && (
          <div className="advanced-accordion-body">
            <WorkspaceReadinessPanel />
            <ReleaseReadinessPanel />
            <QualityBaselinePanel />
            <QualitySamplesPanel />
            <ServiceLifecyclePanel />
            <RecordingValidationPanel />
          </div>
        )}
      </details>
    </div>
  );
}
