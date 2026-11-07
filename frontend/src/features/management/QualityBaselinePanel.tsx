import { useQuery } from "@tanstack/react-query";
import { api } from "../../api";
export interface QualityBaseline {
  suite_id: string;
  fixed_cases: number;
  pending_asr: { engine: string; label: string; reason: string }[];
  reports: {
    name: string;
    mode: string;
    current: boolean;
    metrics?: {
      probe_count?: number;
      passed_probes?: number;
      samples?: number;
      micro_cer?: number;
      reference_characters?: number;
    };
    subject?: { model_label?: string; generation?: string };
  }[];
}
export default function QualityBaselinePanel() {
  const query = useQuery({
    queryKey: ["quality-baseline"],
    queryFn: () => api.qualityBaseline(),
    retry: false,
    gcTime: 0,
    refetchOnWindowFocus: false,
  });
  return (
    <section className="management-section" aria-label="固定样本质量基线">
      <h3>固定样本质量基线</h3>
      <p>仅展示已保存的离线评测，不启动模型、不读取会议。</p>
      <button disabled={query.isFetching} onClick={() => void query.refetch()}>
        刷新基线状态
      </button>
      {query.isError && (
        <p role="alert">基线暂不可用，不能据旧结果判断质量。</p>
      )}
      {query.isFetching && <p role="status">正在核对样本与评分口径…</p>}
      {query.data && !query.isError && !query.isFetching && (
        <>
          <p>
            {query.data.suite_id} · {query.data.fixed_cases}{" "}
            个固定基础样本，另含增量概览样例
          </p>
          {query.data.reports.length === 0 && (
            <p>尚未保存基线报告，请先运行离线评测。</p>
          )}
          {query.data.reports.map((report) => (
            <div className="management-row" key={report.name}>
              <strong>
                {report.mode === "offline_protocol"
                  ? "引用与流程守卫"
                  : report.subject?.model_label || report.name}
              </strong>
              <span>
                {!report.current
                  ? "样本或评分口径已变化，待重新评测"
                  : report.mode === "offline_protocol"
                    ? `${report.metrics?.passed_probes}/${report.metrics?.probe_count} 项离线探针通过`
                    : `合成语音 CER ${((report.metrics?.micro_cer ?? 0) * 100).toFixed(2)}% · ${report.metrics?.samples} 段`}
              </span>
            </div>
          ))}
          {query.data.pending_asr.map((pending) => (
            <p className="muted" key={pending.engine}>
              {pending.label} · 未生成有效基线
              {pending.reason === "cloud_placeholder"
                ? "：模型或配置是云端占位文件，请先保持下载到本机"
                : pending.reason === "missing"
                  ? "：模型文件未就绪"
                  : "：待显式运行评测"}
            </p>
          ))}
          <details>
            <summary>如何理解与更新基线</summary>
            <p>
              离线探针检查引用、分段与流程约束；通过率不代表模型理解准确率。ASR
              使用自编内容的固定合成语音，CER
              按字符计错，保留中文数字与阿拉伯数字的书写差异，不能据此判定哪种模型更适合所有会议。
            </p>
            <p>
              评测报告绑定完整样本和评分版本，缺少样本或口径不同不能直接比较；耗时仅作观察，不作为自动回归门槛。真实长会议、噪声、重叠发言及纪要语义充分性仍需独立标注和评估。
            </p>
            <p className="muted">
              使用项目内 scripts/evaluate_quality.py
              生成候选报告并比较；只在显式指定 --run-local-models 时运行本机
              ASR，不会自动调用云端。
            </p>
          </details>
        </>
      )}
    </section>
  );
}
