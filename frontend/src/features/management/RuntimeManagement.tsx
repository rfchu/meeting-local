import RecordingValidationPanel from "./RecordingValidationPanel";
import TasksPanel from "./TasksPanel";
import ServiceLifecyclePanel from "./ServiceLifecyclePanel";
import IndexManagement from "./IndexManagement";
import ModelDefaults from "./ModelDefaults";
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import Modal from "../../components/Modal";
import { ModelConfigDialog } from "../../components/Dialogs";
import { api } from "../../api";
import { clock } from "../../utils";
import "./management.css";
export type ManagementPage =
  "overview" | "models" | "defaults" | "environment" | "tasks" | "index";
export interface RuntimeOverview {
  checked_at: number;
  passive: boolean;
  issues: string[];
  engines: { id: string; label: string; ready: boolean; model: string }[];
  native: {
    available?: boolean;
    supported?: boolean;
    screen_permission?: boolean;
    microphone_permission?: boolean;
  };
  capture_test_active: boolean;
  qwen: {
    loaded: boolean;
    loading: boolean;
    busy: boolean;
    peak_memory_gb: number;
  };
  dependencies: { ffmpeg: boolean; diarization: boolean; punctuation: boolean };
  storage: {
    data_dir: string;
    writable: boolean;
    free_bytes: number | null;
    total_bytes: number | null;
  };
  llm: {
    connection_checked: boolean;
    profiles: {
      id: string;
      name: string;
      model: string;
      provider_type: string;
      is_default: number;
    }[];
  };
  sessions: {
    recording_id: string;
    engine: string;
    source: string;
    status: string;
    confirmed_until: number;
  }[];
  jobs: {
    id: string;
    kind: string;
    state: string;
    stage: string | null;
    progress: number;
  }[];
  job_counts: Record<string, number>;
}
const pages: [ManagementPage, string, string][] = [
  ["overview", "运行总览", "能力与当前活动"],
  ["models", "模型与服务", "语音模型与 LLM 配置"],
  ["defaults", "功能默认方案", "各功能的默认与上次模型"],
  ["tasks", "任务与诊断", "进度、等待与失败处理"],
  ["index", "数据与索引", "本地检索模型与索引维护"],
  ["environment", "运行环境", "依赖、权限与存储"],
];
const bytes = (n: number | null) =>
  n === null ? "暂不可用" : `${(n / 1024 ** 3).toFixed(1)} GB`;
