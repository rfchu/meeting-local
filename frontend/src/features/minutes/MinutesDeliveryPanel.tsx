import {
  defaultDeliveryFilters,
  matchesDelivery,
  type DeliveryFilters,
} from "./deliveryHistory";
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
  const [filters, setFilters] = useState(defaultDeliveryFilters);
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
  const visible = deliveries
    .map((delivery, index) => ({ delivery, number: deliveries.length - index }))
    .filter(({ delivery }) => matchesDelivery(delivery, filters));
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
        {deliveries.length > 0 && (
          <>
            <h4>交付历史</h4>
            <fieldset>
              <legend>筛选交付件</legend>
              <label>
                受众{" "}
                <select
                  value={filters.audience}
                  onChange={(e) =>
                    setFilters({
                      ...filters,
                      audience: e.target.value as DeliveryFilters["audience"],
                    })
                  }
                >
                  <option value="">全部受众</option>
                  <option value="internal">内部版（含旧交付）</option>
                  <option value="external">对外版</option>
                </select>
              </label>{" "}
              <label>
                起始日期{" "}
                <input
                  type="date"
                  value={filters.from}
                  onChange={(e) =>
                    setFilters({ ...filters, from: e.target.value })
                  }
                />
              </label>{" "}
              <label>
                截止日期{" "}
                <input
                  type="date"
                  value={filters.through}
                  onChange={(e) =>
                    setFilters({ ...filters, through: e.target.value })
                  }
                />
              </label>{" "}
              <label>
                发送状态{" "}
                <select
                  value={filters.sent}
                  onChange={(event) =>
                    setFilters({
                      ...filters,
                      sent: event.target.value as DeliveryFilters["sent"],
                    })
                  }
                >
                  <option value="">全部状态</option>
                  <option value="unmarked">未标记发送</option>
                  <option value="sent">已标记发送</option>
                  <option value="unknown">未知（旧交付）</option>
                </select>
              </label>{" "}
              <button
                type="button"
                onClick={() => setFilters(defaultDeliveryFilters)}
              >
                清除筛选
              </button>
            </fieldset>
            {filters.from &&
            filters.through &&
            filters.from > filters.through ? (
              <p role="status">截止日期不能早于起始日期。</p>
            ) : (
              <p role="status">
                {visible.length
                  ? `显示 ${visible.length} / ${deliveries.length} 个交付件（本地日期）`
                  : "没有符合筛选条件的交付件。"}
              </p>
            )}
          </>
        )}
        {visible.map(({ delivery: d, number }) => (
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
            number={number}
          />
        ))}
      </details>
    </section>
  );
}
