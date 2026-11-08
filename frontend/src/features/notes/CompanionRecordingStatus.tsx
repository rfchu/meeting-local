import { useQuery } from "@tanstack/react-query";
import { api } from "../../api";
import Modal from "../../components/Modal";
import { recordingStatus } from "./recordingStatus";

export const companionRecording = (hash: string) =>
  /^#companion-recording=([0-9a-f]{32})$/.exec(hash)?.[1];

export default function CompanionRecordingStatus({
  id,
  onClose,
  onMeeting,
  onNativeRecording,
}: {
  id: string;
  onClose: () => void;
  onNativeRecording?: (id: string) => void;
  onMeeting?: (id: string) => void;
}) {
  const query = useQuery({
    queryKey: ["companion-recording", id],
    queryFn: ({ signal }) => api.recordingHealth(id, signal),
    retry: false,
    refetchInterval: 3000,
    gcTime: 0,
  });
  const runtime = query.data?.runtime;
  const verified = !query.isError && runtime?.identity?.recording_id === id;
  const view = verified ? recordingStatus(runtime, "", false) : undefined;
  const meeting = verified ? runtime?.identity?.meeting_id : undefined;
  const nativeActive =
    verified &&
    !meeting &&
    query.data?.source === "native" &&
    ["recording", "paused", "starting"].includes(runtime?.phase || "");
  const nativeControl = useQuery({
    queryKey: ["companion-native-control", id],
    queryFn: ({ signal }) => api.nativeStatus(id, signal),
    enabled: nativeActive,
    retry: false,
    refetchInterval: 3000,
  });
  const canRecover =
    nativeActive &&
    !nativeControl.isError &&
    nativeControl.data?.ownership?.control_required &&
    nativeControl.data.ownership.can_recover_control;
  return (
    <Modal className="small-dialog" ariaLabel="本场录音状态" onClose={onClose}>
      <h2>本场录音状态</h2>
      <p>会话 {id.slice(0, 8)}</p>
      {view ? (
        <>
          <p role="status">
            <strong>{view.label}</strong>
          </p>
          <p>
            {[view.input, view.storage, view.asr].filter(Boolean).join(" · ")}
          </p>
          {view.savedPosition && <p>{view.savedPosition}</p>}
          {!meeting && query.data?.source === "browser" && (
            <p>如需操作浏览器录音，请回到开始录音的原标签页。</p>
          )}
        </>
      ) : (
        <p role="status">
          {query.isPending
            ? "正在核对本场录音…"
            : "暂时无法核对本场录音，未切换到其他会话。"}
        </p>
      )}
      {nativeActive && !canRecover && (
        <p>这里显示本场录音状态。请回到开始录音的工作台控制录音。</p>
      )}
      {canRecover && onNativeRecording && (
        <button onClick={() => onNativeRecording(id)}>恢复本场录音控制</button>
      )}
      {meeting && onMeeting && (
        <button onClick={() => onMeeting(meeting)}>打开本场会议</button>
      )}
      {query.isError && (
        <button onClick={() => void query.refetch()}>重新核对</button>
      )}
      <button onClick={onClose}>关闭</button>
    </Modal>
  );
}
