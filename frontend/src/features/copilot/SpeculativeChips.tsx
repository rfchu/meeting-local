import React, { useEffect, useState } from "react";
import type { SpeculativeCandidate } from "../../types";

export interface SpeculativeChipsProps {
  candidates: SpeculativeCandidate[];
  onAccept: (candidate: SpeculativeCandidate) => void | Promise<void>;
  onDismiss: (candidate: SpeculativeCandidate) => void | Promise<void>;
}

export const SpeculativeChips: React.FC<SpeculativeChipsProps> = ({
  candidates,
  onAccept,
  onDismiss,
}) => {
  const [expired, setExpired] = useState<Set<string>>(() => new Set());
  const [busy, setBusy] = useState(false);
  const [focused, setFocused] = useState(false);
  const current = candidates.find((item) => !expired.has(item.candidate_id));
  const currentId = current?.candidate_id;
  useEffect(() => {
    if (!currentId || focused || busy) return;
    const timer = window.setTimeout(() => {
      setExpired((old) => new Set(old).add(currentId));
    }, 10000);
    return () => window.clearTimeout(timer);
  }, [currentId, focused, busy]);

  if (!current) return null;
  const act = async (action: typeof onAccept) => {
    if (busy) return;
    setBusy(true);
    try {
      await action(current);
    } finally {
      setBusy(false);
    }
  };

  const isTodo = current.kind === "todo";

  return (
    <div
      className={`speculative-chip-container chip-fade-in`}
      onFocus={() => setFocused(true)}
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget)) setFocused(false);
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape" && !e.nativeEvent.isComposing) {
          e.stopPropagation();
          void act(onDismiss);
        }
      }}
      role="group"
      aria-label="会中候选"
      aria-live="polite"
    >
      <div
        className={`speculative-chip ${isTodo ? "chip-todo" : "chip-decision"}`}
      >
        <span className="chip-badge">
          {isTodo ? "⚡ 识别到待办" : "📌 识别到决议"}
        </span>
        <span className="chip-text" title={current.content}>
          {current.title}
        </span>
        <div className="chip-actions">
          <button
            type="button"
            className="chip-btn-accept"
            disabled={busy}
            onClick={() => void act(onAccept)}
            title="采纳候选"
          >
            采纳
          </button>
          <button
            type="button"
            className="chip-btn-dismiss"
            disabled={busy}
            onClick={() => void act(onDismiss)}
            title="快捷键: Esc"
          >
            <kbd>Esc</kbd> 忽略
          </button>
        </div>
      </div>
    </div>
  );
};
