import { liveChineseKey, useLiveChinese } from "./useLiveChinese";
import { draftSourceId } from "../overview/notes";
import { useLayoutEffect, useRef, useState } from "react";
import type { LiveDraftSegment } from "../../types";
import { paragraphs } from "../transcript/paragraphs";
import { clock } from "../../utils";

export default function LiveDraftList({
  drafts,
  onAddNote,
  partial,
  emptyText,
  canAddNote = true,
  active = true,
  jump,
  recordingId,
  profiles = [],
}: {
  drafts: LiveDraftSegment[];
  recordingId?: string;
  profiles?: { id: string; name: string }[];
  partial?: LiveDraftSegment | null;
  emptyText?: string;
  canAddNote?: boolean;
  active?: boolean;
  jump?: { id: string; nonce: number } | null;
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
  useLayoutEffect(() => {
    if (!active || !jump || !list.current) return;
    const target = [
      ...list.current.querySelectorAll<HTMLElement>("[data-live-ids]"),
    ].find((el) => el.dataset.liveIds?.split(",").includes(jump.id));
    if (target) {
      following.current = false;
      setFollow(false);
      target.scrollIntoView({ block: "center" });
    }
  }, [jump, active]);
  useLayoutEffect(() => {
    if (active && following.current && list.current)
      list.current.scrollTop = list.current.scrollHeight;
  }, [active]);
  const [chinese, setChinese] = useState(false);
  const [model, setModel] = useState("");
  const profile = profiles.some((item) => item.id === model)
    ? model
    : profiles[0]?.id || "";
  const translation = useLiveChinese(
    drafts,
    recordingId || "",
    profile,
    chinese,
  );
  useLayoutEffect(() => {
    if (chinese && active && following.current && list.current)
      list.current.scrollTop = list.current.scrollHeight;
  }, [translation.entries, chinese, active]);
  const groups = paragraphs(drafts);
  return (
    <>
      <div className="live-draft-follow">
        {recordingId && (
          <button
            className="text-btn"
            aria-pressed={chinese}
            onClick={() => setChinese(!chinese)}
          >
            {chinese ? "中文视图已开启 · 切回原文" : "开启中文视图"}
          </button>
        )}
        {chinese && (
          <label>
            本地翻译模型
            <select
              value={profile}
              onChange={(event) => setModel(event.target.value)}
            >
              {!profiles.length && <option value="">未配置本地模型</option>}
              {profiles.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
        )}
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
      {chinese && (
        <small role="status">
          {!profile
            ? "请选择本地翻译模型，当前显示原文。"
            : translation.busy
              ? "本地模型忙碌，空闲后自动继续翻译。"
              : translation.remaining
                ? `自动翻译已确认片段 · ${translation.remaining} 段待处理`
                : "已确认的英文片段自动翻译 · 临时文字保留原文"}
        </small>
      )}
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
        {groups.map((draft, index) => (
          <article
            key={`${draft.start}-${index}`}
            data-live-ids={draft.parts
              .map(draftSourceId)
              .filter(Boolean)
              .join(",")}
            className={[
              index === groups.length - 1 && !partial
                ? "live-draft-latest"
                : "",
              jump && draft.parts.some((p) => draftSourceId(p) === jump.id)
                ? "live-draft-selected"
                : "",
            ]
              .filter(Boolean)
              .join(" ")}
          >
            <b>{clock(draft.start)}</b>
            {chinese && recordingId ? (
              <div>
                {draft.parts.map((part, partIndex) => {
                  const key = liveChineseKey(part, recordingId, profile);
                  const entry = translation.entries[key];
                  return (
                    <p key={partIndex}>
                      {entry?.result?.text ?? part.text}{" "}
                      {entry?.result?.status === "translated" && (
                        <small>中文译文</small>
                      )}
                      {entry?.result?.status === "unknown" && (
                        <small>语言待确认</small>
                      )}
                      {key && profile && !entry && (
                        <small>
                          {translation.next === key && !translation.busy
                            ? "翻译中"
                            : "等待翻译 · 原文"}
                        </small>
                      )}
                      {entry?.error && (
                        <>
                          <small role="alert">{entry.error}</small>{" "}
                          <button
                            className="text-btn"
                            onClick={() => translation.retry(key)}
                          >
                            重试本段
                          </button>
                        </>
                      )}
                    </p>
                  );
                })}
              </div>
            ) : (
              <p>
                {draft.text}
                {draft.parts.length > 1 && (
                  <small className="live-continuation-label">
                    连续发言 · {draft.parts.length} 个音频片段
                  </small>
                )}
                {draft.parts.some((part) => part.live_correction_id) && (
                  <small className="live-corrected-badge">已校对</small>
                )}
              </p>
            )}
            <button
              className="text-btn"
              disabled={!canAddNote}
              onClick={() => onAddNote(draft)}
            >
              转为简记
            </button>
          </article>
        ))}
        {partial && (
          <article
            className="live-draft-latest live-draft-partial"
            aria-label="未确认的临时文字"
          >
            <b>
              {clock(partial.start)}
              {partial.continues_previous && (
                <small className="live-continuation-label">延续上一段</small>
              )}
            </b>
            <p>
              {partial.stable_text &&
              partial.text.startsWith(partial.stable_text) ? (
                <>
                  <span className="live-stable-prefix">
                    {partial.stable_text}
                  </span>
                  <span>{partial.text.slice(partial.stable_text.length)}</span>
                </>
              ) : (
                partial.text
              )}
              <span className="stream-caret" aria-hidden="true" />
            </p>
            <small>
              正在识别
              <br />
              {partial.stable_text ? "前缀趋稳 · 尾部可修订" : "尾部可修订"}
            </small>
          </article>
        )}
      </div>
    </>
  );
}
