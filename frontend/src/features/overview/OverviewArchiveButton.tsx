import { useState, useEffect } from "react";
import Modal from "../../components/Modal";
import LiveOverviewPanel from "./LiveOverviewPanel";
import type { OverviewEvidence } from "./types";
import type { Meeting } from "../../types";
import { clock, trackLabel } from "../../utils";
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
  onSeek: (evidence: OverviewEvidence) => boolean;
}) {
  const [open, setOpen] = useState(false);
  const [original, setOriginal] = useState<OverviewEvidence>();
  const close = () => {
    setOriginal(undefined);
    setOpen(false);
  };
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
          onClose={close}
        >
          <div className="overview-heading">
            <h2>会中概览与核对</h2>
            <button onClick={close}>关闭</button>
          </div>
          <p className="muted">
            保留会中草稿及人工确认记录；正式纪要仍基于完整文字稿整理。
          </p>
          {original?.source_track && (
            <section className="overview-original-clip" aria-label="会中原话">
              <b>
                {clock(original.start)} · {trackLabel(original.source_track)}
              </b>
              <p>{original.quote}</p>
              <audio
                controls
                preload="metadata"
                aria-label={`回听 ${trackLabel(original.source_track)}`}
                src={`/api/recordings/${meeting.id}/live-audio/${original.range_id}/${original.segment_index}`}
              />
            </section>
          )}
          <LiveOverviewPanel
            key={meeting.id}
            recordingId={meeting.id}
            focusItemId={focusItemId}
            onLocate={(e) => {
              if (onSeek(e)) close();
              else setOriginal(e);
            }}
            locateLabel="回听来源"
          />
        </Modal>
      )}
    </>
  );
}
