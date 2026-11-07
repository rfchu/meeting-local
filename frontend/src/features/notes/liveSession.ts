import type { LiveDraftSegment, SourceLanguage } from "../../types";
import type {
  DecoderMetrics,
  DecoderTotals,
  LiveStreamResult,
} from "./liveStream";
export interface SessionConfig {
  source_language?: SourceLanguage;
  engine?: "qwen" | "whisper";
  responsive: boolean;
  context_enabled: boolean;
  terms: string;
  prefix_enabled?: boolean;
}
export interface SessionSnapshot {
  protocol_version?: number;
  engine?: "qwen" | "whisper";
  recording_id?: string;
  type: "snapshot";
  seq: number;
  status: string;
  segments: LiveDraftSegment[];
  partial: LiveDraftSegment | Record<string, never>;
  cursor: number;
  source: "native" | "browser";
  config: SessionConfig;
  metrics: {
    load?: {
      mode: "balanced" | "saving";
      rtf: number;
      preview_step_seconds: number;
      backlog_seconds: number;
    };
    processing_ms?: number;
    first_text_ms?: number | null;
    duration?: number;
    preview_step_seconds?: number;
    extending?: boolean;
    boundary_reason?: string;
    decoder?: DecoderMetrics;
    decoder_totals?: DecoderTotals;
  };
}
export type SessionEvent =
  | SessionSnapshot
  | {
      type: "partial";
      seq: number;
      partial: LiveDraftSegment;
      metrics?: SessionSnapshot["metrics"];
    }
  | {
      type: "final";
      seq: number;
      result: LiveStreamResult & {
        load?: SessionSnapshot["metrics"]["load"];
        next_offset: number;
        skipped_seconds: number;
      };
    }
  | { type: "status"; seq: number; message: string }
  | { type: "heartbeat" | "ended"; seq: number };

export async function watchLiveSession(
  id: string,
  after: number,
  onEvent: (event: SessionEvent) => void,
  signal: AbortSignal,
) {
  const response = await fetch(
    `/api/recordings/${id}/live-session/events?after=${after}`,
    { signal },
  );
  if (!response.ok || !response.body) throw new Error("实时连接暂时不可用");
  const reader = response.body.getReader(),
    decoder = new TextDecoder("utf-8", { fatal: true });
  let buffer = "",
    cursor = after;
  const consume = (line: string) => {
    if (!line.trim()) return false;
    const event = JSON.parse(line) as SessionEvent & {
      protocol_version?: number;
      recording_id?: string;
      engine?: string;
    };
    if (event.protocol_version !== undefined && event.protocol_version !== 1)
      throw new Error("实时事件协议版本不支持");
    if (event.recording_id !== undefined && event.recording_id !== id)
      throw new Error("实时事件来自其他录音");
    if (
      event.engine !== undefined &&
      !["qwen", "whisper"].includes(event.engine)
    )
      throw new Error("实时事件识别引擎无效");
    if (!Number.isSafeInteger(event.seq) || event.seq < 0)
      throw new Error("实时事件序号无效");
    if (event.type === "heartbeat") return false;
    if (event.type === "ended") {
      onEvent(event);
      return true;
    }
    if (event.type === "snapshot") {
      if (
        !Array.isArray(event.segments) ||
        !event.partial ||
        typeof event.partial !== "object"
      )
        throw new Error("实时快照无效");
    } else {
      if (event.seq <= cursor) return false;
      if (event.seq !== cursor + 1)
        throw new Error("实时事件缺失，正在重新同步");
      if (!["partial", "final", "status"].includes(event.type))
        throw new Error("未知实时事件");
    }
    if (
      event.type === "partial" &&
      (typeof event.partial?.text !== "string" ||
        !Number.isFinite(event.partial.start) ||
        !Number.isFinite(event.partial.end))
    )
      throw new Error("实时草稿无效");
    if (event.type === "final" && !Array.isArray(event.result?.segments))
      throw new Error("实时确认结果无效");
    cursor = event.seq;
    onEvent(event);
    return false;
  };
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += done
        ? decoder.decode()
        : decoder.decode(value, { stream: true });
      if (buffer.length > 8 * 1024 * 1024) throw new Error("实时事件超出范围");
      let next: number;
      while ((next = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, next);
        buffer = buffer.slice(next + 1);
        if (consume(line)) return;
      }
      if (done) {
        if (buffer.trim() && consume(buffer)) return;
        throw new Error("实时连接中断，正在重新连接");
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
