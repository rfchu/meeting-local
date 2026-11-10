import type { ComponentProps } from "react";
import RecordingRecoveryCenter from "../notes/RecordingRecoveryCenter";
import type { TaskFocus } from "../../types";
import RuntimeOverviewPage from "./RuntimeOverviewPage";
import RuntimeEnvironmentPage from "./RuntimeEnvironmentPage";
import ModelServicesPage from "./ModelServicesPage";
import type { ManagementPage } from "./types";
export type { ManagementPage, RuntimeOverview } from "./types";
import RuntimeRecordingsPanel from "./RuntimeRecordingsPanel";

import TasksPanel from "./TasksPanel";

import IndexManagement from "./IndexManagement";
import ModelDefaults from "./ModelDefaults";
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import Modal from "../../components/Modal";

import { api } from "../../api";

import "./management.css";

interface NavGroupItem {
  key: ManagementPage;
  label: string;
  description: string;
  icon: string;
}

interface NavGroup {
  id: string;
  title: string;
  items: NavGroupItem[];
}

const navGroups: NavGroup[] = [
  {
    id: "monitoring",
    title: "运行监控",
    items: [
      { key: "overview", label: "运行总览", description: "能力状态与核心指标", icon: "📊" },
      { key: "tasks", label: "任务队列", description: "离线转写与后台任务", icon: "⏳" },
      { key: "recovery", label: "录音恢复", description: "中断会话与草稿拯救", icon: "🛡️" },
    ],
  },
  {
    id: "ai",
    title: "智能模型",
    items: [
      { key: "models", label: "模型与服务", description: "语音识别与 LLM 供应商", icon: "🧠" },
      { key: "defaults", label: "调度偏好", description: "各功能场景默认模型", icon: "🎛️" },
      { key: "index", label: "向量与检索", description: "本地语义模型与索引库", icon: "🔍" },
    ],
  },
  {
    id: "system",
    title: "系统环境",
    items: [
      { key: "environment", label: "存储与环境", description: "系统权限、依赖与参数", icon: "⚙️" },
    ],
  },
];

const allPages: [ManagementPage, string][] = [
  ["overview", "运行总览"],
  ["tasks", "任务队列"],
  ["recovery", "录音恢复"],
  ["models", "模型与服务"],
  ["defaults", "调度偏好"],
  ["index", "向量与检索"],
  ["environment", "存储与环境"],
];

