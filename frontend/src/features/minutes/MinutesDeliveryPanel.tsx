import DeliveryHistoryRow from "./DeliveryHistoryRow";
import AudienceDeliveryPreview from "./AudienceDeliveryPreview";
import { recordingQualityAttention } from "../notes/recordingQuality";
import { useState, useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { deliveryReadyForView, type DeliveryIssue } from "./deliveryReadiness";
import { api } from "../../api";
import type { Meeting } from "../../types";
import { currentMinutes } from "../../utils";
export default function MinutesDeliveryPanel({
  meeting,
  selectedId,
  dirty,
  includeCitations,
  onRefresh,
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
}) {
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
  const adopted = currentMinutes(meeting);
  const qualityAttention = recordingQualityAttention(meeting.recording_quality);
  const eligible = !!adopted && selectedId === adopted.id && !dirty;
  const handoffDelivery = meeting.handoff?.stages.find(
    (stage) => stage.key === "delivery",
  )?.source;
  const deliveries = meeting.deliveries || [];
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
          最后确认会同时保存“纪要已核对”记录和交付快照；按受众保存独立版本。
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
                  ? "当前采用稿已有内部交付版本。"
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
            onSaved={async () => {
              await readiness.refetch();
              await onRefresh();
            }}
          />
        )}
        {deliveries.length > 0 && <h4>已保存版本 · 历史快照保持不变</h4>}
        {deliveries.map((d, i) => (
          <DeliveryHistoryRow
            key={d.id}
            delivery={d}
            meetingId={meeting.id}
            current={
              currentDeliveryId === d.id ||
              handoffDelivery?.current_delivery_id === d.id ||
              handoffDelivery?.current_external_delivery_id === d.id
            }
            onRefresh={onRefresh}
            number={deliveries.length - i}
          />
        ))}
      </details>
    </section>
  );
}
