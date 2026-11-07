import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, APIError } from "../../api";
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
  activeSegment,
  onContinue,
}: {
  id: string;
  revision: number;
  dirty: boolean;
  profiles: LLMConfig[];
  activeSegment?: string;
  onSeek: (time: number) => void;
  onContinue: (profileId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState("");
  const [offset, setOffset] = useState(0);
  const profile = profiles.find((item) => item.id === selected) || profiles[0];
  const scope = JSON.stringify([
    id,
    revision,
    profile?.id,
    profile?.model,
    profile?.base_url,
    offset,
  ]);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [waiting, setWaiting] = useState(false);
  const record = useQuery({
    queryKey: ["chinese-record", scope],
    queryFn: () => api.chineseMeetingRecord(id, profile?.id || "", offset),
    enabled: open && !dirty,
  });
  const data = record.data?.revision === revision ? record.data : undefined;
  const counts = data?.counts;
  const missing = counts ? counts.pending + counts.unknown : 0;
  const pending = data?.items.filter((item) => item.status === "pending") ?? [];
  const errorKey = (segment: string) => JSON.stringify([scope, segment]);
  const next =
    open && !dirty && profile && !record.isError
      ? pending.find((item) => !errors[errorKey(item.id)])
      : undefined;
  const nextId = next?.id;
  const nextOriginal = next?.original;
  const profileId = profile?.id;
  const { refetch } = record;

  useEffect(() => {
    if (!nextId || nextOriginal == null || !profileId) return;
    const controller = new AbortController();
    let timer: number | undefined;
    const translate = async () => {
      setWaiting(false);
      try {
        await api.translateMeetingSegment(
          id,
          nextId,
          revision,
          profileId,
          nextOriginal,
          controller.signal,
        );
        if (controller.signal.aborted) return;
        const refreshed = await refetch();
        if (refreshed.isError) throw refreshed.error;
      } catch (error) {
        if (controller.signal.aborted) return;
        if (
          error instanceof APIError &&
          error.status === 409 &&
          (error.code === "translation_busy" ||
            /^(等待|会中识别优先|资源请求数量达到上限)/.test(error.message))
        ) {
          setWaiting(true);
          timer = window.setTimeout(() => void translate(), 2000);
          return;
        }
        const key = JSON.stringify([scope, nextId]);
        setErrors((old) => ({ ...old, [key]: (error as Error).message }));
      }
    };
    void translate();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [id, revision, profileId, scope, nextId, nextOriginal, refetch]);

  return (
    <details onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>双语视图</summary>
      {open &&
        (dirty ? (
          <p>请先保存文字稿修改，再打开双语视图。</p>
        ) : (
          <section className="meeting-bilingual-record" aria-label="双语视图">
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
                  ? "正在读取双语记录…"
                  : "文字稿版本已变化，请刷新会议后查看。"}
              </p>
            )}
            {data && counts && (
              <>
                <p>
                  中文原文 {counts.original} 段 · 译文 {counts.translated} 段 ·
                  待翻译 {counts.pending} 段 · 语言待确认 {counts.unknown} 段
                </p>
                {pending.length > 0 && (
                  <p role="status">
                    {!profile
                      ? "请选择本地模型以翻译本页。"
                      : next && waiting
                        ? "本地模型忙碌，空闲后自动继续翻译。"
                        : next
                          ? "正在自动翻译本页待译片段。"
                          : "本页仍有未完成的片段，可逐段重试。"}
                  </p>
                )}
                {!data.total && <p>生成文字稿后可打开双语视图。</p>}
                {data.total > 0 && (
                  <button onClick={() => onContinue(profile?.id || "")}>
                    {missing
                      ? "使用当前可用内容，继续整理纪要"
                      : "继续整理纪要"}
                  </button>
                )}
                {data.items.map((item) => (
                  <article
                    className={
                      activeSegment === item.id
                        ? "bilingual-playing"
                        : undefined
                    }
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
                      result={
                        item.status === "pending"
                          ? undefined
                          : { ...item, status: item.status }
                      }
                      status={
                        item.status === "unknown"
                          ? "语言待确认"
                          : item.status === "pending" &&
                              !errors[errorKey(item.id)]
                            ? nextId === item.id && !waiting
                              ? "翻译中"
                              : "待翻译"
                            : undefined
                      }
                      error={
                        item.status === "pending"
                          ? errors[errorKey(item.id)]
                          : undefined
                      }
                      onRetry={() =>
                        setErrors((old) => {
                          const updated = { ...old };
                          delete updated[errorKey(item.id)];
                          return updated;
                        })
                      }
                    />
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
