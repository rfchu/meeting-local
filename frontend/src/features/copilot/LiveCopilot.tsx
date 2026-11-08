import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import type { SpeculativeCandidate } from "../../types";
import { SpeculativeChips } from "./SpeculativeChips";
import "./copilot.css";

export function LiveCopilot({
  recordingId,
  elapsed,
}: {
  recordingId: string;
  elapsed: number;
}) {
  const client = useQueryClient();
  const candidates = useQuery({
    queryKey: ["copilot-candidates", recordingId],
    queryFn: () => api.candidates(recordingId),
    refetchInterval: 4000,
    retry: false,
  });
  const [text, setText] = useState("");
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);
  const request = useRef<{
    id: string;
    text: string;
    synthesize: boolean;
    time: number;
  } | null>(null);
  const refresh = () =>
    client.invalidateQueries({ queryKey: ["copilot-notes", recordingId] });

  async function submit(synthesize: boolean) {
    const raw = text.trim();
    if (!raw || saving) return;
    if (
      request.current?.text !== raw ||
      request.current.synthesize !== synthesize
    ) {
      request.current = {
        id: crypto.randomUUID(),
        text: raw,
        synthesize,
        time: elapsed,
      };
    }
    const payload = request.current;
    setSaving(true);
    setMessage("正在保存…");
    try {
      await api.postShorthand(recordingId, {
        raw_text: payload.text,
        captured_at: payload.time,
        synthesize: payload.synthesize,
        client_id: payload.id,
      });
      setText("");
      request.current = null;
      setMessage(synthesize ? "速记已保存，等待扩写" : "速记已保存");
      void refresh();
    } catch {
      setMessage("保存失败，原文保留，可重试");
    } finally {
      setSaving(false);
    }
  }

  async function resolve(candidate: SpeculativeCandidate, accept: boolean) {
    try {
      if (accept)
        await api.acceptCandidate(recordingId, candidate.candidate_id);
      else await api.rejectCandidate(recordingId, candidate.candidate_id);
      setMessage(accept ? "已采纳候选" : "已忽略候选");
      await Promise.all([refresh(), candidates.refetch()]);
    } catch {
      setMessage("操作失败，请重试");
    }
  }

  return (
    <div className="live-transcript-copilot">
      <SpeculativeChips
        candidates={candidates.data?.candidates || []}
        onAccept={(candidate) => resolve(candidate, true)}
        onDismiss={(candidate) => resolve(candidate, false)}
      />
      <form
        className="live-transcript-shorthand"
        onSubmit={(e) => {
          e.preventDefault();
          void submit(false);
        }}
      >
        <input
          className="quick-input"
          aria-label="会中速记"
          placeholder="记录此刻的重点"
          value={text}
          readOnly={saving}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.nativeEvent.isComposing) return;
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              void submit(true);
            }
          }}
        />
        <button type="submit" disabled={!text.trim() || saving}>
          保存原文
        </button>
        <button
          type="button"
          disabled={!text.trim() || saving}
          onClick={() => void submit(true)}
        >
          保存并扩写
        </button>
      </form>
      <small role="status">{message}</small>
    </div>
  );
}
