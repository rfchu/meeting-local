import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import Modal from "../../components/Modal";
import { api } from "../../api";
import type { Meeting } from "../../types";
import {
  feedbackLabel,
  sourceCommandKey,
  type HandoffSource,
  type SourceDecision,
} from "./handoffSources";

export default function HandoffSourcesDialog({
  meeting,
  dirty,
  onClose,
  onLocate,
  onContinue,
}: {
  meeting: Meeting;
  dirty: boolean;
  onClose: () => void;
  onLocate: (source: HandoffSource) => void;
  onContinue?: () => void;
}) {
  const [artifactId, setArtifactId] = useState(meeting.active_minutes_id || "");
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const command = useRef<{ key: string; id: string } | null>(null);
  const query = useQuery({
    queryKey: ["handoff-sources", meeting.id, artifactId, offset],
    queryFn: () =>
      api.handoffSources(meeting.id, artifactId || undefined, offset),
    refetchOnWindowFocus: true,
  });
  const report = query.data;
  useEffect(() => {
    if (report?.artifact_id && !artifactId) setArtifactId(report.artifact_id);
  }, [report?.artifact_id, artifactId]);
  async function process(item: HandoffSource, decision: SourceDecision) {
    if (!report?.artifact_id || dirty || busy || query.isFetching) return;
    const key = sourceCommandKey(report.artifact_id, item, decision);
    if (command.current?.key !== key)
      command.current = { key, id: crypto.randomUUID() };
    setBusy(true);
    setMessage("");
    try {
      await api.reviewHandoffSource(meeting.id, {
        artifact_id: report.artifact_id,
        source_id: item.source_id,
        fingerprint: item.fingerprint,
        decision,
        request_id: command.current.id,
        confirmed: true,
      });
      command.current = null;
      setMessage(
        decision === "reset"
          ? "已撤回这条处理记录。"
          : "已保存人工处理记录；来源有效性保持独立判断。",
      );
      await query.refetch();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "保存失败，请重试");
      await query.refetch();
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      ariaLabel="具体来源与人工处理"
      className="handoff-sources-dialog"
      onClose={onClose}
    >
      <div className="overview-heading">
        <h2>来源与处理</h2>
        <button onClick={onClose}>关闭</button>
      </div>
      <p className="muted">
        逐条查看引用和整理依据。人工核对与保留历史只记录处理选择，不会修复过期来源、采用纪要或确认交付。
      </p>
      {dirty && <p role="status">有未保存修改，请先保存后再处理来源。</p>}
      {query.isError && (
        <p role="alert">
          {query.error.message}{" "}
          <button onClick={() => void query.refetch()}>重试读取</button>
        </p>
      )}
      {report && (
        <>
          <div className="source-toolbar">
            <label>
              检查纪要{" "}
              <select
                aria-label="检查纪要版本"
                disabled={busy}
                value={artifactId}
                onChange={(e) => {
                  setArtifactId(e.target.value);
                  setOffset(0);
                  setMessage("");
                }}
              >
                {!report.artifacts.length && <option value="">尚无纪要</option>}
                {report.artifacts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.id === meeting.active_minutes_id
                      ? "采用稿 · "
                      : "历史／草稿 · "}
                    v{a.revision} · {a.created} · {a.id.slice(0, 6)}
                  </option>
                ))}
              </select>
            </label>
            <button
              disabled={busy || query.isFetching}
              onClick={() => void query.refetch()}
            >
              刷新来源
            </button>
          </div>
          <p>
            {report.artifact_id
              ? `纪要原文 v${report.artifact_revision} → 当前 v${report.revision} · ${report.total} 条来源 · ${report.counts.changed} 条需留意 · ${report.counts.processed} 条已处理`
              : "尚无纪要。生成后可在此检查具体来源。"}
          </p>
          {report.reason_codes.length > 0 && (
            <p className="source-warning">
              这份纪要的来源已变化或无法核验，请核对后更新。记录“已人工核对”不会消除该提醒。
            </p>
          )}
          <div className="source-cards">
            {report.items.map((item) => (
              <article
                key={item.source_id}
                className={`source-card ${item.status}`}
              >
                <header>
                  <strong>{item.label}</strong>
                  <span>
                    {
                      {
                        current: "来源可对照",
                        changed: "来源变化",
                        missing: "来源已缺失",
                        unavailable: "无法比较",
                      }[item.status]
                    }
                  </span>
                </header>
                <div className="source-comparison">
                  <div>
                    <small>生成时／历史依据</small>
                    <p>{item.before || "无可用历史快照"}</p>
                  </div>
                  <div>
                    <small>当前来源</small>
                    <p>{item.current || "当前记录不存在"}</p>
                  </div>
                </div>
                <p className="muted">{item.detail}</p>
                <div className="source-controls">
                  <span>{feedbackLabel(item)}</span>
                  {item.target && (
                    <button
                      disabled={dirty || busy}
                      onClick={() => {
                        if (item.target?.artifact_id) {
                          setArtifactId(item.target.artifact_id);
                          setOffset(0);
                          setMessage("");
                        } else onLocate(item);
                      }}
                    >
                      {" "}
                      {item.target.artifact_id
                        ? "检查来源纪要"
                        : item.target.note_id
                          ? "打开这条简记"
                          : item.target.item_id
                            ? "打开这条会中概览"
                            : "定位当前原文"}
                    </button>
                  )}
                  {(["reviewed", "keep_history", "reset"] as SourceDecision[])
                    .filter(
                      (d) =>
                        d !== "reset" ||
                        (item.feedback && item.feedback.decision !== "reset"),
                    )
                    .map((decision) => (
                      <button
                        className="text-btn"
                        key={decision}
                        disabled={
                          dirty ||
                          busy ||
                          query.isFetching ||
                          (!!item.feedback &&
                            !item.feedback.stale &&
                            item.feedback.decision === decision)
                        }
                        onClick={() => void process(item, decision)}
                      >
                        {
                          {
                            reviewed: "确认已核对",
                            keep_history: "标记保留历史",
                            reset: "撤回处理",
                          }[decision]
                        }
                      </button>
                    ))}
                </div>
              </article>
            ))}
          </div>
          {report.total > 25 && (
            <div className="source-pagination">
              <button
                disabled={busy || offset === 0}
                onClick={() => setOffset(Math.max(0, offset - 25))}
              >
                上一页
              </button>
              <span>
                {offset + 1}–{Math.min(offset + 25, report.total)} /{" "}
                {report.total}
              </span>
              <button
                disabled={busy || offset + 25 >= report.total}
                onClick={() => setOffset(offset + 25)}
              >
                下一页
              </button>
            </div>
          )}
        </>
      )}
      {!report && query.isPending && <p role="status">正在读取来源…</p>}
      {message && <p role="status">{message}</p>}
      {onContinue && (
        <button disabled={busy || dirty} onClick={onContinue}>
          回到会后下一步
        </button>
      )}
    </Modal>
  );
}
