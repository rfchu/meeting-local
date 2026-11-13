import {
  memo,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type RefObject,
} from "react";
import { defaultRangeExtractor, useVirtualizer } from "@tanstack/react-virtual";
import type { Segment, SourceTrack } from "../../types";
import { clock, trackLabel } from "../../utils";
import {
  IconAlertTriangle,
  IconCheck,
  IconClock,
  IconCopy,
  IconLightbulb,
} from "../../styles/icons";
function fitText(element: HTMLTextAreaElement | null) {
  if (!element) return;
  element.style.height = "auto";
  element.style.height = `${element.scrollHeight + 2}px`;
}

const SPEAKER_PALETTES = [
  { bg: "#eff6ff", text: "#1d4ed8", border: "#bfdbfe", dot: "#3b82f6" }, // blue
  { bg: "#f5f3ff", text: "#6d28d9", border: "#ddd6fe", dot: "#8b5cf6" }, // violet
  { bg: "#f0fdf4", text: "#15803d", border: "#bbf7d0", dot: "#22c55e" }, // green
  { bg: "#fffbeb", text: "#b45309", border: "#fde68a", dot: "#f59e0b" }, // amber
  { bg: "#fdf2f8", text: "#be185d", border: "#fbcfe8", dot: "#ec4899" }, // pink
  { bg: "#f0fdfa", text: "#0f766e", border: "#99f6e4", dot: "#14b8a6" }, // teal
];

function getSpeakerPalette(name: string) {
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = (hash << 5) - hash + name.charCodeAt(i);
  }
  return SPEAKER_PALETTES[Math.abs(hash) % SPEAKER_PALETTES.length];
}

