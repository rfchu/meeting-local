import { useState } from "react";

export type ChineseResult = {
  status: "original" | "unknown" | "translated";
  text: string;
  start: number;
  end: number;
};

export default function ChineseRecordLine({
  original,
  translate,
}: {
  original: string;
  translate?: () => Promise<ChineseResult>;
}) {
  const [result, setResult] = useState<ChineseResult>();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  return (
    <div>
      <span>{result?.text ?? original}</span>{" "}
      {result?.status === "translated" && <small>中文译文</small>}
      {result?.status === "unknown" && <small>语言待确认</small>}
      {error && <small role="alert">{error}</small>}
      {translate && !result && (
        <button
          className="text-btn"
          disabled={pending}
          onClick={async () => {
            setPending(true);
            setError("");
            try {
              setResult(await translate());
            } catch (e) {
              setError((e as Error).message);
            } finally {
              setPending(false);
            }
          }}
        >
          {pending ? "正在处理译文…" : error ? "重试本段" : "查看中文"}
        </button>
      )}
    </div>
  );
}
