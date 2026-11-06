import { useQueryClient } from "@tanstack/react-query";
import { ModelConfigDialog } from "../../components/Dialogs";
import type { RuntimeOverview } from "./types";

export default function ModelServicesPage({
  active,
  data,
  onDirtyChange,
  onClose,
  onChanged,
  toast,
}: {
  active: boolean;
  data: RuntimeOverview | undefined;
  onDirtyChange: (dirty: boolean) => void;
  onClose: () => void;
  onChanged: () => void;
  toast: (text: string) => void;
}) {
  const client = useQueryClient();
  return (
    <div hidden={!active}>
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
        onDirtyChange={onDirtyChange}
        onClose={onClose}
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
  );
}
