import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import type { ModelImportStatus } from "./types";

const queryKey = ["model-directory-import"];
const busy = (status?: ModelImportStatus) =>
  status?.phase === "downloading" ||
  status?.phase === "copying" ||
  status?.phase === "validating";

type Source = "directory" | "zip" | "https";

const errors: Record<string, string> = {
  network_error: "下载中断或无法连接。请检查网络后重试。",
  certificate_error: "下载站点的 HTTPS 证书无法验证，请检查地址或联系提供方。",
  unsafe_redirect: "下载站点重定向到了不允许的地址。",
  unsafe_download_url: "请输入可公开访问的 HTTPS 下载地址。",
  download_response: "下载站点未返回完整模型包，请检查地址。",
  download_limit: "下载包超过大小限制。",
  invalid_archive: "ZIP 文件损坏或不完整。",
  unsupported_archive: "请选择 ZIP 压缩包。",
  unsupported_archive_entry: "ZIP 含加密文件或不支持的压缩方式。",
  unsafe_archive_entry: "ZIP 含越界路径、链接或无效文件。",
  unexpected_entry: "ZIP 含清单之外的文件。",
  duplicate_path: "ZIP 含重复路径。",
  archive_limits: "ZIP 目录索引超过安全上限。",
  archive_size_mismatch: "ZIP 中的文件大小超出清单约束。",
  no_space: "可用磁盘空间不足。",
  missing: "目录缺少清单中的文件。",
  size_mismatch: "文件大小与清单不一致。",
  hash_mismatch: "文件校验失败，请检查来源和版本。",
  cloud_placeholder: "请先将云端文件下载到本机，再重新导入。",
  unsafe_entry: "模型路径中有链接或无效文件。",
  unsafe_staging: "模型暂存目录不可用。",
  active_root_overlap: "暂存目录与在用模型目录重叠。",
  unreadable: "无法读取或写入文件，请检查目录权限。",
};

function bytes(value: number) {
  if (value >= 1e9) return `${(value / 1e9).toFixed(2)} GB`;
  if (value >= 1e6) return `${(value / 1e6).toFixed(1)} MB`;
  return `${value.toLocaleString()} B`;
}

export default function ModelDirectoryImport({ active }: { active: boolean }) {
  const [directory, setDirectory] = useState("");
  const [source, setSource] = useState<Source>("directory");
  const client = useQueryClient();
  const query = useQuery({
    queryKey,
    queryFn: api.modelImportStatus,
    enabled: active,
    refetchInterval: (query) => (busy(query.state.data) ? 500 : false),
  });
  const action = useMutation({
    mutationFn: (mode: "import" | "cancel" | "activate") =>
      mode === "import"
        ? {
            directory: api.importModelDirectory,
            zip: api.importModelArchive,
            https: api.downloadModelPack,
          }[source](directory.trim())
        : mode === "activate"
          ? api.activateModelImport()
          : api.cancelModelImport(),
    onMutate: async () => {
      await client.cancelQueries({ queryKey });
    },
    onSuccess: (status) => {
      client.setQueryData(queryKey, status);
    },
    onSettled: () => {
      void client.invalidateQueries({ queryKey });
    },
  });
  const status = query.data;
  const working = busy(status);
  const downloading = status?.phase === "downloading";

  return (
    <section className="management-section">
      <h3>导入模型包</h3>
      <p className="muted">导入后先暂存并校验，启用是单独操作。</p>
      {query.isError && <p role="alert">{query.error.message}</p>}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          action.mutate("import");
        }}
      >
        <label htmlFor="model-import-kind">导入来源</label>
        <select
          id="model-import-kind"
          value={source}
          disabled={working || action.isPending}
          onChange={(event) => {
            setSource(event.target.value as Source);
            setDirectory("");
          }}
        >
          <option value="directory">本机目录</option>
          <option value="zip">ZIP 压缩包</option>
          <option value="https">HTTPS 下载</option>
        </select>
        <label htmlFor="model-import-path">
          {
            {
              directory: "本机目录绝对路径",
              zip: "本机 ZIP 绝对路径",
              https: "模型包 HTTPS 下载地址",
            }[source]
          }
        </label>
        <div className="management-row">
          <input
            id="model-import-path"
            value={directory}
            onChange={(event) => setDirectory(event.target.value)}
            placeholder={
              {
                directory: "/Users/…/models",
                zip: "/Users/…/models.zip",
                https: "https://example.com/models.zip",
              }[source]
            }
            disabled={working || action.isPending}
            required
          />
          <button
            disabled={
              !status || !directory.trim() || working || action.isPending
            }
          >
            {source === "https" ? "下载并校验" : "暂存并校验"}
          </button>
          {working && (
            <button
              type="button"
              disabled={status?.cancel_requested || action.isPending}
              onClick={() => action.mutate("cancel")}
            >
              取消导入
            </button>
          )}
        </div>
      </form>
      {status && (
        <>
          <p className="muted">
            模型包 {status.version} · 需要 {bytes(status.total_bytes)}
            {status.available_bytes !== null &&
              ` · 暂存盘可用 ${bytes(status.available_bytes)}`}
          </p>
          {working && (
            <progress
              aria-label="模型包导入进度"
              value={
                downloading
                  ? status.download_total_bytes
                    ? status.downloaded_bytes
                    : undefined
                  : status.copied_bytes
              }
              max={
                downloading
                  ? status.download_total_bytes || undefined
                  : status.total_bytes
              }
            />
          )}
          <p role="status" className="management-path">
            {status.cancel_requested
              ? "正在取消…"
              : {
                  idle: "",
                  downloading: `已下载 ${bytes(status.downloaded_bytes)}${status.download_total_bytes ? ` / ${bytes(status.download_total_bytes)}` : ""}`,
                  copying: `已暂存 ${bytes(status.copied_bytes)}`,
                  validating: "正在校验暂存文件…",
                  ready: status.reused
                    ? "已复用并校验暂存模型包。"
                    : "模型包已校验，等待启用。",
                  cancelled: "导入已取消，临时文件已清理。",
                  failed: errors[status.error_code ?? ""] ?? "导入失败。",
                }[status.phase]}
            {status.error_path && ` ${status.error_path}`}
          </p>
        </>
      )}
      {status?.activation_supported && (
        <div className="management-row">
          <span>
            {status.active_version
              ? `在用模型包 ${status.active_version}`
              : "在用位置：默认模型目录"}
          </span>
          {status.activation_error && (
            <span role="alert">
              模型包未能启用。请重新导入或检查本机存储后再试。
            </span>
          )}
          {status.activation_pending && (
            <span role="status">
              已安排启用。录音和任务结束后，退出并重开听记。
            </span>
          )}
          {status.can_activate &&
            !status.active_version &&
            !status.activation_pending && (
              <button
                disabled={action.isPending}
                onClick={() => action.mutate("activate")}
              >
                下次重开启用
              </button>
            )}
        </div>
      )}
      {action.isError && <p role="alert">{action.error.message}</p>}
    </section>
  );
}
