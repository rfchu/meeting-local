import { useQuery } from "@tanstack/react-query";
import { api } from "../../api";
import { lifecycleLabel } from "../notes/recordingStatus";

export default function ServiceLifecyclePanel() {
  const { data, isError } = useQuery({
    queryKey: ["service-lifecycle"],
    queryFn: api.serviceLifecycle,
    refetchInterval: 5000,
  });
  return (
    <section className="management-section">
      <h3>服务生命周期</h3>
      <p>
        {isError
          ? "服务状态暂不可用，不能确认仍在运行"
          : !data
            ? "正在核对…"
            : `${data.mode === "detached" ? "独立进程会话" : data.mode === "launchd" ? "macOS 托管" : "前台开发模式"} · ${data.available ? "生命周期记录已启用" : "生命周期记录暂不可用"}`}
      </p>
      <details>
        <summary>启动与关闭记录</summary>
        <p className="muted">
          没有正常关闭记录只说明退出原因未知，不能单凭此认定内存不足或休眠。
        </p>
        {data?.events.slice(0, 12).map((event) => (
          <p key={event.seq}>
            {new Date(event.at * 1000).toLocaleString()} ·{" "}
            {lifecycleLabel(event.code)}
          </p>
        ))}
      </details>
    </section>
  );
}
