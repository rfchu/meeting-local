import { useLayoutEffect, useRef, useState } from "react";
import type { LiveDraftSegment } from "../../types";
import { clock } from "../../utils";

export default function LiveDraftList({
  drafts,
  onAddNote,
  partial,
  emptyText,
}: {
  drafts: LiveDraftSegment[];
  partial?: LiveDraftSegment | null;
  emptyText?: string;
  onAddNote: (draft: LiveDraftSegment) => void;
}) {
  const list = useRef<HTMLDivElement>(null),
    following = useRef(true),
    seenCount = useRef(0);
  const [follow, setFollow] = useState(true),
    [unread, setUnread] = useState(0);
  const jumpToLatest = () => {
    following.current = true;
    setFollow(true);
    setUnread(0);
    if (list.current) list.current.scrollTop = list.current.scrollHeight;
  };
  useLayoutEffect(() => {
    const added = Math.max(0, drafts.length - seenCount.current);
    seenCount.current = drafts.length;
    if (following.current) {
      if (list.current) list.current.scrollTop = list.current.scrollHeight;
      setUnread(0);
    } else if (added) setUnread((old) => old + added);
  }, [drafts, partial?.text]);
  return (
    <>
      <div className="live-draft-follow">
        <span>{follow ? "自动跟随最新结果" : "正在回看 · 已暂停跟随"}</span>
        <button
          className="text-btn"
          onClick={() => {
            if (follow) {
              following.current = false;
              setFollow(false);
            } else jumpToLatest();
          }}
        >
          {follow
            ? "暂停跟随"
            : `回到最新${unread ? `（${unread} 条新结果）` : ""}`}
        </button>
      </div>
      <div
        className="live-draft-list"
        ref={list}
        role="log"
        aria-label="实时识别结果"
        aria-live="polite"
        aria-relevant="additions text"
        tabIndex={0}
        onScroll={() => {
          const element = list.current;
          if (!element) return;
          const atBottom =
            element.scrollHeight - element.scrollTop - element.clientHeight <=
            32;
          if (atBottom) {
            following.current = true;
            setFollow(true);
            setUnread(0);
          } else if (following.current) {
            following.current = false;
            setFollow(false);
          }
        }}
      >
        {!drafts.length && !partial && (
          <p>{emptyText || "每约 15 秒更新一次，仅作会议跟随参考。"}</p>
        )}
        {drafts.map((draft, index) => (
          <article
            key={`${draft.start}-${index}`}
            className={
              index === drafts.length - 1 && !partial
                ? "live-draft-latest"
                : undefined
            }
          >
            <b>{clock(draft.start)}</b>
            <p>{draft.text}</p>
            <button className="text-btn" onClick={() => onAddNote(draft)}>
              转为简记
            </button>
          </article>
        ))}
        {partial && (
          <article
            className="live-draft-latest live-draft-partial"
            aria-label="未确认的临时文字"
          >
            <b>{clock(partial.start)}</b>
            <p>
              {partial.text}
              <span className="stream-caret" aria-hidden="true" />
            </p>
            <small>
              生成中
              <br />
              未确认
            </small>
          </article>
        )}
      </div>
    </>
  );
}
