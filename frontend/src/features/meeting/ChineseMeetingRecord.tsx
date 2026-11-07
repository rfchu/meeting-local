import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api";
import { clock } from "../../utils";
import type { LLMConfig } from "../../types";
import ChineseRecordLine from "../notes/ChineseRecordLine";

const pageSize = 50;

export default function ChineseMeetingRecord({
  id,
  revision,
  dirty,
  profiles,
  onSeek,
  onContinue,
}: {
  id: string;
  revision: number;
  dirty: boolean;
  profiles: LLMConfig[];
  onSeek: (time: number) => void;
  onContinue: (profileId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState("");
  const [offset, setOffset] = useState(0);
  const profile = profiles.find((item) => item.id === selected) || profiles[0];
  const record = useQuery({
    queryKey: [
      "chinese-record",
      id,
      revision,
      profile?.id,
      profile?.model,
      profile?.base_url,
      offset,
    ],
    queryFn: () => api.chineseMeetingRecord(id, profile?.id || "", offset),
    enabled: open && !dirty,
  });
  const data = record.data?.revision === revision ? record.data : undefined;
  const counts = data?.counts;
  const missing = counts ? counts.pending + counts.unknown : 0;
  return (
    <details onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>中文会议记录</summary>
      {open &&
        (dirty ? (
          <p>请先保存文字稿修改，再查看对应的中文记录。</p>
        ) : (
          <section aria-label="中文会议记录">
            <label>
              本地翻译模型{" "}
              <select
                value={profile?.id || ""}
                onChange={(event) => {
                  setSelected(event.target.value);
                  setOffset(0);
                }}
              >
                {!profiles.length && <option value="">未配置本地模型</option>}
                {profiles.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
            {record.isError && (
              <p role="alert">
                {(record.error as Error).message}{" "}
                <button
                  className="text-btn"
                  onClick={() => void record.refetch()}
                >
                  重试读取
                </button>
              </p>
            )}
            {!data && !record.isError && (
              <p role="status">
                {record.isFetching
                  ? "正在读取中文记录…"
                  : "文字稿版本已变化，请刷新会议后查看。"}
              </p>
            )}
            {data && counts && (
              <>
                <p>
                  中文原文 {counts.original} 段 · 译文 {counts.translated} 段 ·
                  待翻译 {counts.pending} 段 · 语言待确认 {counts.unknown} 段
                </p>
                {!data.total && <p>生成文字稿后可查看中文记录。</p>}
                {data.total > 0 && (
                  <button onClick={() => onContinue(profile?.id || "")}>
                    {missing
                      ? "使用当前可用内容，继续整理纪要"
                      : "继续整理纪要"}
                  </button>
                )}
                {data.items.map((item) => (
                  <article
                    key={JSON.stringify([
                      revision,
                      profile?.id,
                      profile?.model,
                      profile?.base_url,
                      item.id,
                    ])}
                  >
                    <button
                      className="text-btn"
                      onClick={() => onSeek(item.start)}
                    >
                      回听 {clock(item.start)}–{clock(item.end)}
                    </button>
                    <ChineseRecordLine
                      original={item.original}
                      initial={
                        item.status === "pending"
                          ? undefined
                          : { ...item, status: item.status }
                      }
                      missing={item.status === "pending"}
                      translate={
                        item.status === "pending" && profile
                          ? async (signal) => {
                              const value = await api.translateMeetingSegment(
                                id,
                                item.id,
                                revision,
                                profile.id,
                                item.original,
                                signal,
                              );
                              void record.refetch();
                              return value;
                            }
                          : undefined
                      }
                    />
                    {item.status === "translated" && (
                      <details>
                        <summary>查看原文</summary>
                        <p>{item.original}</p>
                      </details>
                    )}
                  </article>
                ))}
                {data.total > pageSize && (
                  <p>
                    <button
                      disabled={!offset}
                      onClick={() => setOffset(offset - pageSize)}
                    >
                      上一页
                    </button>{" "}
                    {offset + 1}–{offset + data.items.length} / {data.total}{" "}
                    <button
                      disabled={offset + pageSize >= data.total}
                      onClick={() => setOffset(offset + pageSize)}
                    >
                      下一页
                    </button>
                  </p>
                )}
              </>
            )}
          </section>
        ))}
    </details>
  );
}
