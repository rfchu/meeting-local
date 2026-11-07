import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, downloadUrl } from "../../api";
export interface PrivateSample {
  id: string;
  duration: number;
  tags: string[];
  version: number;
  state: string;
  reference?: string;
  reference_origin?: string | null;
  source?: { kind: string; revision?: number; start?: number; end?: number };
}
export interface PrivateQualityInventory {
  summary: {
    total: number;
    human_reviewed: number;
    draft: number;
    datasets: number;
  };
  items: PrivateSample[];
  datasets: { id: string; samples: number; current_annotations: boolean }[];
  reports: {
    id: string;
    dataset_id: string;
    metrics: { samples: number; micro_cer: number };
    subject: { engine?: string; generation: string };
    current_annotations: boolean;
    current_rubric: boolean;
    annotation_methods: string[];
  }[];
}
const tags: Record<string, string> = {
  clean: "清晰语音",
  terms: "专名与术语",
  numbers: "数字与日期",
  noise: "噪声",
  overlap: "重叠发言",
  long_boundary: "长句边界",
};
function PrivateWorkspace() {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: ["private-quality-samples"],
    queryFn: api.privateQualitySamples,
    retry: false,
    gcTime: 0,
    refetchOnWindowFocus: false,
  });
  const [selected, setSelected] = useState("");
  const detail = useQuery({
    queryKey: ["private-quality-sample", selected],
    queryFn: () => api.privateQualitySample(selected),
    enabled: !!selected,
    retry: false,
    gcTime: 0,
    refetchOnWindowFocus: false,
  });
  const [reference, setReference] = useState("");
  const [heard, setHeard] = useState(false);
  const [rights, setRights] = useState(false);
  const [real, setReal] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [tag, setTag] = useState("clean");
  const [checked, setChecked] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => {
    setReference(detail.data?.reference || "");
    setHeard(false);
  }, [detail.data?.id, detail.data?.version]);
  async function act(fn: () => Promise<unknown>) {
    if (busy) return;
    setBusy(true);
    setMessage("");
    try {
      await fn();
      await query.refetch();
      await client.invalidateQueries({
        queryKey: ["private-quality-sample", selected],
      });
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "操作未确认，请重新核对。");
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <p>
        真实录音、人工参考和结果保存在当前数据目录的私有样本库。不会自动读取会议或把
        ASR 文字当作参考答案。
      </p>
      {query.isError && (
        <p role="alert">
          私有样本库暂不可用。
          <button onClick={() => void query.refetch()}>重新读取</button>
        </p>
      )}
      {query.data && (
        <p>
          {query.data.summary.total} 条样本 ·{" "}
          {query.data.summary.human_reviewed} 条已回听标注 ·{" "}
          {query.data.summary.datasets} 个固定版本
        </p>
      )}
      <h4>1. 导入已授权片段</h4>
      <label>
        短音频（1～120 秒，最多 20 MB）
        <input
          type="file"
          accept="audio/*"
          disabled={busy}
          onChange={(e) => setFile(e.target.files?.[0] || null)}
        />
      </label>
      <label>
        评测重点{" "}
        <select
          value={tag}
          disabled={busy}
          onChange={(e) => setTag(e.target.value)}
        >
          {Object.entries(tags).map(([key, label]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
        </select>
      </label>
      <label>
        <input
          type="checkbox"
          checked={rights}
          disabled={busy}
          onChange={(e) => setRights(e.target.checked)}
        />
        我有权将该片段用于本机质量评测
      </label>
      <label>
        <input
          type="checkbox"
          checked={real}
          disabled={busy}
          onChange={(e) => setReal(e.target.checked)}
        />
        该片段是真实语音，未将合成音频混入此集合
      </label>
      <button
        disabled={busy || !file || !rights || !real || query.isError}
        onClick={() =>
          void act(async () => {
            const row = await api.addPrivateQualitySample(
              file!,
              tag,
              rights,
              real,
            );
            setSelected(row.id);
            setRights(false);
            setReal(false);
            setMessage("片段已导入，尚未形成评分参考。");
          })
        }
      >
        导入候选片段
      </button>
      <h4>2. 回听与参考标注</h4>
      {query.data?.items.map((row) => (
        <div className="management-row" key={row.id}>
          <label>
            <input
              type="checkbox"
              checked={checked.includes(row.id)}
              disabled={busy || row.state !== "human_reviewed"}
              onChange={(e) =>
                setChecked((old) =>
                  e.target.checked
                    ? [...old, row.id]
                    : old.filter((id) => id !== row.id),
                )
              }
            />
            {row.id.slice(0, 8)} · {row.duration.toFixed(1)} 秒 ·{" "}
            {row.tags.map((t) => tags[t] || t).join("、")} ·{" "}
            {row.state === "human_reviewed"
              ? `人工参考 v${row.version}`
              : "待标注"}
          </label>
          <button disabled={busy} onClick={() => setSelected(row.id)}>
            回听标注
          </button>
        </div>
      ))}
      {selected && detail.data && !detail.isError && (
        <div>
          <p>
            当前片段 {selected.slice(0, 8)} · v{detail.data.version}
          </p>
          {detail.data.source?.kind === "selected_meeting" && (
            <p className="muted">
              来源：所选会议版本 {detail.data.source.revision} ·{" "}
              {detail.data.source.start?.toFixed(1)}～
              {detail.data.source.end?.toFixed(1)} 秒
            </p>
          )}
          <audio
            controls
            preload="none"
            src={downloadUrl(`/runtime/quality-samples/${selected}/audio`)}
          />
          {detail.data.reference_origin && (
            <p className="muted">
              标注方式：
              {detail.data.reference_origin ===
              "user_verified_existing_transcript"
                ? "用户回听校对既有文字"
                : "用户回听确认"}
              ；不视为双人独立标注。
            </p>
          )}
          <label>
            逐字参考文字
            <textarea
              value={reference}
              maxLength={2000}
              disabled={busy}
              rows={5}
              onChange={(e) => setReference(e.target.value)}
              placeholder="请根据音频手工填写；不自动带入模型结果"
            />
          </label>
          <label>
            <input
              type="checkbox"
              checked={heard}
              disabled={busy}
              onChange={(e) => setHeard(e.target.checked)}
            />
            我已回听核对参考文字
          </label>
          <button
            disabled={busy || !heard || !reference.trim() || detail.isFetching}
            onClick={() =>
              void act(async () => {
                await api.annotatePrivateQualitySample(
                  selected,
                  detail.data!.version,
                  reference,
                  heard,
                );
                setHeard(false);
                setMessage("已保存新的人工参考版本，旧固定集仍保留原版本。");
              })
            }
          >
            保存人工参考版本
          </button>
        </div>
      )}
      {detail.isError && <p role="alert">片段读取失败，不能提交标注。</p>}
      <h4>3. 冻结本次参考集</h4>
      <p>
        勾选已确认样本后冻结音频与参考版本。修改标注会保留旧结果，并提示其不再对应最新标注。
      </p>
      <button
        disabled={
          busy || checked.length === 0 || query.isError || query.isFetching
        }
        onClick={() =>
          void act(async () => {
            const members = (query.data?.items || [])
              .filter((row) => checked.includes(row.id))
              .map((row) => ({ id: row.id, version: row.version }));
            const data = await api.freezePrivateQualityDataset(members);
            setChecked([]);
            setMessage(
              `已冻结 ${data.samples} 条样本，固定集 ${data.id.slice(0, 8)}。`,
            );
          })
        }
      >
        冻结选中的 {checked.length} 条样本
      </button>
      {query.data?.datasets.map((data) => (
        <p key={data.id}>
          固定集 <code>{data.id}</code> · {data.samples} 条 ·{" "}
          {data.current_annotations
            ? "对应当前标注"
            : "保留历史版本，已有新标注"}
        </p>
      ))}
      <h4>4. 查看私有实测结果</h4>
      <p className="muted">
        CER
        按字面字符计错，中文数字与阿拉伯数字的书写差异也会计入；需结合片段核对，不作为语义错误率。
      </p>
      {!query.data?.reports.length && (
        <p className="muted">
          尚无评分。固定集生成后可显式运行项目内
          evaluate_private_quality.py；此页面不自动调用模型或云端。
        </p>
      )}
      {query.data?.reports.map((report) => (
        <p key={report.id}>
          {report.subject.engine || "导入候选"} · {report.metrics.samples} 条 ·
          CER {(report.metrics.micro_cer * 100).toFixed(2)}% ·{" "}
          {report.current_annotations && report.current_rubric
            ? "对应当前标注及口径"
            : "历史参考或评分口径"}
        </p>
      ))}
      {message && <p role="status">{message}</p>}
      <p className="muted">
        人工确认属于用户对来源和内容的声明，不能自动证明多人独立标注或真实场景覆盖。这里只显示最近
        100 条样本。
      </p>
    </>
  );
}
export default function QualitySamplesPanel() {
  const [open, setOpen] = useState(false);
  return (
    <details
      className="management-section"
      onToggle={(e) => setOpen(e.currentTarget.open)}
    >
      <summary>真实样本与人工标注 · 仅本机</summary>
      {open && <PrivateWorkspace />}
    </details>
  );
}
