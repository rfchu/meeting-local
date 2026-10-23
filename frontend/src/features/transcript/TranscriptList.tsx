import {
  memo,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { defaultRangeExtractor, useVirtualizer } from "@tanstack/react-virtual";
import type { Segment } from "../../types";
import { clock } from "../../utils";
function fitText(element: HTMLTextAreaElement | null) {
  if (!element) return;
  element.style.height = "auto";
  element.style.height = `${element.scrollHeight + 2}px`;
}

const Row = memo(function Row({
  segment,
  onUpdate,
  onSeek,
}: {
  segment: Segment;
  onUpdate: (segment: Segment) => void;
  onSeek: (time: number) => void;
}) {
  const textRef = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => fitText(textRef.current), [segment.text]);
  useEffect(() => {
    const element = textRef.current;
    if (!element) return;
    let width = element.clientWidth;
    const observer = new ResizeObserver(() => {
      if (element.clientWidth !== width) {
        width = element.clientWidth;
        fitText(element);
      }
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return (
    <div className="segment" data-id={segment.id}>
      <button
        className="time"
        title={
          segment.boundary_review
            ? "分段交界处，请回听确认重复或漏字"
            : "回听这一段"
        }
        onClick={() => onSeek(segment.start)}
      >
        {clock(segment.start)}
        {segment.boundary_review ? " · 核对" : ""}
      </button>
      <div>
        <input
          className="speaker"
          aria-label="发言人"
          value={segment.speaker}
          onChange={(e) => onUpdate({ ...segment, speaker: e.target.value })}
        />
        <label className="speaker-confirmed">
          <input
            type="checkbox"
            checked={!!segment.speaker_locked}
            onChange={(e) =>
              onUpdate({ ...segment, speaker_locked: e.target.checked })
            }
          />
          已确认
        </label>
        {segment.alignment_warning && (
          <p role="note" className="error">
            时间待校准：{segment.alignment_warning}
          </p>
        )}
        <textarea
          ref={textRef}
          aria-label={`转写文字 ${clock(segment.start)}`}
          rows={Math.max(1, Math.ceil(segment.text.length / 42))}
          value={segment.text}
          onChange={(e) => onUpdate({ ...segment, text: e.target.value })}
        />
      </div>
    </div>
  );
});

export default function TranscriptList({
  segments,
  total,
  onUpdate,
  onSeek,
  jumpSegmentId,
  onEditingChange,
}: {
  segments: Segment[];
  total: number;
  onUpdate: (segment: Segment) => void;
  onSeek: (time: number) => void;
  jumpSegmentId?: string;
  onEditingChange: (id: string | null) => void;
}) {
  const parent = useRef<HTMLDivElement>(null);
  const [full, setFull] = useState(false),
    [focused, setFocused] = useState<string | null>(null),
    [highlight, setHighlight] = useState<string | null>(null);
  const [pendingFocus, setPendingFocus] = useState<{
    id: string;
    last: boolean;
  } | null>(null);
  const virtual = total > 300 && !full;
  const focusIndex = focused ? segments.findIndex((s) => s.id === focused) : -1;
  const getKey = useCallback((i: number) => segments[i].id, [segments]);
  const list = useVirtualizer({
    count: segments.length,
    getScrollElement: () => parent.current,
    enabled: virtual,
    estimateSize: () => 160,
    overscan: 5,
    getItemKey: getKey,
    rangeExtractor: (range) => {
      const indexes = defaultRangeExtractor(range);
      if (focusIndex >= 0 && !indexes.includes(focusIndex))
        indexes.push(focusIndex);
      return indexes.sort((a, b) => a - b);
    },
  });
  useEffect(() => {
    if (!jumpSegmentId) return;
    const index = segments.findIndex((s) => s.id === jumpSegmentId);
    if (index < 0) return;
    setHighlight(jumpSegmentId);
    if (virtual)
      list.scrollToIndex(index, { align: "center", behavior: "auto" });
    else
      parent.current
        ?.querySelector(`[data-index="${index}"]`)
        ?.scrollIntoView({ block: "center" });
  }, [jumpSegmentId, segments, virtual, list]);
  useEffect(() => {
    if (!pendingFocus) return;
    const index = segments.findIndex((s) => s.id === pendingFocus.id);
    if (index < 0) {
      setPendingFocus(null);
      return;
    }
    const frame = requestAnimationFrame(() => {
      const row = parent.current?.querySelector(`[data-index="${index}"]`);
      const items = row?.querySelectorAll<HTMLElement>("button,input,textarea");
      const field = pendingFocus.last ? items?.[items.length - 1] : items?.[0];
      if (field) {
        field.focus({ preventScroll: true });
        setPendingFocus(null);
      }
    });
    return () => cancelAnimationFrame(frame);
  }, [pendingFocus, segments]);
  const keyboard = (event: KeyboardEvent<HTMLDivElement>) => {
    if (
      !virtual ||
      event.key !== "Tab" ||
      event.altKey ||
      event.ctrlKey ||
      event.metaKey
    )
      return;
    const target = event.target as HTMLElement;
    const row = target.closest<HTMLElement>("[data-index]");
    if (!row) return;
    const items = row.querySelectorAll("button,input,textarea");
    const edge = event.shiftKey ? items[0] : items[items.length - 1];
    if (target !== edge) return;
    const index = Number(row.dataset.index) + (event.shiftKey ? -1 : 1);
    if (index < 0 || index >= segments.length) return;
    event.preventDefault();
    setFocused(segments[index].id);
    setPendingFocus({ id: segments[index].id, last: event.shiftKey });
    list.scrollToIndex(index, { align: "auto", behavior: "auto" });
  };
  const renderRow = (segment: Segment, index: number, start = 0) => (
    <div
      key={segment.id}
      data-index={index}
      ref={virtual ? list.measureElement : undefined}
      role="listitem"
      aria-posinset={index + 1}
      aria-setsize={segments.length}
      className={highlight === segment.id ? "transcript-hit" : ""}
      style={
        virtual
          ? {
              position: "absolute",
              top: 0,
              left: 0,
              width: "100%",
              transform: `translateY(${start}px)`,
            }
          : undefined
      }
    >
      <Row segment={segment} onUpdate={onUpdate} onSeek={onSeek} />
    </div>
  );
  return (
    <>
      {total > 300 && (
        <div className="transcript-list-info">
          <span>
            共 {total} 段 · 当前显示 {segments.length}{" "}
            段。全文检索请使用上方搜索框。
          </span>
          <label>
            <input
              type="checkbox"
              checked={full}
              onChange={(e) => setFull(e.target.checked)}
            />
            完整显示（浏览器查找／辅助阅读）
          </label>
        </div>
      )}
      <div
        id="segments"
        ref={parent}
        className={virtual ? "virtual-transcript" : ""}
        role="list"
        aria-label="会议文字片段"
        onKeyDownCapture={keyboard}
        onFocusCapture={(e) => {
          const row = (e.target as HTMLElement).closest<HTMLElement>(
            "[data-index]",
          );
          const i = Number(row?.dataset.index);
          if (row && segments[i]) {
            setFocused(segments[i].id);
            onEditingChange(segments[i].id);
          }
        }}
        onBlurCapture={(e) => {
          if (!e.currentTarget.contains(e.relatedTarget as Node | null)) {
            setFocused(null);
            onEditingChange(null);
          }
        }}
      >
        {virtual ? (
          <div
            style={{
              height: list.getTotalSize(),
              position: "relative",
              width: "100%",
            }}
          >
            {list
              .getVirtualItems()
              .map((item) =>
                renderRow(segments[item.index], item.index, item.start),
              )}
          </div>
        ) : (
          segments.map((segment, index) => renderRow(segment, index))
        )}
      </div>
    </>
  );
}
