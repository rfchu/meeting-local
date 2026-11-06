import RecordingQualityReview from "./RecordingQualityReview";
import { useEffect, useState } from "react";
import { api } from "../../api";
import { clock } from "../../utils";

export default function RecordingReceipt({
  id,
  engine,
}: {
  id: string;
  engine: "whisper" | "qwen";
}) {
  const [report, setReport] = useState<Awaited<
    ReturnType<typeof api.recordingCompletion>
  > | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    setError("");
    setReport(null);
    void api.recordingCompletion(id, engine).then(
      (value) => {
        if (active) setReport(value);
      },
      () => {
        if (active) setError("核对暂不可用，已保存的录音保留在本机。");
      },
    );
    return () => {
      active = false;
    };
  }, [id, engine, attempt]);
  if (error)
    return (
      <p role="status">
        {error}{" "}
        <button className="text-btn" onClick={() => setAttempt(attempt + 1)}>
          重新核对
        </button>
      </p>
    );
  if (!report) return <p role="status">正在核对保存文件与识别覆盖…</p>;
  return (
    <div className="recording-receipt" aria-label="录音保存核对结果">
      <div className={report.audio_saved ? "" : "health-warning"}>
        <strong>
          {report.audio_saved ? "本机文件已保存" : "文件保存待检查"}
        </strong>
        <span>
          {clock(report.duration_seconds)} ·{" "}
          {(report.saved_bytes / 1048576).toFixed(1)} MB · {report.notes_count}{" "}
          条关注点
        </span>
      </div>
      <div>
        <strong>实时识别覆盖 {clock(report.coverage.covered_seconds)}</strong>
        <span>
          {report.coverage.uncovered_seconds > 0
            ? `还有 ${clock(report.coverage.uncovered_seconds)} 待补识别，快速完成会补齐缺段与尾段。`
            : "已覆盖全部录音范围，快速完成可直接复用已确认结果。"}
        </span>
      </div>
      {report.coverage.gaps.length > 0 && (
        <details>
          <summary>查看待补范围 · {report.coverage.gaps.length} 处</summary>
          <p>
            {report.coverage.gaps
              .map((gap) => `${clock(gap.start)}–${clock(gap.end)}`)
              .join("、")}
          </p>
        </details>
      )}
      <RecordingQualityReview quality={report.quality} />
      <small>
        文件存在不代表全程采集完整；待补识别范围与录音异常分别核对，补识别不能补回未采集到的声音。
      </small>
    </div>
  );
}
