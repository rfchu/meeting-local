import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import type { LiveDraftSegment, SpeculativeCandidate } from "../../types";
import { draftSourceId } from "../overview/notes";
import { ExtractedSidebar } from "./ExtractedSidebar";
import { SpeculativeChips } from "./SpeculativeChips";
import { TimelineItem, type UnifiedItem } from "./TimelineItem";
import "./copilot.css";

export function LiveCopilot({
  recordingId,
  confirmed,
  partial,
  elapsed,
  onLocate,
}: {
  recordingId: string;
  confirmed: LiveDraftSegment[];
  partial: LiveDraftSegment | null;
  elapsed: number;
  onLocate: (id: string) => void;
}) {
  const client = useQueryClient();
  const notes = useQuery({
    queryKey: ["copilot-notes", recordingId],
    queryFn: () => api.shorthandNotes(recordingId),
    refetchInterval: 4000,
    retry: false,
  });
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

  const preview = partial?.tracks?.length
    ? partial.tracks
    : partial
      ? [partial]
      : [];
  const segments = confirmed.slice(-100);
  const items: UnifiedItem[] = [
    ...segments.map((part, index): UnifiedItem => ({
      id: draftSourceId(part) ?? `preview-${index}`,
      type: "segment",
      timestamp: part.start,
      sourceTrack: part.source_track,
      text: part.text,
    })),
    ...(notes.data || []).map((note): UnifiedItem => ({
      id: note.id,
      type:
        note.note_type === "action"
          ? "todo"
          : note.note_type === "decision"
            ? "decision"
            : "note",
      timestamp: note.captured_at,
      text: note.raw_text,
      status: note.status,
      speaker: note.speaker_hint,
      synthesizedText: note.synthesized_text,
      quote: note.shorthand_quote,
      anchorStart: note.anchor_start ?? undefined,
      anchorEnd: note.anchor_end ?? undefined,
      owner: note.owner,
      dueDate: note.due_date,
    })),
  ].sort((a, b) => a.timestamp - b.timestamp);

  function locate(time: number, id?: string) {
    const segment =
      segments.find((part) => draftSourceId(part) === id) ||
      segments.find((part) => part.start <= time && part.end >= time);
    const sourceId = segment && draftSourceId(segment);
    if (sourceId) onLocate(sourceId);
    else setMessage("该时点暂无可定位的实时文字，录音结束后可回听原音");
  }

  return (
    <div className="unified-timeline-layout">
      <section className="timeline-main-pane" aria-label="会中时间线">
        <div className="timeline-stream-container">
          {items.map((item) => (
            <TimelineItem
              key={item.id}
              item={item}
              isActive={false}
              onSeek={(time) => locate(time, item.id)}
            />
          ))}
          {preview.map((part, index) => (
            <p key={index} className="muted">
              临时文字 ·{" "}
              {part.source_track === "microphone"
                ? "麦克风"
                : part.source_track === "system"
                  ? "电脑声音"
                  : ""}{" "}
              {part.text}
            </p>
          ))}
        </div>
        <SpeculativeChips
          candidates={candidates.data?.candidates || []}
          onAccept={(candidate) => resolve(candidate, true)}
          onDismiss={(candidate) => resolve(candidate, false)}
        />
        <form
          className="quick-shorthand-bar"
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
        <small role="status">
          {message || (notes.isError ? "暂时无法读取已保存速记" : "")}
        </small>
      </section>
      <ExtractedSidebar items={items} onLocate={locate} />
    </div>
  );
}
