import type { LiveDraftSegment } from "../../types";
export interface LiveStreamResult {
  confirmed?: boolean;
  first_text_ms?: number | null;
  segments: LiveDraftSegment[];
  duration: number;
  processing_ms: number;
}
export interface LivePartial {
  start: number;
  end: number;
  text: string;
}
export async function readLiveStream<T extends LiveStreamResult>(
  path: string,
  init: RequestInit,
  onPartial: (partial: LivePartial) => void,
): Promise<T> {
  const response = await fetch(`/api${path}`, init);
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || response.statusText);
  }
  if (!response.body) throw new Error("浏览器不支持流式响应，请关闭逐字推送");
  const reader = response.body.getReader(),
    decoder = new TextDecoder("utf-8", { fatal: true });
  let buffer = "",
    sequence = -1,
    requestId = "";
  const consume = (line: string): T | undefined => {
    if (!line.trim()) return;
    const event = JSON.parse(line);
    if (!Number.isInteger(event.seq) || typeof event.request_id !== "string")
      throw new Error("流式事件格式无效");
    if (requestId && requestId !== event.request_id)
      throw new Error("流式请求标识不一致");
    requestId = event.request_id;
    if (event.seq <= sequence) return;
    if (event.seq !== sequence + 1)
      throw new Error("流式事件序列缺失，部分文字未确认");
    sequence = event.seq;
    if (event.type === "error")
      throw new Error(event.message || "流式识别失败");
    if (event.type === "partial") {
      if (
        typeof event.text !== "string" ||
        !Number.isFinite(event.start) ||
        !Number.isFinite(event.end)
      )
        throw new Error("流式文字格式无效");
      onPartial({ start: event.start, end: event.end, text: event.text });
    } else if (event.type === "final") {
      if (!event.result || !Array.isArray(event.result.segments))
        throw new Error("流式最终结果无效");
      return event.result as T;
    } else if (event.type !== "start") throw new Error("未知流式事件");
  };
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += done
        ? decoder.decode()
        : decoder.decode(value, { stream: true });
      if (buffer.length > 256 * 1024) throw new Error("流式事件超过长度限制");
      let newline: number;
      while ((newline = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, newline);
        buffer = buffer.slice(newline + 1);
        const final = consume(line);
        if (final) return final;
      }
      if (done) {
        const final = consume(buffer);
        if (final) return final;
        throw new Error("流式连接提前结束，部分文字未确认为完整结果");
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
