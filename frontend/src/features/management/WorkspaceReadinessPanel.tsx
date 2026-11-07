import { useQuery } from "@tanstack/react-query";
import { api } from "../../api";

const labels: Record<string, string> = {
  repository: "项目源码",
  data: "会议数据",
  backups: "备份目录",
  python: "服务 Python",
  python_environment: "服务 Python 环境",
  qwen_environment: "Qwen Python 环境",
  whisper_cli: "Whisper 程序",
  whisper_model: "Whisper 模型",
  qwen_python: "Qwen Python",
  whisper_vad: "Whisper 语音活动检测模型",
  diarization_segmentation: "发言人分段模型",
  diarization_embedding: "发言人向量模型",
  punctuation_model: "标点模型",
  qwen_runtime: "Qwen 环境记录",
  qwen_model: "Qwen 识别模型",
  qwen_aligner: "Qwen 对齐模型",
};
const states: Record<string, string> = {
  ready: "已在本机",
  cloud_placeholder: "有云端占位文件，请先下载",
  incomplete: "文件不完整",
  missing: "路径不存在",
  inaccessible: "路径或权限异常",
};
export default function WorkspaceReadinessPanel() {
  const query = useQuery({
    queryKey: ["workspace-readiness"],
    queryFn: api.workspaceReadiness,
    retry: false,
    refetchOnWindowFocus: false,
  });
  return (
    <section className="management-section">
      <h3>运行目录与模型预检</h3>
      <button disabled={query.isFetching} onClick={() => void query.refetch()}>
        重新检查
      </button>
      {query.isFetching && <p role="status">正在检查本机文件状态…</p>}
      {query.isError && (
        <p role="alert">路径检查暂不可用，请核对服务版本并重试。</p>
      )}
      {query.data && !query.isError && !query.isFetching && (
        <>
          <p>
            Python {query.data.python_version} · 数据盘剩余{" "}
            {query.data.free_bytes === null
              ? "未知"
              : `${(query.data.free_bytes / 1024 ** 3).toFixed(1)} GB`}
          </p>
          {query.data.paths.map(({ id, path, state }) => (
            <div key={id} className="management-row">
              <strong>{labels[id] || id}</strong>
              <span>{states[state] || state}</span>
              <small className="management-path">{path}</small>
            </div>
          ))}
          <p className="muted">
            本机路径检查读取文件状态。数据迁移预演可在项目终端运行{" "}
            <code>./workspace.command plan --help</code>。
          </p>
        </>
      )}
    </section>
  );
}
