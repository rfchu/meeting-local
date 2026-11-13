import { createPortal } from "react-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import { liveChineseKey, useLiveChinese } from "./useLiveChinese";
import { draftSourceId } from "../overview/notes";
import { useLayoutEffect, useRef, useState } from "react";
import type {
  CopilotNote,
  LiveDraftSegment,
  MeetingNoteInput,
  SourceLanguage,
} from "../../types";
import { paragraphs } from "../transcript/paragraphs";
import { clock, trackLabel } from "../../utils";

export default function LiveDraftList({
  drafts,
  notes = [],
  copilotNotes = [],
  onAddNote,
  partial,
  emptyText,
  canAddNote = true,
  active = true,
  jump,
  recordingId,
  sourceLanguage,
  profiles = [],
  subtitleTarget,
  subtitleEnabled = false,
  onCollapseSubtitle,
  onConfigureModels,
}: {
  drafts: LiveDraftSegment[];
  notes?: MeetingNoteInput[];
  copilotNotes?: CopilotNote[];
  subtitleTarget?: HTMLDivElement | null;
  subtitleEnabled?: boolean;
  onCollapseSubtitle?: () => void;
  onConfigureModels?: () => void;
  recordingId?: string;
  sourceLanguage?: SourceLanguage;
  profiles?: { id: string; name: string }[];
  partial?: LiveDraftSegment | null;
  emptyText?: string;
  canAddNote?: boolean;
  active?: boolean;
  jump?: { id: string; nonce: number } | null;
  onAddNote: (draft: LiveDraftSegment) => void;
}) {
  const [filter, setFilter] = useState("all");
  useLayoutEffect(() => {
    if (jump) setFilter("all");
  }, [jump]);
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
  }, [jump, active, filter]);
  useLayoutEffect(() => {
    if (active && following.current && list.current)
      list.current.scrollTop = list.current.scrollHeight;
  }, [active]);
  const [bilingual, setBilingual] = useState(false);
  const queryClient = useQueryClient();
  const profileKey = ["live-translation-profile", recordingId] as const;
  const selection = useQuery({
    queryKey: profileKey,
    queryFn: () => api.liveTranslationProfile(recordingId!),
    enabled: !!recordingId && (bilingual || subtitleEnabled),
    refetchInterval: 1500,
  });
  const selectProfile = useMutation({
    mutationFn: (profileId: string) =>
      api.selectLiveTranslationProfile(recordingId!, profileId),
    onMutate: () => queryClient.cancelQueries({ queryKey: profileKey }),
    onSuccess: (value) => queryClient.setQueryData(profileKey, value),
    onSettled: () => queryClient.invalidateQueries({ queryKey: profileKey }),
  });
  const profile = selection.data?.profile_id || "";
  const latest = drafts.at(-1);
  const caption = partial || latest;
  const translation = useLiveChinese(
    subtitleEnabled ? (latest ? [latest] : []) : drafts,
    recordingId || "",
    profile,
    subtitleEnabled || bilingual,
    sourceLanguage,
  );
  useLayoutEffect(() => {
    if (bilingual && active && following.current && list.current)
      list.current.scrollTop = list.current.scrollHeight;
  }, [translation.entries, bilingual, active]);
  const groups = paragraphs(drafts);
  const located =
    active && jump
      ? drafts.find((part) => draftSourceId(part) === jump.id)
      : undefined;
  const captionTranslation =
    latest && !partial
      ? translation.entries[
          liveChineseKey(latest, recordingId || "", profile, sourceLanguage)
        ]?.result
      : undefined;
  return (
    <>
      {subtitleEnabled &&
        subtitleTarget &&
        createPortal(
          <section className="workbench-subtitle" aria-label="最新会中字幕">
            {caption && <time>{clock(caption.start)}</time>}
            <div>
              <p>{caption?.text || "等待实时文字…"}</p>
              {captionTranslation?.status === "translated" && (
                <p>{captionTranslation.text}</p>
              )}
            </div>
            <button className="text-btn" onClick={onCollapseSubtitle}>
              收起字幕
            </button>
          </section>,
          subtitleTarget,
        )}
      <div className="live-draft-follow">
        <label>
          显示
          <select
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          >
            <option value="all">全部</option>
            <option value="marker">仅标记</option>
            <option value="note">仅简记</option>
            <option value="accepted">已采纳</option>
          </select>
        </label>
        {recordingId && (
          <button
            className="text-btn"
            aria-pressed={bilingual}
            onClick={() => setBilingual((value) => !value)}
          >
            {bilingual ? "双语 · 切回原文" : "原文 · 切换双语"}
          </button>
        )}
        {bilingual && (
          <details className="workbench-transcript-menu">
            <summary>翻译设置</summary>
            <label>
              本场翻译模型
              <select
                value={profile}
                disabled={
                  !recordingId || selection.isPending || selectProfile.isPending
                }
                onChange={(event) => selectProfile.mutate(event.target.value)}
              >
                {!profile && (
                  <option value="" disabled>
                    {selection.isPending ? "正在读取…" : "请选择本地模型"}
                  </option>
                )}
                {profile && !profiles.some((item) => item.id === profile) && (
                  <option value={profile}>{profile}</option>
                )}
                {profiles.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
            {!profiles.length && onConfigureModels && (
              <button className="text-btn" onClick={onConfigureModels}>
                配置本地翻译模型
              </button>
            )}
            {(selectProfile.error || selection.error) && (
              <small role="alert">
                {(selectProfile.error || selection.error)?.message}
              </small>
            )}
          </details>
        )}
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
      {bilingual && (
        <small role="status">
          {!profile
            ? "请选择本地翻译模型，当前显示原文。"
            : translation.busy
              ? "本地模型忙碌，空闲后自动继续翻译。"
              : translation.remaining
                ? `自动翻译已确认片段 · ${translation.remaining} 段待处理`
                : "已确认片段显示原文和译文 · 临时文字显示原文"}
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
          if (!active) return;
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
        {filter === "all" && !drafts.length && !partial && (
          <p>{emptyText || "每约 15 秒更新一次，仅作会议跟随参考。"}</p>
        )}
        {[
          ...(filter === "all"
            ? groups.map((draft, index) => ({
                time: draft.start,
                node: (
                  <article
                    key={`${draft.start}-${index}`}
                    data-live-ids={
                      bilingual
                        ? undefined
                        : draft.parts
                            .map(draftSourceId)
                            .filter(Boolean)
                            .join(",")
                    }
                    className={[
                      index === groups.length - 1 && !partial
                        ? "live-draft-latest"
                        : "",
                      jump &&
                      draft.parts.some((p) => draftSourceId(p) === jump.id)
                        ? "live-draft-selected"
                        : "",
                    ]
                      .filter(Boolean)
                      .join(" ")}
                  >
                    <b>
                      {clock(draft.start)}
                      {draft.source_track && (
                        <small className="source-track-label">
                          {trackLabel(draft.source_track)}
                        </small>
                      )}
                    </b>
                    {draft.possible_echo &&
                    !(located && draft.parts.includes(located)) &&
                    !bilingual ? (
                      <details>
                        <summary>疑似外放回声 · 展开核对现场文字</summary>
                        <p>{draft.text}</p>
                      </details>
                    ) : bilingual && recordingId ? (
                      <div>
                        {draft.parts.map((part, partIndex) => {
                          const key = liveChineseKey(
                            part,
                            recordingId,
                            profile,
                            sourceLanguage,
                          );
                          const entry = translation.entries[key];
                          return (
                            <div
                              key={partIndex}
                              data-live-ids={draftSourceId(part) ?? undefined}
                              className={
                                part === located
                                  ? "live-bilingual-part live-source-selected"
                                  : "live-bilingual-part"
                              }
                            >
                              <p>
                                {part.text}{" "}
                                {entry?.result?.status === "unknown" && (
                                  <small>语言待确认</small>
                                )}
                                {key && profile && !entry && (
                                  <small>
                                    {translation.next === key &&
                                    !translation.busy
                                      ? "翻译中"
                                      : "等待翻译"}
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
                              {entry?.result?.status === "translated" && (
                                <p
                                  className="live-bilingual-translation"
                                  lang="zh-Hans"
                                >
                                  <small>中文译文</small> {entry.result.text}
                                </p>
                              )}
                            </div>
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
                        {draft.parts.some(
                          (part) => part.live_correction_id,
                        ) && (
                          <small className="live-corrected-badge">已校对</small>
                        )}
                      </p>
                    )}
                    {recordingId &&
                      located?.source_track &&
                      located.live_range_id &&
                      located.live_segment_index != null &&
                      draft.parts.includes(located) && (
                        <div className="live-source-player">
                          <small>
                            回听 · {trackLabel(located.source_track)}
                          </small>
                          <audio
                            key={jump?.nonce}
                            controls
                            preload="metadata"
                            aria-label={`回听 ${trackLabel(located.source_track)}`}
                            src={`/api/recordings/${recordingId}/live-audio/${located.live_range_id}/${located.live_segment_index}`}
                          />
                        </div>
                      )}
                    <button
                      className="text-btn"
                      disabled={!canAddNote}
                      onClick={() => onAddNote(draft)}
                    >
                      转为简记
                    </button>
                  </article>
                ),
              }))
            : []),
          ...notes
            .filter(
              (note) =>
                filter === "all" ||
                (filter === "marker" && note.is_marker) ||
                (filter === "note" && !note.is_marker),
            )
            .map((note, index) => ({
              time: note.captured_at,
              node: (
                <article key={note.client_id || `note-${index}`}>
                  <b>{clock(note.captured_at)}</b>
                  <p>
                    {note.is_marker ? "标记" : "我的简记"} ·{" "}
                    {note.raw_text || "标记此刻"}
                  </p>
                </article>
              ),
            })),
          ...(filter === "marker"
            ? []
            : copilotNotes
                .filter((note) => {
                  const accepted =
                    note.note_type === "decision" ||
                    note.note_type === "action";
                  return (
                    filter === "all" ||
                    (filter === "accepted" ? accepted : !accepted)
                  );
                })
                .map((note) => ({
                  time: note.captured_at,
                  node: (
                    <article key={note.id}>
                      <b>{clock(note.captured_at)}</b>
                      <p>
                        {note.note_type === "decision" ||
                        note.note_type === "action"
                          ? "已采纳"
                          : "速记"}{" "}
                        · {note.raw_text}
                        {note.synthesized_text && (
                          <small>{note.synthesized_text}</small>
                        )}
                      </p>
                    </article>
                  ),
                }))),
        ]
          .sort((a, b) => a.time - b.time)
          .map(({ node }) => node)}
        {filter !== "all" ? null : partial?.tracks?.length ? (
          partial.tracks.map((track) => (
            <article
              key={track.source_track}
              className="live-draft-latest live-draft-partial"
              aria-label="未确认的临时文字"
            >
              <b>
                {clock(track.start)}
                {track.source_track && (
                  <small className="source-track-label">
                    {trackLabel(track.source_track)}
                  </small>
                )}
              </b>
              <p>
                {track.stable_text &&
                track.text.startsWith(track.stable_text) ? (
                  <>
                    <span className="live-stable-prefix">
                      {track.stable_text}
                    </span>
                    <span className="live-revisable-tail">
                      {track.text.slice(track.stable_text.length)}
                    </span>
                  </>
                ) : (
                  track.text
                )}
                <span className="stream-caret" aria-hidden="true" />
              </p>
              <small>
                {track.stable_text
                  ? "前缀趋稳 · 尾部可修订"
                  : "临时文字 · 待确认"}
              </small>
            </article>
          ))
        ) : partial?.text ? (
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
        ) : null}
      </div>
    </>
  );
}
