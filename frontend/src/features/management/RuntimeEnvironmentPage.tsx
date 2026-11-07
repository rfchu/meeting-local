import QualitySamplesPanel from "./QualitySamplesPanel";
import QualityBaselinePanel from "./QualityBaselinePanel";
import ReleaseReadinessPanel from "./ReleaseReadinessPanel";
import ServiceLifecyclePanel from "./ServiceLifecyclePanel";
import RecordingValidationPanel from "./RecordingValidationPanel";
import type { RuntimeOverview, ManagementPage } from "./types";

const bytes = (n: number | null) =>
  n === null ? "暂不可用" : `${(n / 1024 ** 3).toFixed(1)} GB`;
export default function RuntimeEnvironmentPage({
  data,
  onPage,
}: {
  data: RuntimeOverview;
  onPage?: (page: ManagementPage) => void;
}) {
  return (
    <>
      <ReleaseReadinessPanel />
      <QualityBaselinePanel />
      <QualitySamplesPanel />
      <ServiceLifecyclePanel />
      <RecordingValidationPanel />
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
