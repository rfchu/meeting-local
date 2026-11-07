import { useState } from "react";
import { api, downloadDelivery, downloadUrl } from "../../api";
import type { MinutesDelivery } from "../../types";

const formats = [
  ["docx", "Word"],
  ["pdf", "PDF"],
  ["md", "Markdown"],
] as const;

export default function DeliveryHistoryRow({
  delivery,
  meetingId,
  current,
  onRefresh,
  number,
}: {
  delivery: MinutesDelivery;
  meetingId: string;
  current: boolean;
  onRefresh: () => Promise<void>;
  number: number;
}) {
  const [downloadStatus, setDownloadStatus] = useState("");
  const [downloadError, setDownloadError] = useState("");
  const [sendingError, setSendingError] = useState("");
  const [exporting, setExporting] = useState(false);
  const [savingStatus, setSavingStatus] = useState(false);
  const link = (format: string) =>
    `/meetings/${meetingId}/minutes/export/${format}?delivery_id=${delivery.id}`;

  return (
    <article className="delivery-row">
      <div>
        <strong>
          {delivery.title} ·{" "}
          {delivery.audience === "external" ? "对外版" : "内部版"}{" "}
          {current ? "· 当前交付" : "· 历史快照"}
        </strong>
        <small>
          交付 {number} · {delivery.created} ·{" "}
          {delivery.include_citations ? "含原文引用" : "不含原文引用"}
          {!!delivery.template_version &&
            ` · 自定义模板 v${delivery.template_version}`}
        </small>
        <small>
          {delivery.sent_at
            ? `已记录发送 · ${delivery.sent_at} UTC`
            : "发送状态 · 未记录"}
        </small>
        {downloadStatus && <small role="status">{downloadStatus}</small>}
        {downloadError && <small role="alert">{downloadError}</small>}
        {sendingError && <small role="alert">{sendingError}</small>}
      </div>
      <a
        target="_blank"
        rel="noreferrer"
        href={downloadUrl(link("pdf") + "&inline=true")}
      >
        预览 PDF
      </a>
      {formats.map(([format, label]) => (
        <button
          key={format}
          disabled={exporting}
          onClick={async () => {
            setExporting(true);
            setDownloadStatus("");
            setDownloadError("");
            try {
              await downloadDelivery(
                link(format),
                `${delivery.title}.${format}`,
              );
              setDownloadStatus(`${label} 已生成并交给浏览器下载`);
            } catch (error) {
              setDownloadError(
                `${label} 导出失败，可再次点击重试。${(error as Error).message}`,
              );
            } finally {
              setExporting(false);
            }
          }}
        >
          {label}
        </button>
      ))}
      <button
        disabled={savingStatus}
        onClick={async () => {
          setSavingStatus(true);
          setSendingError("");
          try {
            await api.setDeliverySent(
              meetingId,
              delivery.id,
              !delivery.sent_at,
            );
            await onRefresh();
          } catch (error) {
            setSendingError((error as Error).message);
          } finally {
            setSavingStatus(false);
          }
        }}
      >
        {delivery.sent_at ? "撤销已发送记录" : "标记为已发送"}
      </button>
    </article>
  );
}
