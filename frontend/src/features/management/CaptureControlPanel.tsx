import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api";
import { clock } from "../../utils";

export interface CaptureControlStatus {
  passive: boolean;
  sessions: {
    recording_id: string;
    seq: number;
    engine: string;
    state: string;
    recoverable: boolean;
    confirmed_until: number;
    lag_seconds: number;
    coverage: {
      gaps: { start: number; end: number }[];
      covered_seconds: number;
    };
  }[];
  control: {
    broker_build: string | null;
    recording_id: string;
    state: string;
    alive: boolean;
    can_claim: boolean;
    exit_code: number | null;
    exit_reason: string | null;
    pending_commands: number | null;
  } | null;
  upgrade: { requires_idle: boolean; policy: string };
}

export default function CaptureControlPanel() {
  const query = useQuery({
    queryKey: ["capture-control"],
    queryFn: () => api.captureControl(),
    refetchInterval: 5000,
    retry: false,
    gcTime: 0,
  });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  async function recover(id: string, seq: number) {
    setBusy(true);
    setMessage("");
    try {
      await api.recoverLiveSession(id, seq);
      setMessage("已从最后确认位置继续识别，历史缺段保留待补。");
    } catch (error) {
      setMessage(
        error instanceof Error ? error.message : "恢复未确认，请刷新后重试。",
      );
    } finally {
      await query.refetch();
      setBusy(false);
    }
  }
  return (
    <section className="management-section" aria-label="识别恢复与升级">
      <h3>识别恢复与升级</h3>
      {query.isError && <p role="alert">状态暂不可用，请刷新核对。</p>}
      {query.data?.control && (
        <p>
          独立采集服务 {query.data.control.broker_build || "旧版"} ·{" "}
          {query.data.control.alive
            ? "采集器运行中"
            : `${query.data.control.exit_reason === "normal_stop" ? "正常结束" : "异常退出或停止未确认"}（${query.data.control.exit_code ?? "待核对"}）`}{" "}
          · {query.data.control.pending_commands ?? "待核对"} 条未确认命令
        </p>
      )}
      {query.data?.sessions.map((session) => (
        <div className="management-row" key={session.recording_id}>
          <strong>{session.engine === "qwen" ? "Qwen-ASR" : "Whisper"}</strong>
          <span>
            {session.recoverable
              ? "识别已停止，录音仍在保存"
              : session.state === "catching_up"
                ? "识别追赶中"
                : "识别运行中"}
            {" · "}已确认至 {clock(session.confirmed_until)}
            {" · "}未覆盖 {session.coverage.gaps.length} 段
          </span>
          {session.recoverable && (
            <button
              disabled={busy || query.isError || query.isFetching}
              onClick={() => void recover(session.recording_id, session.seq)}
            >
              按原配置恢复识别
            </button>
          )}
        </div>
      ))}
      {message && <p role="status">{message}</p>}
      {query.data && <p className="muted">{query.data.upgrade.policy}</p>}
    </section>
  );
}
