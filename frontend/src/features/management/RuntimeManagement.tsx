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

const pages: [ManagementPage, string, string][] = [
  ["overview", "运行总览", "能力与当前活动"],
  ["models", "模型与服务", "语音模型与 LLM 配置"],
  ["defaults", "功能默认方案", "各功能的默认与上次模型"],
  ["tasks", "任务与诊断", "进度、等待与失败处理"],
  ["index", "数据与索引", "本地检索模型与索引维护"],
  ["environment", "运行环境", "依赖、权限与存储"],
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
  onRecovery,
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
  onRecovery: () => void;
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
                  queryKey: ["runtime-resources"],
                });
                void client.invalidateQueries({
                  queryKey: ["runtime-recordings"],
                });
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
          {page === "overview" && (
            <RuntimeRecordingsPanel
              onRecording={() => leave(onRecording)}
              onRecovery={() => leave(onRecovery)}
            />
          )}
          {page === "overview" && data && (
            <RuntimeOverviewPage data={data} onPage={onPage} />
          )}
          {page === "tasks" && (
            <TasksPanel
              focus={taskFocus}
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
          <ModelServicesPage
            toast={toast}
            active={page === "models"}
            data={data}
            onDirtyChange={setModelDirty}
            onClose={close}
            onChanged={onChanged}
          />
          {page === "environment" && data && (
            <RuntimeEnvironmentPage data={data} />
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
