import { useEffect, useState } from "react";
import { api, APIError } from "../../api";
import type { LiveDraftSegment, SourceLanguage } from "../../types";
import type { ChineseResult } from "./ChineseRecordLine";

type Entry = { result?: ChineseResult; error?: string };

export function liveChineseKey(
  part: LiveDraftSegment,
  recordingId: string,
  profile: string,
  sourceLanguage?: SourceLanguage,
) {
  if (
    !part.live_range_id ||
    part.live_segment_index == null ||
    (sourceLanguage !== "ja" &&
      (!/[A-Za-z]/.test(part.text) || /[\u3400-\u9fff]/.test(part.text)))
  )
    return "";
  return JSON.stringify([
    recordingId,
    profile,
    part.live_range_id,
    part.live_segment_index,
    part.text,
  ]);
}

export function useLiveChinese(
  drafts: LiveDraftSegment[],
  recordingId: string,
  profile: string,
  enabled: boolean,
  sourceLanguage?: SourceLanguage,
) {
  const [entries, setEntries] = useState<Record<string, Entry>>({});
  const [busy, setBusy] = useState(false);
  const keys = drafts
    .map((part) => liveChineseKey(part, recordingId, profile, sourceLanguage))
    .filter(Boolean);
  const next =
    enabled && recordingId && profile
      ? keys.find((key) => !entries[key])
      : undefined;

  useEffect(() => {
    if (!next) return;
    const controller = new AbortController();
    const [recording, model, range, index, text] = JSON.parse(next) as [
      string,
      string,
      string,
      number,
      string,
    ];
    let timer: number | undefined;
    const run = async () => {
      setBusy(false);
      try {
        const result = await api.translateLiveSegment(
          recording,
          range,
          index,
          model,
          text,
          controller.signal,
        );
        if (!controller.signal.aborted)
          setEntries((old) => ({ ...old, [next]: { result } }));
      } catch (error) {
        if (controller.signal.aborted) return;
        if (
          error instanceof APIError &&
          (error.code === "translation_busy" ||
            error.code === "translation_profile_changed")
        ) {
          setBusy(true);
          timer = window.setTimeout(() => void run(), 2000);
          return;
        }
        setEntries((old) => ({
          ...old,
          [next]: { error: (error as Error).message },
        }));
      }
    };
    // Leave a turn for the overview monitor before taking the shared model again.
    timer = window.setTimeout(() => void run(), 1100);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [next]);

  return {
    entries,
    next,
    busy: !!next && busy,
    remaining: keys.filter((key) => !entries[key]).length,
    retry: (key: string) =>
      setEntries((old) => {
        const updated = { ...old };
        delete updated[key];
        return updated;
      }),
  };
}
