import React, { useCallback, useMemo } from "react";
import type { Meeting, SourceTrack } from "../../types";
import { TimelineItem, type UnifiedItem } from "./TimelineItem";
import { ExtractedSidebar } from "./ExtractedSidebar";
import "./copilot.css";

export interface UnifiedTimelineProps {
  meeting: Meeting;
  activeSegment?: string;
  onSeek: (time: number, track?: SourceTrack | null) => void;
}

export const UnifiedTimeline: React.FC<UnifiedTimelineProps> = ({
  meeting,
  activeSegment,
  onSeek,
}) => {
  // Combine segments, notes, and todos into a single chronological stream
  const unifiedItems = useMemo<UnifiedItem[]>(() => {
    const list: UnifiedItem[] = [];

    // 1. Transcript segments
    for (const seg of meeting.segments || []) {
      list.push({
        id: seg.id,
        type: "segment",
        timestamp: seg.start,
        speaker: seg.speaker,
        sourceTrack: seg.source_track,
        text: seg.text,
      });
    }

    // 2. Meeting notes
    for (const note of meeting.notes || []) {
      const isDecision = note.note_type === "decision";
      const isTodo = note.note_type === "action";

      list.push({
        id: note.id,
        type: isDecision ? "decision" : isTodo ? "todo" : "note",
        timestamp: note.captured_at,
        speaker: note.speaker_hint,
        text: note.raw_text,
        synthesizedText: note.synthesized_text,
        quote: note.shorthand_quote,
        status: note.status,
        sourceTrack: note.shorthand_track || undefined,
        anchorStart: note.anchor_start,
        anchorEnd: note.anchor_end,
      });
    }

    // 3. Note Todos
    for (const todo of meeting.note_todos || []) {
      // Avoid duplicate display if already in notes list
      const source = list.find((item) => item.id === todo.note_id);
      if (source?.type === "todo") {
        source.owner = todo.owner;
        source.dueDate = todo.due_date;
      } else {
        list.push({
          id: todo.id,
          type: "todo",
          timestamp: source?.timestamp ?? 0,
          text: todo.content,
          owner: todo.owner,
          dueDate: todo.due_date,
        });
      }
    }

    // Sort by timestamp ascending
    list.sort((a, b) => a.timestamp - b.timestamp);
    return list;
  }, [meeting]);

  const handleLocate = useCallback(
    (timestamp: number, itemId: string) => {
      const item = unifiedItems.find((entry) => entry.id === itemId);
      onSeek(item?.anchorStart ?? timestamp, item?.sourceTrack);
      const element = document.getElementById(`timeline-${itemId}`);
      if (element) {
        element.scrollIntoView({ behavior: "smooth", block: "center" });
        element.classList.add("highlight-flash");
        window.setTimeout(() => {
          element.classList.remove("highlight-flash");
        }, 1200);
      }
    },
    [onSeek, unifiedItems],
  );

  return (
    <div className="unified-timeline-layout">
      <div className="timeline-main-pane">
        <div className="timeline-stream-container">
          {unifiedItems.length === 0 ? (
            <div className="empty-timeline">
              <span className="empty-icon">⏳</span>
              <p>转写或速记生成后，将在此呈现连续时间线</p>
            </div>
          ) : (
            <div className="timeline-stream">
              {unifiedItems.map((item) => (
                <TimelineItem
                  key={item.id}
                  item={item}
                  isActive={item.id === activeSegment}
                  onSeek={onSeek}
                />
              ))}
            </div>
          )}
        </div>
      </div>

      <ExtractedSidebar items={unifiedItems} onLocate={handleLocate} />
    </div>
  );
};
