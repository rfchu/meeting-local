import { recordingQualityAttention } from "../notes/recordingQuality";
import { useState, useEffect } from "react";
import { api, download, downloadUrl } from "../../api";
import type { Meeting } from "../../types";
import { currentMinutes } from "../../utils";
export default function MinutesDeliveryPanel({
  meeting,
  selectedId,
  dirty,
  includeCitations,
  onRefresh,
  toast,
}: {
  meeting: Meeting;
  selectedId: string | null;
  dirty: boolean;
  includeCitations: boolean;
  onRefresh: () => Promise<void>;
  toast: (s: string) => void;
}) {
  const [confirmed, setConfirmed] = useState(false),
    [busy, setBusy] = useState(false);
  const adopted = currentMinutes(meeting);
  const qualityAttention = recordingQualityAttention(meeting.recording_quality);
  useEffect(
    () => setConfirmed(false),
    [selectedId, meeting.active_minutes_id, dirty, includeCitations],
  );
  const eligible = !!adopted && selectedId === adopted.id && !dirty;
  const deliveries = meeting.deliveries || [];
  const link = (id: string, format: string) =>
    `/meetings/${meeting.id}/minutes/export/${format}?delivery_id=${id}`;
  return (
    <section className="minutes-delivery" aria-label="纪要交付">
      <details>
        <summary>
          交付与导出
          {deliveries.length ? ` · ${deliveries.length} 个已确认版本` : ""}
        </summary>
        <p>
          确认后固定当前采用稿、原文引用和跟进状态记录，后续修改保留为新版本。
        </p>
        {qualityAttention && (
          <p className="health-warning" role="status">
            {qualityAttention.text} 可在文字稿页回听核对。
          </p>
        )}
        {!eligible && <p role="status">请先保存并采用当前纪要，再确认交付。</p>}
        <label>
          <input
            type="checkbox"
            disabled={!eligible || busy}
            checked={confirmed}
            onChange={(e) => setConfirmed(e.target.checked)}
          />
          已核对内容、参会人、负责人及期限
        </label>
        <button
          className="primary"
          disabled={!eligible || !confirmed || busy}
          onClick={async () => {
            if (!adopted) return;
            setBusy(true);
            try {
              await api.createDelivery(
                meeting.id,
                adopted.id,
                includeCitations,
              );
              setConfirmed(false);
              await onRefresh();
              toast("交付版本已保存，可以导出。");
            } catch (e) {
              toast((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          {busy ? "正在保存…" : "保存交付版本"}
        </button>
        {deliveries.map((d, i) => (
          <article className="delivery-row" key={d.id}>
            <div>
              <strong>{d.title}</strong>
              <small>
                交付 {deliveries.length - i} · {d.created} ·{" "}
                {d.include_citations ? "含原文引用" : "不含原文引用"}
              </small>
            </div>
            <a
              target="_blank"
              rel="noreferrer"
              href={downloadUrl(link(d.id, "pdf") + "&inline=true")}
            >
              预览
            </a>
            <button onClick={() => download(link(d.id, "docx"))}>Word</button>
            <button onClick={() => download(link(d.id, "pdf"))}>PDF</button>
            <details>
              <summary>更多</summary>
              <button onClick={() => download(link(d.id, "md"))}>
                Markdown
              </button>
              <p>
                文字稿 v{d.revision} · 交付时已完成{" "}
                {d.task_status.filter((t) => t.status === "done").length} /{" "}
                {d.task_status.length} 项已关联行动
              </p>
            </details>
          </article>
        ))}
      </details>
    </section>
  );
}
