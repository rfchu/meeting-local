import AudienceDeliveryPreview from "./AudienceDeliveryPreview";
import { recordingQualityAttention } from "../notes/recordingQuality";
import { useState, useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { deliveryReadyForView, type DeliveryIssue } from "./deliveryReadiness";
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
  focusRequested = false,
  onFocused,
  onNavigate,
}: {
  focusRequested?: boolean;
  onFocused?: () => void;
  onNavigate?: (target: DeliveryIssue["target"]) => void;
  meeting: Meeting;
  selectedId: string | null;
  dirty: boolean;
  includeCitations: boolean;
  onRefresh: () => Promise<void>;
  toast: (s: string) => void;
}) {
  const [confirmed, setConfirmed] = useState(false),
    [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const details = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    if (!focusRequested) return;
    setOpen(true);
    details.current?.scrollIntoView({ block: "start", behavior: "smooth" });
    onFocused?.();
  }, [focusRequested, onFocused]);
  const readiness = useQuery({
    queryKey: [
      "delivery-readiness",
      meeting.id,
      includeCitations,
      meeting.handoff?.source_version,
    ],
    queryFn: () => api.deliveryReadiness(meeting.id, includeCitations),
    enabled: open,
    refetchInterval: open ? 5000 : false,
  });
  const value = readiness.data;
  const viewCurrent = deliveryReadyForView(
    value,
    meeting,
    selectedId,
    dirty,
    includeCitations,
  );
  const currentDeliveryId =
    viewCurrent && value?.minutes_review_state === "done" && value.can_finalize
      ? value.current_delivery_id
      : null;
  const [confirmedFingerprint, setConfirmedFingerprint] = useState("");
  useEffect(() => {
    setConfirmed(false);
    setConfirmedFingerprint("");
  }, [value?.fingerprint, viewCurrent]);
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
      <details
        ref={details}
        open={open}
        onToggle={(event) => setOpen(event.currentTarget.open)}
      >
        <summary>
          交付与导出
          {deliveries.length ? ` · ${deliveries.length} 个已确认版本` : ""}
        </summary>
        <p>
          最后确认会同时保存“纪要已核对”记录和交付快照；固定当前采用稿、原文引用及跟进状态。保存和导出都不表示已经发送。
        </p>
        {qualityAttention && (
          <p className="health-warning" role="status">
            {qualityAttention.text} 可在文字稿页回听核对。
          </p>
        )}
        {!eligible && <p role="status">请先保存并采用当前纪要，再确认交付。</p>}
        {eligible && value && !viewCurrent && (
          <p role="status">交付检查与页面版本不一致，等待更新后再确认。</p>
        )}
        {readiness.isError && (
          <p role="alert">
            交付检查读取失败：{readiness.error.message}{" "}
            <button onClick={() => void readiness.refetch()}>重试</button>
          </p>
        )}
        {open && readiness.isPending && (
          <p role="status">正在检查当前交付条件…</p>
        )}
        {value && (
          <div className="delivery-checks" aria-label="交付检查">
            {value.blockers.map((issue) => (
              <p key={issue.code}>
                {issue.label}{" "}
                <button
                  className="text-btn"
                  onClick={() => onNavigate?.(issue.target)}
                >
                  去处理
                </button>
              </p>
            ))}
            {value.warnings.map((issue) => (
              <p className="muted" key={issue.code}>
                {issue.label}{" "}
                <button
                  className="text-btn"
                  onClick={() => onNavigate?.(issue.target)}
                >
                  查看
                </button>
              </p>
            ))}
            {value.can_finalize && (
              <p role="status">
                {currentDeliveryId
                  ? "当前检查对应的交付版本已保存，可直接预览或导出。"
                  : "前序核对已完成。核对下方内容后，确认并保存本次交付版本。"}
              </p>
            )}
          </div>
        )}
        {open && eligible && (
          <AudienceDeliveryPreview
            key={adopted.id}
            meetingId={meeting.id}
            artifactId={adopted.id}
            includeCitations={includeCitations}
          />
        )}
        <label>
          <input
            type="checkbox"
            disabled={
              !eligible ||
              !viewCurrent ||
              readiness.isError ||
              !value?.can_finalize ||
              !!currentDeliveryId ||
              busy ||
              readiness.isFetching
            }
            checked={confirmed}
            onChange={(e) => {
              setConfirmed(e.target.checked);
              setConfirmedFingerprint(
                e.target.checked ? value?.fingerprint || "" : "",
              );
            }}
          />
          已核对当前采用稿、参会人、负责人、期限及本次提示，确认保存
        </label>
        <button
          className="primary"
          disabled={
            !eligible ||
            !viewCurrent ||
            readiness.isError ||
            !value?.can_finalize ||
            !!currentDeliveryId ||
            !confirmed ||
            confirmedFingerprint !== value?.fingerprint ||
            busy ||
            readiness.isFetching
          }
          onClick={async () => {
            if (
              !adopted ||
              !value ||
              !viewCurrent ||
              readiness.isError ||
              confirmedFingerprint !== value.fingerprint
            )
              return;
            setBusy(true);
            try {
              await api.finalizeDelivery(
                meeting.id,
                adopted.id,
                value.fingerprint,
                includeCitations,
              );
              setConfirmed(false);
              await readiness.refetch();
              await onRefresh();
              toast("交付版本已保存，可以导出。");
            } catch (e) {
              setConfirmed(false);
              await readiness.refetch();
              toast((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          {busy
            ? "正在保存…"
            : currentDeliveryId
              ? "当前交付版本已保存"
              : "确认核对并保存交付版本"}
        </button>
        {deliveries.length > 0 && <h4>已保存版本 · 历史快照保持不变</h4>}
        {deliveries.map((d, i) => (
          <article className="delivery-row" key={d.id}>
            <div>
              <strong>
                {d.title}{" "}
                {currentDeliveryId === d.id ? "· 当前交付" : "· 历史快照"}
              </strong>
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
