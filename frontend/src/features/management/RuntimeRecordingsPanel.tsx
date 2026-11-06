import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api";
import RecordingStateSummary from "../notes/RecordingStateSummary";
import { watchRecordingCommands } from "../notes/recordingCommandEvents";
import { watchRecordingRecovery } from "../notes/recordingRecovery";
import { healthFreshness } from "../notes/healthFreshness";
export default function RuntimeRecordingsPanel({
  onRecording,
  onRecovery,
}: {
  onRecording: () => void;
  onRecovery: () => void;
}) {
  const query = useQuery({
    queryKey: ["runtime-recordings"],
    queryFn: ({ signal }) => api.runtimeRecordings(signal),
    retry: false,
    gcTime: 0,
    refetchInterval: 5000,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    networkMode: "always",
  });
  const [now, setNow] = useState(Date.now());
  const [checking, setChecking] = useState(false);
  const epoch = useRef(0);
  const { refetch } = query;
  const refresh = useCallback(async () => {
    const version = ++epoch.current;
    setChecking(true);
    setNow(Date.now());
    try {
      await refetch({ cancelRefetch: true });
    } finally {
      if (epoch.current === version) {
        setChecking(false);
        setNow(Date.now());
      }
    }
  }, [refetch]);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    const unwatch = watchRecordingRecovery(() => void refresh());
    const unwatchCommands = watchRecordingCommands(() => void refresh());
    return () => {
      ++epoch.current;
      window.clearInterval(timer);
      unwatch();
      unwatchCommands();
    };
  }, [refresh]);
  const stale =
    healthFreshness(
      query.dataUpdatedAt,
      Math.max(now, Date.now()),
      query.isError,
      checking,
    ) !== "current";
  return (
    <section className="management-section" aria-label="录音与控制状态">
      <h3>录音与控制状态</h3>
      <p>采集、文件、识别和控制结果分别核对，包含仅录音的会话。</p>
      {stale && (
        <p role="status">
          {query.isError
            ? "状态连接失败，不能确认当前采集仍在继续。"
            : "正在核对最新录音状态，旧记录仅供参考。"}
        </p>
      )}
      {query.data?.items.map((item) => (
        <article key={item.recording_id} className="management-recording">
          <p>
            {item.source === "native" ? "原生采集" : "浏览器采集"} ·{" "}
            {new Date(item.created * 1000).toLocaleString()} ·{" "}
            {item.recording_id.slice(0, 8)}
          </p>
          <RecordingStateSummary
            id={item.recording_id}
            runtime={item.runtime || undefined}
            unavailable={stale || !item.available}
          />
        </article>
      ))}
      {!stale && query.data?.total_count === 0 && <p>没有未完成的录音会话。</p>}
      {query.data?.truncated && (
        <p>
          共 {query.data.total_count} 场未完成会话，此处展示{" "}
          {query.data.items.length}{" "}
          场，优先展示原生采集中及控制待确认记录；其余请在录音恢复中查看。
        </p>
      )}
      <div className="management-task-actions">
        <button disabled={query.isFetching} onClick={() => void refresh()}>
          {query.isFetching ? "核对中…" : "重新核对录音"}
        </button>
        <button onClick={onRecording}>录音工作台</button>
        <button onClick={onRecovery}>录音恢复</button>
      </div>
    </section>
  );
}