export default function RuntimeManagement({
  page,
  onPage,
  onClose,
  onChanged,
  toast,
  onOpenMeeting,
  onRecording,
  onSearch,
}: {
  page: ManagementPage;
  onPage: (p: ManagementPage) => void;
  onClose: () => void;
  onChanged: () => void;
  toast: (text: string) => void;
  onOpenMeeting: (id: string) => void;
  onRecording: () => void;
  onSearch: () => void;
}) {
  const [modelDirty, setModelDirty] = useState(false);
  const [indexDirty, setIndexDirty] = useState(false);
  const dirty = modelDirty || indexDirty;
  const [indexVisited, setIndexVisited] = useState(page === "index");
  useEffect(() => {
    if (page === "index") setIndexVisited(true);
  }, [page]);
  const client = useQueryClient();
  const query = useQuery({
    queryKey: ["runtime-overview"],
    queryFn: api.runtimeOverview,
    refetchInterval: 15000,
  });
  const data = query.data;
  const [pendingLeave, setPendingLeave] = useState<(() => void) | null>(null);
  const leave = (action: () => void) => {
    if (dirty) setPendingLeave(() => action);
    else action();
  };
  const close = () => leave(onClose);
  return (
    <Modal className="management-shell" ariaLabel="运行管理" onClose={close}>
      <header className="management-header">
        <div>
          <span className="management-eyebrow">听记 · 本机工作空间</span>
          <h1>运行管理</h1>
        </div>
        <button onClick={close}>返回会议</button>
      </header>
      <div className="management-layout">
        <nav aria-label="运行管理导航">
          {pages.map(([key, label, description]) => (
            <button
              key={key}
              aria-current={page === key ? "page" : undefined}
              onClick={() => onPage(key)}
            >
              <strong>{label}</strong>
              <small>{description}</small>
            </button>
          ))}
          <p>
            会中选择仍在会议现场；这里管理服务与环境。切换分类保留未保存的模型表单。
          </p>
        </nav>
        <div className="management-content">
          <div className="management-page-heading">
            <div>
              <h2>{pages.find((p) => p[0] === page)?.[1]}</h2>
              <p>
                {page === "overview"
                  ? "了解当前能力与运行情况，再按需处理。"
                  : page === "models"
                    ? "统一管理模型服务；连接测试由你主动发起。"
                    : page === "defaults"
                      ? "统一默认、上次选择与本场覆盖的生效规则。"
                      : page === "tasks"
                        ? "查看处理进度、等待原因和可恢复的失败任务。"
                        : page === "index"
                          ? "集中维护本机语义检索，不影响关键词搜索。"
                          : "仅检查本机能力，不会请求录音权限或启动采集。"}
              </p>
            </div>
            <button
              disabled={query.isFetching}
              onClick={() => {
                void query.refetch();
                void client.invalidateQueries({
                  queryKey: ["model-preferences"],
                });
              }}
            >
              {query.isFetching ? "检查中…" : "刷新状态"}
            </button>
          </div>
          {query.error && (
            <p className="management-error" role="alert">
              状态检查暂不可用：{query.error.message}{" "}
              <button onClick={() => void query.refetch()}>重试检查</button>
            </p>
          )}
          {data && (
            <p className="management-check-time">
              本机检查于{" "}
              {new Date(data.checked_at * 1000).toLocaleTimeString("zh-CN", {
                hour12: false,
              })}{" "}
              · 未测试外部 LLM 连接
            </p>
          )}
          {!data && query.isPending && (
            <p role="status">正在读取本机运行信息…</p>
          )}
          {page === "overview" && data && (
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
                    {data.engines.filter((e) => e.ready).length}/
                    {data.engines.length} 已就绪
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
                    {(data.job_counts.running || 0) +
                      (data.job_counts.queued || 0)}{" "}
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
                  {!data.dependencies.ffmpeg && (
                    <p>缺少 FFmpeg，音频处理能力受限。</p>
                  )}
                  <button onClick={() => onPage("environment")}>
                    查看运行环境
                  </button>
                </section>
              )}
              <section className="management-section">
                <h3>当前实时会话</h3>
                {!data.sessions.length ? (
                  <p className="muted">暂无运行中的后端实时识别会话。</p>
                ) : (
                  data.sessions.map((s) => (
                    <div className="management-row" key={s.recording_id}>
                      <strong>
                        {s.engine === "qwen" ? "Qwen-ASR" : "Whisper"}
                      </strong>
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
          )}
          {page === "tasks" && (
            <TasksPanel
              onPage={onPage}
              onRecording={() => leave(onRecording)}
              onSearch={() => leave(onSearch)}
              onMeeting={(id) => leave(() => onOpenMeeting(id))}
            />
          )}
          <div hidden={page !== "index"}>
            {indexVisited && (
              <IndexManagement
                active={page === "index"}
                onDirtyChange={setIndexDirty}
                onTasks={() => onPage("tasks")}
              />
            )}
          </div>
          {page === "defaults" && <ModelDefaults />}
          <div hidden={page !== "models"}>
            {data && (
              <section className="management-section">
                <h3>本机语音引擎</h3>
                {data.engines.map((e) => (
                  <div className="management-row" key={e.id}>
                    <strong>{e.label}</strong>
                    <span>{e.ready ? "模型文件已就绪" : "待安装"}</span>
                    <small>
                      {e.id === "qwen"
                        ? data.qwen.busy
                          ? "正在推理"
                          : data.qwen.loaded
                            ? "已加载"
                            : data.qwen.loading
                              ? "加载中"
                              : "未加载"
                        : "按片段启动推理"}
                    </small>
                  </div>
                ))}
                <p className="muted">
                  本轮保留现有安装方式；本场引擎仍在录音或转写时选择。
                </p>
              </section>
            )}
            <ModelConfigDialog
              embedded
              onDirtyChange={setModelDirty}
              onClose={close}
              onChanged={() => {
                onChanged();
                void client.invalidateQueries({
                  queryKey: ["model-preferences"],
                });
                void client.invalidateQueries({
                  queryKey: ["runtime-overview"],
                });
              }}
              toast={toast}
            />
          </div>
          {page === "environment" && data && (
            <>
              <ServiceLifecyclePanel />
              <RecordingValidationPanel />
              <section className="management-section">
                <h3>采集与权限</h3>
                <div className="management-row">
                  <strong>本机音频采集</strong>
                  <span>
                    {data.native.available ? "采集器可用" : "采集器暂不可用"}
                  </span>
                </div>
                <div className="management-row">
                  <strong>屏幕与系统音频权限</strong>
                  <span>
                    {data.native.screen_permission
                      ? "已允许"
                      : "未允许或尚未确认"}
                  </span>
                </div>
                <div className="management-row">
                  <strong>麦克风权限</strong>
                  <span>
                    {data.native.microphone_permission
                      ? "已允许"
                      : "未允许或尚未确认"}
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
                    {bytes(data.storage.free_bytes)} /{" "}
                    {bytes(data.storage.total_bytes)}
                  </span>
                </div>
                <p className="muted">
                  此处仅查看存储状态，不迁移或清理会议数据。
                </p>
              </section>
            </>
          )}
        </div>
      </div>
      {pendingLeave && (
        <Modal
          className="small-dialog"
          ariaLabel="未保存的配置"
          onClose={() => setPendingLeave(null)}
        >
          <h2>有未保存的配置修改</h2>
          <p>继续编辑可保留当前表单；放弃后返回原工作区。</p>
          <div className="config-actions">
            <button onClick={() => setPendingLeave(null)}>继续编辑</button>
            <button
              onClick={() => {
                const next = pendingLeave;
                setPendingLeave(null);
                next();
              }}
            >
              放弃修改并继续
            </button>
          </div>
        </Modal>
      )}
    </Modal>
  );
}
