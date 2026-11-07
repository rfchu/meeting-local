import CaptureControlPanel from "./CaptureControlPanel";
import { clock } from "../../utils";
import type { RuntimeOverview, ManagementPage } from "./types";

export default function RuntimeOverviewPage({
  data,
  onPage,
}: {
  data: RuntimeOverview;
  onPage: (page: ManagementPage) => void;
}) {
  return (
    <>
      <div className="management-cards">
        <article>
          <span>实时识别</span>
          <strong>
            {data.sessions.length
              ? `${data.sessions.length} 场托管会话`
              : "暂无托管会话"}
          </strong>
          <small>包含运行、暂停与收尾中的识别</small>
        </article>
        <article>
          <span>语音模型文件</span>
          <strong>
            {data.engines.filter((e) => e.ready).length}/{data.engines.length}{" "}
            已就绪
          </strong>
          <small>检查本机模型文件可用性</small>
        </article>
        <article>
          <span>LLM 服务</span>
          <strong>{data.llm.profiles.length} 个已配置</strong>
          <small>连接状态需主动测试</small>
        </article>
        <article>
          <span>后台任务</span>
          <strong>
            {(data.job_counts.running || 0) + (data.job_counts.queued || 0)}{" "}
            个待完成
          </strong>
          <small>包括处理中与排队任务</small>
        </article>
      </div>
      {(data.issues.length > 0 ||
        !data.storage.writable ||
        !data.dependencies.ffmpeg) && (
        <section className="management-notice">
          <h3>需要检查</h3>
          {data.issues.map((i) => (
            <p key={i}>{i}</p>
          ))}
          {!data.storage.writable && (
            <p>数据目录不可写，请检查本机目录权限。</p>
          )}
          {!data.dependencies.ffmpeg && <p>缺少 FFmpeg，音频处理能力受限。</p>}
          <button onClick={() => onPage("environment")}>查看运行环境</button>
        </section>
      )}
      <CaptureControlPanel />
      <section className="management-section">
        <h3>实时识别会话</h3>
        {!data.sessions.length ? (
          <p className="muted">暂无运行中的后端实时识别会话。</p>
        ) : (
          data.sessions.map((s) => (
            <div className="management-row" key={s.recording_id}>
              <strong>{s.engine === "qwen" ? "Qwen-ASR" : "Whisper"}</strong>
              <span>
                {s.status === "paused"
                  ? "暂停"
                  : s.status === "draining"
                    ? "收尾中"
                    : "识别中"}{" "}
                · 已确认至 {clock(s.confirmed_until)}
              </span>
              <code>{s.recording_id.slice(0, 8)}</code>
            </div>
          ))
        )}
      </section>
      <section className="management-section">
        <h3>后台任务</h3>
        {!data.jobs.length ? (
          <p className="muted">暂无后台任务。</p>
        ) : (
          data.jobs.map((j) => (
            <div className="management-row" key={j.id}>
              <strong>
                {(
                  {
                    transcription: "音频转写",
                    analysis: "会议分析",
                    minutes: "AI 会议纪要",
                    retrieval_index: "语义索引",
                    retrieval_answer: "引用问答",
                    terminology: "术语提取",
                    note_expansion: "简记整理",
                  } as Record<string, string>
                )[j.kind] || "后台处理"}
              </strong>
              <span>
                {j.state === "queued" ? "排队中" : j.stage || "处理中"}
              </span>
              <small>{Math.round((j.progress || 0) * 100)}%</small>
            </div>
          ))
        )}
      </section>
    </>
  );
}
