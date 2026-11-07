export type SourceDecision = "reviewed" | "keep_history" | "reset";
export interface HandoffSource {
  source_id: string;
  fingerprint: string;
  kind: string;
  label: string;
  status: "current" | "changed" | "missing" | "unavailable";
  before: string;
  translation_zh?: string;
  current: string;
  detail: string;
  target: {
    item_id?: string;
    artifact_id?: string;
    note_id?: string;
    segment_id?: string;
    start?: number;
  } | null;
  feedback: {
    decision: SourceDecision;
    created: string;
    stale: boolean;
  } | null;
}
export interface HandoffSources {
  revision: number;
  artifact_id: string | null;
  artifact_revision: number | null;
  artifacts: { id: string; revision: number; created: string }[];
  reason_codes: string[];
  total: number;
  offset: number;
  counts: { changed: number; processed: number };
  items: HandoffSource[];
}
export function feedbackLabel(item: HandoffSource): string {
  if (!item.feedback) return "待处理";
  if (item.feedback.stale) return "来源又有变化，需重新处理";
  return {
    reviewed: "已人工核对",
    keep_history: "已标记保留历史",
    reset: "已撤回处理",
  }[item.feedback.decision];
}
export function sourceCommandKey(
  artifactId: string,
  item: HandoffSource,
  decision: SourceDecision,
) {
  return JSON.stringify([
    artifactId,
    item.source_id,
    item.fingerprint,
    decision,
  ]);
}
