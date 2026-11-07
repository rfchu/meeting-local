import { useQuery } from "@tanstack/react-query";
import { api } from "../../api";
export interface ReleaseReadiness {
  local_ready: boolean;
  external_release_ready: boolean;
  hardware_evidence_available: boolean;
  components: {
    component: string;
    verified: boolean;
    version: string | null;
    signature: string;
  }[];
  hardware: { case: string; label: string; state: string }[];
  boundaries: { id: string; state: string; label: string }[];
}
export default function ReleaseReadinessPanel() {
  const query = useQuery({
    queryKey: ["release-readiness"],
    queryFn: () => api.releaseReadiness(),
    retry: false,
    gcTime: 0,
    refetchOnWindowFocus: false,
  });
  return (
    <section className="management-section" aria-label="本机交付与设备验收">
      <h3>本机交付与设备验收</h3>
      <button disabled={query.isFetching} onClick={() => void query.refetch()}>
        重新检查交付状态
      </button>
      {(query.isPending || query.isFetching) && (
        <p role="status">正在核对本机应用与源码…</p>
      )}
      {query.isError && (
        <p role="alert">交付状态暂不可用，不能据旧结果确认可升级或发行。</p>
      )}
      {query.data && !query.isError && !query.isFetching && (
        <>
          <p>
            {query.data.local_ready
              ? "本机应用与源码匹配"
              : "本机交付组件需要检查或重新构建"}{" "}
            ·{" "}
            {query.data.external_release_ready
              ? "独立发行检查通过"
              : "独立发行尚未完成"}
          </p>
          <div className="management-cards">
            {query.data.components.map((c) => (
              <article key={c.component}>
                <span>
                  {c.component === "host" ? "原生宿主" : "音频采集器"}
                </span>
                <strong>{c.verified ? "核对通过" : "待检查"}</strong>
                <small>
                  版本 {c.version || "未知"} ·{" "}
                  {c.signature === "adhoc" ? "本机开发签名" : "签名待发行核对"}
                </small>
              </article>
            ))}
          </div>
          <details>
            <summary>设备场景与发行边界</summary>
            {!query.data.hardware_evidence_available && (
              <p>设备验收记录暂不可用，不能视为已通过。</p>
            )}
            {query.data.hardware.map((h) => (
              <p key={h.case}>
                {h.label} ·{" "}
                {h.state === "passed"
                  ? "已有当前版本人工核对记录"
                  : h.state === "failed"
                    ? "最近人工核对未通过"
                    : h.state === "stale"
                      ? "原结论已过期，需重新验收"
                      : "待真实设备验收"}
              </p>
            ))}
            {query.data.boundaries.map((b) => (
              <p key={b.id}>{b.label}</p>
            ))}
            <p className="muted">
              在下方“录音稳定性观察”中查看原记录并逐项回听核对。此处不停止服务、不操作设备、不下载模型。
            </p>
          </details>
        </>
      )}
    </section>
  );
}
