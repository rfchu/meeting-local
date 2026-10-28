import { useState, useRef, useEffect } from "react";
import type { Meeting } from "../../types";
import TranscriptList from "../transcript/TranscriptList";
export default function ReviewSourcePane({
  meeting,
  focusTime,
  focusVersion,
  onSeek,
  onEdit,
}: {
  meeting: Meeting;
  focusTime: number | null;
  focusVersion: number;
  onSeek: (time: number) => void;
  onEdit: () => void;
}) {
  const pane = useRef<HTMLElement>(null);
  const [query, setQuery] = useState("");
  useEffect(() => {
    if (focusTime !== null) setQuery("");
  }, [focusVersion]);
  const focused =
    focusTime === null
      ? undefined
      : meeting.segments.find(
          (s) => s.start <= focusTime && s.end > focusTime,
        ) || meeting.segments.find((s) => s.start >= focusTime);
  const segments = query
    ? meeting.segments.filter(
        (s) => s.text.includes(query) || s.speaker.includes(query),
      )
    : meeting.segments;
  return (
    <section
      ref={pane}
      className="review-source-pane"
      aria-label="整理时查看原文"
    >
      <div className="review-pane-heading">
        <h3>原文与回听</h3>
        <button onClick={onEdit}>校对文字稿</button>
      </div>
      <p>原文 v{meeting.revision} · 点击时间戳回听</p>
      <input
        aria-label="整理原文搜索"
        placeholder="搜索原文或发言人"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      {focused && (
        <button className="text-btn" onClick={() => setQuery("")}>
          返回所选关注点附近
        </button>
      )}
      {meeting.segments.length ? (
        <TranscriptList
          readOnly
          scrollRoot={pane}
          segments={segments}
          total={meeting.segments.length}
          jumpSegmentId={query ? undefined : focused?.id}
          onSeek={onSeek}
          onUpdate={() => {}}
          onEditingChange={() => {}}
        />
      ) : (
        <p>文字稿尚未完成。录音与关注点已保存，可先整理个人记录。</p>
      )}
    </section>
  );
}
