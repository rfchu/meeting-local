import React from "react";
import type { UnifiedItem } from "./TimelineItem";
import { clock } from "../../utils";

export interface ExtractedSidebarProps {
  items: UnifiedItem[];
  onLocate: (timestamp: number, itemId: string) => void;
}

export const ExtractedSidebar: React.FC<ExtractedSidebarProps> = React.memo(
  function ExtractedSidebar({ items, onLocate }) {
    const decisions = items.filter((i) => i.type === "decision");
    const todos = items.filter((i) => i.type === "todo");
    const notes = items.filter((i) => i.type === "note");

    return (
      <aside className="copilot-sidebar">
        <div className="sidebar-header">
          <h3>实时萃取看板</h3>
          <p className="sidebar-subtitle">点击条目自动对齐时间线与原音</p>
        </div>

        <div className="sidebar-section">
          <div className="section-title">
            <span>📌 核心决议</span>
            <span className="count-tag">{decisions.length}</span>
          </div>
          {decisions.length === 0 ? (
            <div className="empty-hint">暂未识别到确认决议</div>
          ) : (
            <ul className="sidebar-card-list">
              {decisions.map((d) => (
                <li key={d.id} className="sidebar-card decision-border">
                  <button
                    type="button"
                    className="copilot-locate"
                    onClick={() => onLocate(d.anchorStart ?? d.timestamp, d.id)}
                  >
                    <div className="card-top">
                      <span className="card-time">{clock(d.timestamp)}</span>
                      {d.speaker && (
                        <span className="card-speaker">{d.speaker}</span>
                      )}
                    </div>
                    <div className="card-content">
                      {d.synthesizedText || d.text}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="sidebar-section">
          <div className="section-title">
            <span>⚡ 行动项清单</span>
            <span className="count-tag">{todos.length}</span>
          </div>
          {todos.length === 0 ? (
            <div className="empty-hint">暂无待办事项</div>
          ) : (
            <ul className="sidebar-card-list">
              {todos.map((t) => (
                <li key={t.id} className="sidebar-card todo-border">
                  <button
                    type="button"
                    className="copilot-locate"
                    onClick={() => onLocate(t.anchorStart ?? t.timestamp, t.id)}
                  >
                    <div className="card-top">
                      <span className="card-time">{clock(t.timestamp)}</span>
                      {t.owner && (
                        <span className="badge-owner">{t.owner}</span>
                      )}
                      {t.dueDate && (
                        <span className="badge-due">{t.dueDate}</span>
                      )}
                    </div>
                    <div className="card-content">
                      {t.synthesizedText || t.text}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="sidebar-section">
          <div className="section-title">
            <span>📝 速记与扩充</span>
            <span className="count-tag">{notes.length}</span>
          </div>
          {notes.length === 0 ? (
            <div className="empty-hint">敲入笔记后在此聚合</div>
          ) : (
            <ul className="sidebar-card-list">
              {notes.map((n) => (
                <li key={n.id} className="sidebar-card note-border">
                  <button
                    type="button"
                    className="copilot-locate"
                    onClick={() => onLocate(n.anchorStart ?? n.timestamp, n.id)}
                  >
                    <div className="card-top">
                      <span className="card-time">{clock(n.timestamp)}</span>
                      {n.status === "synthesizing" && (
                        <span className="badge-busy">扩写中</span>
                      )}
                    </div>
                    <div className="card-content">
                      <strong>{n.text}</strong>
                      {n.synthesizedText && (
                        <div className="card-preview-text">
                          {n.synthesizedText}
                        </div>
                      )}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </aside>
    );
  },
);
