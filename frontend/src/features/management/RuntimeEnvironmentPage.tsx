import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api";
import {
  readRecordingPreferences,
  saveRecordingPreferences,
  type RecordingPreferences,
} from "../notes/recordingPreferences";
import RecordingReadiness from "../onboarding/RecordingReadiness";
import WorkspaceReadinessPanel from "./WorkspaceReadinessPanel";
import QualitySamplesPanel from "./QualitySamplesPanel";
import QualityBaselinePanel from "./QualityBaselinePanel";
import ReleaseReadinessPanel from "./ReleaseReadinessPanel";
import ServiceLifecyclePanel from "./ServiceLifecyclePanel";
import RecordingValidationPanel from "./RecordingValidationPanel";
import type { RuntimeOverview, ManagementPage } from "./types";

const recordingDefaults = [
  ["keepAwake", "录音期间保持系统唤醒"],
  ["silenceReminder", "持续静音时提醒"],
  ["streamEnabled", "Qwen 实时推送文字"],
  ["responsiveMode", "短片段预览与尾部修订"],
  ["prefixEnabled", "保留稳定前缀"],
  ["contextEnabled", "使用会议准备与前文辅助识别"],
  ["correctionEnabled", "用本地模型校对已确认的转写"],
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
  const localProfiles = environment?.analysis.profiles.filter((profile) => profile.provider_type === "local") || [];
  function update(patch: Partial<RecordingPreferences>) {
    const next = { ...readRecordingPreferences(window.localStorage), ...patch };
    const saved = saveRecordingPreferences(window.localStorage, next);
    setSaveError(!saved);
    if (saved) setPreferences(next);
  }
  return (
    <details className="management-section">
      <summary>录音与识别默认项</summary>
      <p className="muted">应用于下次开始的录音。音源、回声消除和本场交付目标在会前准备选择。</p>
      {recordingDefaults.map(([key, label]) => (
        <label className="management-row" key={key}>
          <span>{label}</span>
          <input
            type="checkbox"
            checked={preferences[key]}
            onChange={(event) => update({ [key]: event.target.checked })}
          />
        </label>
      ))}
      {preferences.correctionEnabled && (
        <label className="management-row">
          <span>校对模型</span>
          <select
            value={preferences.correctionProfileId}
            onChange={(event) => update({ correctionProfileId: event.target.value })}
          >
            <option value="">请选择本地模型</option>
            {localProfiles.map((profile) => (
              <option key={profile.id} value={profile.id}>{profile.name}</option>
            ))}
          </select>
        </label>
      )}
      <label className="management-row">
        <span>通用识别参考词</span>
        <input
          value={preferences.recognitionTerms}
          maxLength={1000}
          onChange={(event) => update({ recognitionTerms: event.target.value })}
        />
      </label>
      {saveError && <p role="alert">当前环境无法保存录音偏好。</p>}
    </details>
  );
}

const bytes = (n: number | null) =>
  n === null ? "暂不可用" : `${(n / 1024 ** 3).toFixed(1)} GB`;
export default function RuntimeEnvironmentPage({
  data,
  onPage,
}: {
  data: RuntimeOverview;
  onPage?: (page: ManagementPage) => void;
}) {
  const [advanced, setAdvanced] = useState(false);
  return (
    <>
      <section className="management-section readiness-management-wrapper">
        <RecordingReadiness
          source="macos-both"
          engine="qwen"
          onConfigure={() => onPage?.("models")}
          onModels={() => onPage?.("models")}
          onReadiness={() => {}}
        />
      </section>
      <RecordingDefaults />
      <WorkspaceReadinessPanel />
      <ReleaseReadinessPanel />
      <details
        className="management-section"
        onToggle={(event) => setAdvanced(event.currentTarget.open)}
      >
        <summary>高级诊断与评测</summary>
        {advanced && (
          <>
            <QualityBaselinePanel />
            <QualitySamplesPanel />
            <ServiceLifecyclePanel />
            <RecordingValidationPanel />
          </>
        )}
      </details>
      <section className="management-section">
        <h3>采集与权限</h3>
        <div className="management-row">
          <strong>本机音频采集</strong>
          <span>{data.native.available ? "采集器可用" : "采集器暂不可用"}</span>
        </div>
        <div className="management-row">
          <strong>屏幕与系统音频权限</strong>
          <span>
            {data.native.screen_permission ? "已允许" : "未允许或尚未确认"}
          </span>
        </div>
        <div className="management-row">
          <strong>麦克风权限</strong>
          <span>
            {data.native.microphone_permission ? "已允许" : "未允许或尚未确认"}
          </span>
        </div>
        <p className="muted">
          试录{data.capture_test_active ? "进行中" : "未运行"}
          。浏览器麦克风权限在开始录音或试录时确认。
        </p>
      </section>
      <section className="management-section">
        <h3>处理依赖</h3>
        {(
          [
            ["ffmpeg", "音频处理 FFmpeg"],
            ["diarization", "声学发言人分析"],
            ["punctuation", "中文标点恢复"],
          ] as const
        ).map(([key, label]) => (
          <div className="management-row" key={key}>
            <strong>{label}</strong>
            <span>
              {data.dependencies[key] ? "依赖已就绪" : "待安装或配置"}
            </span>
          </div>
        ))}
      </section>
      <section className="management-section">
        <h3>数据存储</h3>
        <p className="management-path">{data.storage.data_dir}</p>
        <div className="management-row">
          <strong>目录权限</strong>
          <span>{data.storage.writable ? "可写" : "不可写"}</span>
        </div>
        <div className="management-row">
          <strong>所在磁盘可用空间</strong>
          <span>
            {bytes(data.storage.free_bytes)} / {bytes(data.storage.total_bytes)}
          </span>
        </div>
        <p className="muted">此处仅查看存储状态，不迁移或清理会议数据。</p>
      </section>
    </>
  );
}
