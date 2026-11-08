import React from "react";
import type { SourceTrack } from "../../types";
import { clock } from "../../utils";

export interface UnifiedItem {
  id: string;
  type: "segment" | "note" | "decision" | "todo";
  timestamp: number;
  speaker?: string;
  sourceTrack?: SourceTrack | null;
  text: string;
  synthesizedText?: string;
  quote?: string;
  status?: string;
  owner?: string;
  dueDate?: string;
  anchorStart?: number;
  anchorEnd?: number;
}

export interface TimelineItemProps {
  item: UnifiedItem;
  isActive: boolean;
  onSeek: (time: number, track?: SourceTrack | null) => void;
}

export const TimelineItem: React.FC<TimelineItemProps> = React.memo(
  function TimelineItem({ item, isActive, onSeek }) {
    const timeStr = clock(item.timestamp);

    if (item.type === "segment") {
      const isSystem = item.sourceTrack === "system";
      return (
        <div
          id={`timeline-${item.id}`}
          className={`timeline-row segment-row ${isActive ? "active-segment" : ""}`}
        >
          <div className="timeline-gutter">
            <button
              type="button"
              className="time-tag-btn"
              onClick={() => onSeek(item.timestamp, item.sourceTrack)}
              title="跳转播放"
            >
              {timeStr}
            </button>
          </div>
          <div className="timeline-content">
            <div className="speaker-header">
              <span
                className={`speaker-pill ${isSystem ? "pill-system" : "pill-mic"}`}
              >
                {item.speaker || (isSystem ? "系统声音" : "发言人")}
              </span>
            </div>
            <p className="segment-text">{item.text}</p>
          </div>
        </div>
      );
    }

    if (item.type === "note") {
      const isSynthesizing = item.status === "synthesizing";
      const hasSynthesis =
        item.status === "ready" && Boolean(item.synthesizedText);

      return (
        <div id={`timeline-${item.id}`} className="timeline-row note-row">
          <div className="timeline-gutter">
            <button
              type="button"
              className="time-tag-btn note-time"
              onClick={() =>
                onSeek(item.anchorStart ?? item.timestamp, item.sourceTrack)
              }
              title="跳转记录点"
            >
              {timeStr}
            </button>
          </div>
          <div className="timeline-content note-bubble">
            <div className="note-header">
              <span className="badge-note">📝 伴随速记</span>
              {item.speaker && (
                <span className="note-speaker-hint">引用: {item.speaker}</span>
              )}
            </div>
            <div className="raw-note-body">{item.text}</div>

            {isSynthesizing && (
              <div className="synthesis-skeleton" aria-live="polite">
                <span className="pulse-dot" /> 端侧小模型智能扩充中...
              </div>
            )}

            {item.status === "failed" && <small>扩写未完成 · 原文已保存</small>}
            {hasSynthesis && (
              <div className="synthesis-card">
                <div className="synthesis-badge">扩写建议 · 请核对原文</div>
                <div className="synthesis-text">{item.synthesizedText}</div>
                {item.quote && <blockquote>{item.quote}</blockquote>}
                {item.anchorStart != null && item.anchorEnd != null && (
                  <div className="synthesis-anchor">
                    <span>
                      锚定原音: {clock(item.anchorStart)} -{" "}
                      {clock(item.anchorEnd)}
                    </span>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      );
    }

    if (item.type === "decision") {
      return (
        <div id={`timeline-${item.id}`} className="timeline-row decision-row">
          <div className="timeline-gutter">
            <button
              type="button"
              className="time-tag-btn decision-time"
              onClick={() => onSeek(item.timestamp)}
            >
              {timeStr}
            </button>
          </div>
          <div className="timeline-content decision-card">
            <div className="decision-header">
              <span className="badge-decision">📌 会议决议</span>
              {item.speaker && (
                <span className="note-speaker-hint">{item.speaker}</span>
              )}
            </div>
            <div className="decision-title">
              {item.synthesizedText || item.text}
            </div>
            {item.synthesizedText && item.text !== item.synthesizedText && (
              <div className="decision-context">{item.text}</div>
            )}
          </div>
        </div>
      );
    }

    // todo
    return (
      <div id={`timeline-${item.id}`} className="timeline-row todo-row">
        <div className="timeline-gutter">
          <button
            type="button"
            className="time-tag-btn todo-time"
            onClick={() => onSeek(item.timestamp)}
          >
            {timeStr}
          </button>
        </div>
        <div className="timeline-content todo-card">
          <div className="todo-header">
            <span className="badge-todo">⚡ 行动待办</span>
            {item.owner && (
              <span className="todo-owner-pill">负责人: {item.owner}</span>
            )}
            {item.dueDate && (
              <span className="todo-due-pill">期限: {item.dueDate}</span>
            )}
          </div>
          <div className="todo-title">{item.synthesizedText || item.text}</div>
          {item.synthesizedText && item.text !== item.synthesizedText && (
            <div className="todo-context">{item.text}</div>
          )}
        </div>
      </div>
    );
  },
);