const Row = memo(function Row({
  segment,
  onUpdate,
  onSeek,
  playing = false,
  readOnly = false,
}: {
  playing?: boolean;
  readOnly?: boolean;
  segment: Segment & { reading_parts?: Segment[] };
  onUpdate: (segment: Segment) => void;
  onSeek: (time: number, track?: SourceTrack) => void;
}) {
  const textRef = useRef<HTMLTextAreaElement>(null);
  const [copied, setCopied] = useState(false);
  const palette = getSpeakerPalette(segment.speaker || "发言人");

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

  const handleCopy = () => {
    void navigator.clipboard?.writeText(segment.text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div
      className={`transcript-card segment ${playing ? "playing active-playback" : ""} ${segment.speaker_locked ? "speaker-locked" : ""}`}
      data-id={segment.id}
    >
      <div className="card-meta-bar">
        <div className="meta-left">
          <button
            type="button"
            className="btn-timestamp"
            title={
              segment.boundary_review
                ? "分段交界处，点击回听确认"
                : "回听此句原声"
            }
            onClick={() =>
              onSeek(segment.start, segment.source_track ?? undefined)
            }
          >
            <span className="play-triangle">▶</span>
            <span className="time-num">{clock(segment.start)}</span>
          </button>
          <div
            className="speaker-badge"
            style={{
              backgroundColor: palette.bg,
              borderColor: palette.border,
              color: palette.text,
            }}
          >
            <span
              className="speaker-dot"
              style={{ backgroundColor: palette.dot }}
            />
            <input
              readOnly={readOnly}
              className="speaker-input"
              aria-label="发言人"
              value={segment.speaker}
              title="点击直接修改发言人名称"
              onChange={(e) =>
                onUpdate({ ...segment, speaker: e.target.value })
              }
            />
          </div>
          {segment.source_track && (
            <span className="source-track-pill">
              {trackLabel(segment.source_track)}
            </span>
          )}
          {segment.boundary_review && (
            <span
              className="warning-pill boundary"
              title="分段交界处，请回听确认重复或漏字"
            >
              <IconAlertTriangle size={12} /> 待核对边界
            </span>
          )}
          {segment.alignment_warning && (
            <span
              className="warning-pill alignment"
              title={segment.alignment_warning}
            >
              <IconClock size={12} /> 时间待校准
            </span>
          )}
        </div>
        <div className="card-actions">
          <button
            type="button"
            className="btn-card-action"
            title="回听这一句原声 (Option+R)"
            onClick={() =>
              onSeek(segment.start, segment.source_track ?? undefined)
            }
          >
            ▶ 回听
          </button>
          <button
            type="button"
            className="btn-card-action"
            title="复制本句文字"
            onClick={handleCopy}
          >
            {copied ? (
              <>
                <IconCheck size={12} /> 已复制
              </>
            ) : (
              <>
                <IconCopy size={12} /> 复制
              </>
            )}
          </button>
          <label
            className={`speaker-lock-label ${segment.speaker_locked ? "is-locked" : ""}`}
            title="锁定发言人声纹，防止重算覆盖"
          >
            <input
              type="checkbox"
              disabled={readOnly}
              checked={!!segment.speaker_locked}
              onChange={(e) =>
                onUpdate({ ...segment, speaker_locked: e.target.checked })
              }
            />
            <span>{segment.speaker_locked ? "已确认" : "确认发言人"}</span>
          </label>
        </div>
      </div>
      <div className="card-body">
        {readOnly ? (
          <>
            <p className="reading-paragraph">{segment.text}</p>
            {segment.reading_parts && segment.reading_parts.length > 1 && (
              <div className="paragraph-source-times">
                <small>连续发言 · 分段回听</small>
                {segment.reading_parts.map((part) => (
                  <button
                    key={part.id}
                    className="text-btn"
                    aria-label={`回听片段 ${clock(part.start)}`}
                    onClick={() =>
                      onSeek(part.start, part.source_track ?? undefined)
                    }
                  >
                    {clock(part.start)}
                  </button>
                ))}
              </div>
            )}
          </>
        ) : (
          <textarea
            readOnly={readOnly}
            ref={textRef}
            className="transcript-textarea"
            aria-label={`转写文字 ${clock(segment.start)}`}
            rows={Math.max(1, Math.ceil(segment.text.length / 42))}
            value={segment.text}
            onChange={(e) => onUpdate({ ...segment, text: e.target.value })}
          />
        )}
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
  jumpVersion,
  onEditingChange,
  scrollRoot,
  activeSegment,
  readOnly = false,
  fullDom,
  hideVirtualBar = false,
}: {
  readOnly?: boolean;
  activeSegment?: string;
  scrollRoot?: RefObject<HTMLElement | null>;
  segments: (Segment & { reading_parts?: Segment[] })[];
  total: number;
  onUpdate: (segment: Segment) => void;
  onSeek: (time: number, track?: SourceTrack) => void;
  jumpSegmentId?: string;
  jumpVersion?: number;
  onEditingChange: (id: string | null) => void;
  fullDom?: boolean;
  hideVirtualBar?: boolean;
}) {
  const parent = useRef<HTMLDivElement>(null);
  const [full, setFull] = useState(false),
    [focused, setFocused] = useState<string | null>(null),
    [highlight, setHighlight] = useState<string | null>(null);
  const [pendingFocus, setPendingFocus] = useState<{
    id: string;
    last: boolean;
  } | null>(null);
  const effectiveFull = fullDom ?? full;
  const virtual = total > 300 && !effectiveFull;
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
    else {
      const row = parent.current?.querySelector(`[data-index="${index}"]`);
      const root = scrollRoot?.current;
      if (row && root)
        root.scrollTo({
          top: Math.max(
            0,
            root.scrollTop +
              row.getBoundingClientRect().top -
              root.getBoundingClientRect().top -
              root.clientHeight / 2,
          ),
        });
      else row?.scrollIntoView({ block: "center" });
    }
  }, [jumpSegmentId, jumpVersion, segments, virtual, list, scrollRoot]);
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
    if (event.altKey && (event.key === "r" || event.key === "R")) {
      event.preventDefault();
      const target =
        segments.find((s) => s.id === focused) ||
        segments.find((s) => s.id === activeSegment);
      if (target) onSeek(target.start, target.source_track ?? undefined);
      return;
    }
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
  const renderRow = (
    segment: (typeof segments)[number],
    index: number,
    start = 0,
  ) => (
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
      <Row
        playing={
          segment.id === activeSegment ||
          segment.reading_parts?.some((part) => part.id === activeSegment)
        }
        readOnly={readOnly}
        segment={segment}
        onUpdate={onUpdate}
        onSeek={onSeek}
      />
    </div>
  );
  return (
    <>
      {total > 300 && !hideVirtualBar && (
        <div className="transcript-virtual-mode-bar">
          <span className="virtual-mode-hint">
            <IconLightbulb size={13} /> 已启用大文稿流畅渲染（共 {total}{" "}
            段，当前视图 {segments.length} 段）
          </span>
          <label className="virtual-mode-toggle">
            <input
              type="checkbox"
              checked={full}
              onChange={(e) => setFull(e.target.checked)}
            />
            <span>展开全部 DOM（支持浏览器原生 ⌘F 查找）</span>
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
