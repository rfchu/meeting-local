import { useEffect, useRef, useState } from "react";
import { api } from "../../api";
import type { LiveCorrection, LiveDraftSegment } from "../../types";

export function useLiveCorrections(
  recordingId: string | undefined,
  drafts: LiveDraftSegment[],
  enabled: boolean,
  profileId: string,
  active: boolean,
) {
  const [corrections, setCorrections] = useState<LiveCorrection[]>([]);
  const [status, setStatus] = useState("");
  const seen = useRef(new Set<string>()),
    busy = useRef(false),
    mounted = useRef(true);
  const current = useRef({ recordingId, drafts, enabled, profileId, active });
  current.current = { recordingId, drafts, enabled, profileId, active };
  const merge = (value: LiveCorrection) =>
    setCorrections((old) => [...old.filter((x) => x.id !== value.id), value]);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    setCorrections([]);
    seen.current.clear();
    setStatus("");
    if (!recordingId) return;
    let valid = true;
    api
      .liveCorrections(recordingId)
      .then((values) => {
        if (valid) setCorrections(values);
      })
      .catch(() => {});
    return () => {
      valid = false;
    };
  }, [recordingId]);
  useEffect(() => {
    const pump = async () => {
      if (busy.current) return;
      busy.current = true;
      try {
        while (mounted.current) {
          const state = current.current;
          if (
            !state.enabled ||
            !state.active ||
            !state.recordingId ||
            !state.profileId
          )
            break;
          const draft = state.drafts.find(
            (s) =>
              s.live_range_id &&
              !seen.current.has(
                `${state.profileId}:${s.live_range_id}:${s.live_segment_index}`,
              ),
          );
          if (!draft) break;
          seen.current.add(
            `${state.profileId}:${draft.live_range_id}:${draft.live_segment_index}`,
          );
          setStatus("本地模型正在校对已确认片段…");
          try {
            const value = await api.proposeLiveCorrection(
              state.recordingId,
              draft.live_range_id!,
              draft.live_segment_index || 0,
              state.profileId,
            );
            if (
              mounted.current &&
              current.current.recordingId === state.recordingId
            ) {
              merge(value);
              setStatus(
                value.edits.length
                  ? "校对完成，修改建议已保存"
                  : "最近片段校对完成",
              );
            }
          } catch {
            if (mounted.current)
              setStatus(
                "本地纠错暂时不可用，原始文字已保存；可在纠错记录中重试",
              );
          }
        }
      } finally {
        busy.current = false;
      }
    };
    void pump();
  }, [recordingId, drafts, enabled, profileId, active]);
  return { corrections, status, setCorrections };
}
