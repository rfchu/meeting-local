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
  draftId,
  dirty,
  profiles,
  onSeek,
  activeSegment,
  onContinue,
  embedded = false,
}: {
  id: string;
  revision: number;
  draftId?: string;
  dirty: boolean;
  profiles: LLMConfig[];
  activeSegment?: string;
  onSeek: (time: number) => void;
  onContinue?: (profileId: string) => void;
  embedded?: boolean;
}) {
  const [open, setOpen] = useState(embedded);
  const [selected, setSelected] = useState("");
  const [offset, setOffset] = useState(0);
  const profile = profiles.find((item) => item.id === selected) || profiles[0];
  const scope = JSON.stringify([
    id,
    revision,
    draftId,
    profile?.id,
    profile?.model,
    profile?.base_url,
    offset,
  ]);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [waiting, setWaiting] = useState(false);
  const record = useQuery({
    queryKey: ["chinese-record", scope],
    queryFn: () =>
      api.chineseMeetingRecord(id, profile?.id || "", offset, draftId),
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
          draftId,
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
  }, [id, revision, draftId, profileId, scope, nextId, nextOriginal, refetch]);

  const recordBody = dirty ? (
    <div className="bilingual-dirty-notice">
      <p>⚠️ 文字稿正在编辑或有未保存修改，请先保存修改后再查看双语对照。</p>
    </div>
  ) : (
    <section className="meeting-bilingual-record" aria-label="双语视图">
      {draftId && (
        <p className="bilingual-draft-hint">
          译文依据实验性识别稿生成。请按音频块回听核对姓名、数字和日期。
        </p>
      )}
      <div className="bilingual-model-bar">
        <label>
          本地翻译模型：{" "}
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
      </div>
      {record.isError && (
        <p role="alert" className="error">
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
          <div className="bilingual-summary-badge">
            中文原文 {counts.original} 段 · 译文 {counts.translated} 段 ·
            待翻译 {counts.pending} 段 · 语言待确认 {counts.unknown} 段
          </div>
          {pending.length > 0 && (
            <p role="status" className="bilingual-progress-status">
              {!profile
                ? "请选择本地模型以翻译本页。"
                : next && waiting
                  ? "本地模型忙碌，空闲后自动继续翻译。"
                  : next
                    ? "正在自动翻译本页待译片段…"
                    : "本页仍有未完成的片段，可逐段重试。"}
            </p>
          )}
          {!data.total && <p className="muted">生成文字稿后可打开双语视图。</p>}
          {data.total > 0 && onContinue && (
            <button
              className="primary btn-sm bilingual-continue-btn"
              onClick={() => onContinue(profile?.id || "")}
            >
              {missing
                ? "使用当前可用内容，继续整理纪要"
                : "继续整理纪要"}
            </button>
          )}
          <div className="bilingual-items-list">
            {data.items.map((item) => (
              <article
                className={
                  activeSegment === item.id
                    ? "bilingual-card bilingual-playing"
                    : "bilingual-card"
                }
                key={JSON.stringify([
                  revision,
                  profile?.id,
                  profile?.model,
                  profile?.base_url,
                  item.id,
                ])}
              >
                <div className="bilingual-card-header">
                  <button
                    className="btn-timestamp"
                    onClick={() => onSeek(item.start)}
                  >
                    ▶ {draftId ? "回听音频块" : "回听"} {clock(item.start)}–
                    {clock(item.end)}
                  </button>
                </div>
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
          </div>
          {data.total > pageSize && (
            <div className="bilingual-pagination">
              <button
                disabled={!offset}
                onClick={() => setOffset(offset - pageSize)}
              >
                上一页
              </button>{" "}
              <span>
                {offset + 1}–{offset + data.items.length} / {data.total}
              </span>{" "}
              <button
                disabled={offset + pageSize >= data.total}
                onClick={() => setOffset(offset + pageSize)}
              >
                下一页
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );

  if (embedded) {
    return <div className="bilingual-embedded-container">{recordBody}</div>;
  }

  return (
    <details onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>{draftId ? "查看原文与中文译文" : "双语视图"}</summary>
      {open && recordBody}
    </details>
  );
}
