import { useState } from "react";
import Modal from "../../components/Modal";
import type { Artifact, MeetingMinutes } from "../../types";
import {
  compareDocuments,
  minutesSectionLabels,
  minutesVersionDescription,
} from "./presentation";

export default function MinutesCompare({
  left,
  right,
  onClose,
  onAdopt,
  blockedReason,
  onSaveSelection,
}: {
  left: Artifact<MeetingMinutes>;
  right: Artifact<MeetingMinutes>;
  onClose: () => void;
  onAdopt: () => void;
  blockedReason?: string;
  onSaveSelection?: (document: MeetingMinutes) => Promise<void>;
}) {
  const [changesOnly, setChangesOnly] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const comparison = compareDocuments(left.payload, right.payload);
  const unchanged =
    !comparison.metadataChanges &&
    !comparison.leftChanges &&
    !comparison.rightChanges;
  async function saveSelection() {
    if (!onSaveSelection || saving || blockedReason) return;
    const document = { ...left.payload };
    for (const section of comparison.sections) {
      const key = section.key as keyof typeof minutesSectionLabels;
      document[key] = [
        ...section.left.filter((_, i) => !picked.includes(`left:${key}:${i}`)),
        ...section.right.filter((_, i) => picked.includes(`right:${key}:${i}`)),
      ].map((row) => row.item);
    }
    setSaving(true);
    setError("");
    try {
      await onSaveSelection(document);
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }
  return (
    <Modal id="minutesCompareDialog" onClose={onClose} ariaLabel="对比纪要版本">
      <div className="compare-header">
        <h2>对比纪要版本</h2>
        <button onClick={onClose}>关闭</button>
      </div>
      <p>
        两个版本独立保存。采用右侧版本后，会议成果和跟进以它为准；原版本仍可查看。
      </p>
      <div className="minutes-diff-toolbar">
        <p role="status">
          {unchanged
            ? "正文及基本信息一致。"
            : `基本信息 ${comparison.metadataChanges} 项变化；左侧 ${comparison.leftChanges} 条移除或改写，右侧 ${comparison.rightChanges} 条新增或改写。`}
        </p>
        <label>
          <input
            type="checkbox"
            checked={changesOnly}
            onChange={(e) => setChangesOnly(e.target.checked)}
          />
          只看差异
        </label>
      </div>
      <p className="minutes-diff-help">
        按文字、负责人、期限、状态及原文引用逐项比对；高亮表示条目不同，不代表事实已核实。条目顺序调整不算增删。
      </p>
      <div className="minutes-compare-grid">
        {[left, right].map((artifact, index) => {
          const side = index === 0 ? "left" : "right";
          return (
            <section
              key={artifact.id}
              aria-label={index === 0 ? "左侧版本" : "右侧版本"}
            >
              <h3>
                {index === 0 ? "原版本" : "待采用版本"} ·{" "}
                {artifact.payload.title}
              </h3>
              <p className="minutes-version-description">
                {minutesVersionDescription(artifact)} · 文字稿 v
                {artifact.revision}
                {artifact.stale ? " · 已过期" : ""}
              </p>
              {comparison.metadata
                .filter(
                  (row) =>
                    (!changesOnly || row.changed) && (row.left || row.right),
                )
                .map((row) => (
                  <p
                    key={row.label}
                    className={
                      row.changed
                        ? `minutes-diff-row ${side}`
                        : "minutes-diff-row"
                    }
                  >
                    <strong>{row.label}：</strong>
                    {row[side] || "未填写"}
                  </p>
                ))}
              {comparison.sections.map((section) => {
                const rows = section[side]
                  .map((row, index) => ({ ...row, index }))
                  .filter((row) => !changesOnly || row.changed);
                const changed =
                  section.left.some((row) => row.changed) ||
                  section.right.some((row) => row.changed);
                if (changesOnly && !changed) return null;
                return (
                  <div key={section.key}>
                    <h4>{section.title}</h4>
                    {rows.length ? (
                      rows.map(({ item, changed, reason, index: rowIndex }) => {
                        const choice = `${side}:${section.key}:${rowIndex}`;
                        return (
                          <div
                            key={rowIndex}
                            className={
                              changed
                                ? `minutes-diff-row ${side}`
                                : "minutes-diff-row"
                            }
                          >
                            {changed && (
                              <span className="minutes-diff-tag">
                                {index === 0 ? "移除／改写" : "新增／改写"}
                              </span>
                            )}
                            {onSaveSelection && changed && (
                              <label>
                                <input
                                  type="checkbox"
                                  disabled={saving || !!blockedReason}
                                  checked={picked.includes(choice)}
                                  onChange={(event) =>
                                    setPicked(
                                      event.target.checked
                                        ? [...picked, choice]
                                        : picked.filter(
                                            (key) => key !== choice,
                                          ),
                                    )
                                  }
                                />
                                {side === "left"
                                  ? "从采用稿移除此条"
                                  : "加入此条"}
                              </label>
                            )}
                            <p>
                              {item.content}
                              {item.owner ? ` · ${item.owner}` : ""}
                              {item.due_date ? ` · ${item.due_date}` : ""}
                              {item.status ? ` · ${item.status}` : ""}
                            </p>
                            {changed && (
                              <small>
                                {reason && <span>{reason} · </span>}原文依据：
                                {(item.source_segment_ids || []).length} 段
                              </small>
                            )}
                          </div>
                        );
                      })
                    ) : (
                      <p className="minutes-diff-help">
                        {changesOnly ? "此侧无变化条目" : "无内容"}
                      </p>
                    )}
                  </div>
                );
              })}
              {changesOnly && unchanged && <p>没有需要对比的差异。</p>}
            </section>
          );
        })}
      </div>
      {onSaveSelection && (
        <p className="muted">
          勾选要移除或加入的条目，保存为待采用草稿。改写一条内容时，请同时勾选旧条目和新条目。
        </p>
      )}
      {error && <p role="alert">{error}</p>}
      <div className="minutes-compare-footer">
        {onSaveSelection && (
          <button
            disabled={!picked.length || saving || !!blockedReason}
            onClick={() => void saveSelection()}
          >
            {saving ? "正在保存…" : `保存所选差异为草稿（${picked.length}）`}
          </button>
        )}
        <button
          className="primary"
          disabled={saving || right.stale || !!blockedReason}
          onClick={onAdopt}
        >
          采用右侧版本
        </button>
        {(blockedReason || right.stale) && (
          <span>
            {blockedReason || "右侧版本已过期，请根据当前来源重新生成。"}
          </span>
        )}
      </div>
    </Modal>
  );
}
