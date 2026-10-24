import { useState } from "react";
import Modal from "../../components/Modal";
import type { Artifact, MeetingMinutes } from "../../types";
import { compareDocuments, minutesVersionDescription } from "./presentation";

export default function MinutesCompare({
  left,
  right,
  onClose,
  onAdopt,
  blockedReason,
}: {
  left: Artifact<MeetingMinutes>;
  right: Artifact<MeetingMinutes>;
  onClose: () => void;
  onAdopt: () => void;
  blockedReason?: string;
}) {
  const [changesOnly, setChangesOnly] = useState(false);
  const comparison = compareDocuments(left.payload, right.payload);
  const unchanged =
    !comparison.metadataChanges &&
    !comparison.leftChanges &&
    !comparison.rightChanges;
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
                const rows = changesOnly
                  ? section[side].filter((row) => row.changed)
                  : section[side];
                const changed =
                  section.left.some((row) => row.changed) ||
                  section.right.some((row) => row.changed);
                if (changesOnly && !changed) return null;
                return (
                  <div key={section.key}>
                    <h4>{section.title}</h4>
                    {rows.length ? (
                      rows.map(({ item, changed, reason }, i) => (
                        <div
                          key={i}
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
                      ))
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
      <div className="minutes-compare-footer">
        <button
          className="primary"
          disabled={right.stale || !!blockedReason}
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
