export type ChineseResult = {
  status: "original" | "unknown" | "translated";
  text: string;
  start: number;
  end: number;
};

export default function ChineseRecordLine({
  original,
  result,
  status,
  error,
  onRetry,
}: {
  original: string;
  result?: ChineseResult;
  status?: string;
  error?: string;
  onRetry: () => void;
}) {
  return (
    <>
      <p>
        {original} {status && <small>{status}</small>}
        {error && (
          <>
            <small role="alert">{error}</small>{" "}
            <button className="text-btn" onClick={onRetry}>
              重试本段
            </button>
          </>
        )}
      </p>
      {result?.status === "translated" && (
        <p className="live-bilingual-translation" lang="zh-Hans">
          <small>中文译文</small> {result.text}
        </p>
      )}
    </>
  );
}