export default function RuntimeManagement({
  page,
  taskFocus,
  onPage,
  onClose,
  onChanged,
  toast,
  onOpenMeeting,
  onRecording,
  recoveryCount,
  recoveryEngine,
  onRecoveryComplete,
  onSearch,
}: {
  page: ManagementPage;
  taskFocus?: TaskFocus;
  onPage: (p: ManagementPage) => void;
  onClose: () => void;
  onChanged: () => void;
  toast: (text: string) => void;
  onOpenMeeting: (id: string) => void;
  onRecording: () => void;
  recoveryCount: number;
  recoveryEngine: ComponentProps<
    typeof RecordingRecoveryCenter
  >["defaultEngine"];
  onRecoveryComplete: ComponentProps<
    typeof RecordingRecoveryCenter
  >["onComplete"];
  onSearch: () => void;
}) {
  const [modelDirty, setModelDirty] = useState(false);
  const [indexDirty, setIndexDirty] = useState(false);
  const [recoveryBusy, setRecoveryBusy] = useState(false);
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
  const close = () => {
    if (!recoveryBusy) leave(onClose);
  };

  const activeJobCount = data ? (data.job_counts.running || 0) + (data.job_counts.queued || 0) : 0;
  const hasEnvIssue = data ? data.issues.length > 0 || !data.storage.writable || !data.dependencies.ffmpeg : false;

  return (
    <Modal className="management-shell" ariaLabel="运行管理" onClose={close}>
      <header className="management-header">
        <div className="management-header-title">
          <div className="management-header-icon-box">🎛️</div>
          <div>
            <div className="management-header-meta">
              <span className="management-eyebrow">听记 · 本机工作空间</span>
              {data && (
                <span
                  className={`management-health-badge ${hasEnvIssue ? "warning" : "ready"}`}
                >
                  <span className="health-dot" />
                  {hasEnvIssue ? "部分组件需检查" : "本地引擎就绪"}
                </span>
              )}
            </div>
            <h1>控制中心与运行管理</h1>
          </div>
        </div>
        <div className="management-header-actions">
          {page !== "recovery" && (
            <button
              type="button"
              className="management-btn-refresh"
              disabled={query.isFetching}
              onClick={() => {
                void query.refetch();
                void client.invalidateQueries({ queryKey: ["runtime-resources"] });
                void client.invalidateQueries({ queryKey: ["runtime-recordings"] });
                void client.invalidateQueries({ queryKey: ["model-preferences"] });
              }}
              title="刷新本机状态"
            >
              <span className={`refresh-icon ${query.isFetching ? "spinning" : ""}`}>🔄</span>
              <span>{query.isFetching ? "检查中…" : "刷新状态"}</span>
            </button>
          )}
          <button
            type="button"
            className="management-btn-close"
            disabled={recoveryBusy}
            onClick={close}
          >
            返回会议 ✕
          </button>
        </div>
      </header>

      <div className="management-layout">
        <nav aria-label="运行管理导航" className="management-sidebar-nav">
          {navGroups.map((group) => (
            <div key={group.id} className="management-nav-group">
              <span className="management-nav-group-title">{group.title}</span>
              <div className="management-nav-group-items">
                {group.items.map((item) => {
                  let badgeContent: string | number | undefined;
                  let badgeType: "primary" | "warning" | "neutral" = "neutral";

                  if (item.key === "recovery" && recoveryCount > 0) {
                    badgeContent = recoveryCount;
                    badgeType = "warning";
                  } else if (item.key === "tasks" && activeJobCount > 0) {
                    badgeContent = activeJobCount;
                    badgeType = "primary";
                  } else if (item.key === "environment" && hasEnvIssue) {
                    badgeContent = "!";
                    badgeType = "warning";
                  }

                  return (
                    <button
                      key={item.key}
                      className={`management-nav-item ${page === item.key ? "active" : ""}`}
                      aria-current={page === item.key ? "page" : undefined}
                      disabled={recoveryBusy}
                      onClick={() => onPage(item.key)}
                    >
                      <div className="management-nav-item-header">
                        <span className="management-nav-icon">{item.icon}</span>
                        <strong className="management-nav-label">{item.label}</strong>
                        {badgeContent !== undefined && (
                          <span className={`management-nav-badge ${badgeType}`}>
                            {badgeContent}
                          </span>
                        )}
                      </div>
                      <small className="management-nav-desc">{item.description}</small>
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
          <div className="management-nav-footer">
            <span className="nav-privacy-tag">🔒 纯本地运行 · 隐私无传输</span>
          </div>
        </nav>

        <div className="management-content">
          <div className="management-page-heading">
            <div>
              <h2>{allPages.find((p) => p[0] === page)?.[1] || "控制中心"}</h2>
              <p>
                {page === "overview"
                  ? "实时监控本机引擎、活动录音会话与后台流水线。"
                  : page === "recovery"
                    ? "核对异常中断的会话，拯救已保存的音频碎片并整理为正式会议。"
                    : page === "models"
                      ? "统一管理本机语音模型与大模型服务商，主动发起连通性测试。"
                      : page === "defaults"
                        ? "统一配置各场景默认模型与调度偏好，保存在本机工作空间。"
                        : page === "tasks"
                          ? "查看后台转写、分析与纪要生成进度，定位排队原因与失败重试。"
                          : page === "index"
                            ? "集中维护本机语义向量知识库与检索模型，不影响普通关键词搜索。"
                            : "检查系统采集权限、FFmpeg 多媒体依赖与本地存储空间。"}
              </p>
            </div>
            {data && (
              <span className="management-check-time">
                检查于 {new Date(data.checked_at * 1000).toLocaleTimeString("zh-CN", { hour12: false })} · 纯本机环境
              </span>
            )}
          </div>

          {page === "recovery" && (
            <RecordingRecoveryCenter
              defaultEngine={recoveryEngine}
              toast={toast}
              onBusyChange={setRecoveryBusy}
              onComplete={onRecoveryComplete}
            />
          )}

          {page !== "recovery" && query.error && (
            <div className="management-error-banner" role="alert">
              <span className="error-icon">⚠️</span>
              <div>
                <strong>状态检查暂不可用</strong>
                <p>{query.error.message}</p>
              </div>
              <button
                type="button"
                className="btn-retry"
                onClick={() => void query.refetch()}
              >
                重试检查
              </button>
            </div>
          )}

          {page !== "recovery" && !data && query.isPending && (
            <div className="management-loading-box" role="status">
              <span className="spinning">🔄</span>
              <span>正在读取本机运行信息与模型状态…</span>
            </div>
          )}

          {/* 运行总览 */}
          {page === "overview" && data && (
            <RuntimeOverviewPage data={data} onPage={onPage} />
          )}
          {page === "overview" && (
            <RuntimeRecordingsPanel
              onRecording={() => leave(onRecording)}
              onRecovery={() => onPage("recovery")}
            />
          )}

          {/* 任务队列 */}
          {page === "tasks" && (
            <TasksPanel
              focus={taskFocus}
              onPage={onPage}
              onRecording={() => leave(onRecording)}
              onSearch={() => leave(onSearch)}
              onMeeting={(id) => leave(() => onOpenMeeting(id))}
            />
          )}

          {/* 向量与检索 */}
          <div hidden={page !== "index"}>
            {indexVisited && (
              <IndexManagement
                active={page === "index"}
                onDirtyChange={setIndexDirty}
                onTasks={() => onPage("tasks")}
              />
            )}
          </div>

          {/* 调度偏好 */}
          {page === "defaults" && <ModelDefaults />}

          {/* 模型与服务 */}
          <ModelServicesPage
            toast={toast}
            active={page === "models"}
            data={data}
            onDirtyChange={setModelDirty}
            onClose={close}
            onChanged={onChanged}
          />

          {/* 存储与环境 */}
          {page === "environment" && data && (
            <RuntimeEnvironmentPage data={data} onPage={onPage} />
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
