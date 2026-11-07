import { useEffect, useRef, useState } from "react";

export type ChineseResult = {
  status: "original" | "unknown" | "translated";
  text: string;
  start: number;
  end: number;
};

export default function ChineseRecordLine({
  original,
  initial,
  missing,
  translate,
}: {
  original: string;
  initial?: ChineseResult;
  missing?: boolean;
  translate?: (signal: AbortSignal) => Promise<ChineseResult>;
}) {
  const [latest, setResult] = useState<ChineseResult>();
  const result = latest || initial;
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  return (
    <div>
      <span>{result?.text ?? original}</span>{" "}
      {result?.status === "translated" && <small>中文译文</small>}
      {result?.status === "unknown" && <small>语言待确认</small>}
      {missing && !result && <small>待翻译</small>}
      {error && <small role="alert">{error}</small>}
      {pending && (
        <button
          className="text-btn"
          onClick={() => {
            request.current?.abort();
            request.current = null;
            setPending(false);
            setError("已取消等待，原文保留。");
          }}
        >
          取消等待
        </button>
      )}
      {translate && !result && (
        <button
          className="text-btn"
          disabled={pending}
          onClick={async () => {
            if (request.current) return;
            const controller = new AbortController();
            request.current = controller;
            setPending(true);
            setError("");
            try {
              const value = await translate(controller.signal);
              if (!controller.signal.aborted) setResult(value);
            } catch (e) {
              if (!controller.signal.aborted) setError((e as Error).message);
            } finally {
              if (request.current === controller) {
                request.current = null;
                if (!controller.signal.aborted) setPending(false);
              }
            }
          }}
        >
          {pending
            ? "正在处理译文…"
            : error
              ? "重试本段"
              : missing
                ? "补译本段"
                : "查看中文"}
        </button>
      )}
    </div>
  );
}
