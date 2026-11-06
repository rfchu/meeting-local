import { useState, useEffect } from "react";
import Modal from "../../components/Modal";
import LiveOverviewPanel from "./LiveOverviewPanel";
import type { Meeting } from "../../types";
export default function OverviewArchiveButton({
  meeting,
  onSeek,
  openRequested = false,
  onOpened,
  focusItemId,
}: {
  meeting: Meeting;
  openRequested?: boolean;
  onOpened?: () => void;
  focusItemId?: string;
  onSeek: (time: number) => void;
}) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (openRequested && meeting.live_overview_available) {
      setOpen(true);
      onOpened?.();
    }
  }, [openRequested, meeting.live_overview_available, onOpened]);
  if (!meeting.live_overview_available) return null;
  return (
    <>
      <button onClick={() => setOpen(true)}>会中概览</button>
      {open && (
        <Modal
          className="overview-archive-dialog"
          ariaLabel="回看会中概览"
          onClose={() => setOpen(false)}
        >
          <div className="overview-heading">
            <h2>会中概览与核对</h2>
            <button onClick={() => setOpen(false)}>关闭</button>
          </div>
          <p className="muted">
            保留会中草稿及人工确认记录；正式纪要仍基于完整文字稿整理。
          </p>
          <LiveOverviewPanel
            key={meeting.id}
            recordingId={meeting.id}
            focusItemId={focusItemId}
            onLocate={(e) => onSeek(e.start)}
            locateLabel="回听来源"
          />
        </Modal>
      )}
    </>
  );
}
