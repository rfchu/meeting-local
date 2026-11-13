import React from "react";
import type {
  Meeting,
  MeetingSummary,
  Artifact,
  MeetingMinutes,
} from "../types";
import SearchDialog, { type SearchHit } from "../features/SearchDialog";
import { RenameDialog, MinutesPreview } from "./Dialogs";
import MinutesCompare from "../features/minutes/MinutesCompare";
import CompanionRecordingStatus from "../features/notes/CompanionRecordingStatus";

export interface AppModalsProps {
  toastText?: string;

  // Search
  searchOpen: boolean;
  meetings: MeetingSummary[];
  onSearchClose: () => void;
  onSearchHit: (hit: SearchHit) => Promise<void>;
  onManageIndex: () => void;
  onConfigureSearch: () => void;

  // Rename
  renameOpen: boolean;
  meeting: Meeting | null;
  onRenameClose: () => void;
  onRenameSave: (title: string) => Promise<void>;

  // Compare
  compareIds: [string, string] | null;
  onCompareClose: () => void;
  onSaveCompareSelection?: (document: MeetingMinutes) => Promise<void>;
  onAdoptCompareMinutes: () => void;
  compareBlockedReason?: string;

  // Preview
  previewOpen: boolean;
  minutesDraft: MeetingMinutes | null;
  selectedMinutesId: string | null;
  showCitations: boolean;
  onPreviewClose: () => void;
  onExportMinutes: (format: "docx" | "pdf") => void;

  // Companion
  companionId?: string;
  onCloseCompanion: () => void;
  onCompanionNativeRecording?: (id: string) => void;
  onCompanionMeeting?: (id: string) => void;
}

export const AppModals: React.FC<AppModalsProps> = React.memo(
  function AppModals(props) {
    const {
      toastText,
      searchOpen,
      meetings,
      onSearchClose,
      onSearchHit,
      onManageIndex,
      onConfigureSearch,
      renameOpen,
      meeting,
      onRenameClose,
      onRenameSave,
      compareIds,
      onCompareClose,
      onSaveCompareSelection,
      onAdoptCompareMinutes,
      compareBlockedReason,
      previewOpen,
      minutesDraft,
      selectedMinutesId,
      showCitations,
      onPreviewClose,
      onExportMinutes,
      companionId,
      onCloseCompanion,
      onCompanionNativeRecording,
      onCompanionMeeting,
    } = props;

    return (
      <>
        {toastText && (
          <div id="toast" role="status">
            {toastText}
          </div>
        )}

        {searchOpen && (
          <SearchDialog
            meetings={meetings}
            onManageIndex={onManageIndex}
            onConfigure={onConfigureSearch}
            onClose={onSearchClose}
            onOpen={onSearchHit}
          />
        )}

        {renameOpen && meeting && (
          <RenameDialog
            title={meeting.title}
            onClose={onRenameClose}
            onSave={onRenameSave}
          />
        )}

        {compareIds &&
          meeting &&
          (() => {
            const left = meeting.artifacts.find(
              (a) => a.id === compareIds[0],
            ) as Artifact<MeetingMinutes> | undefined;
            const right = meeting.artifacts.find(
              (a) => a.id === compareIds[1],
            ) as Artifact<MeetingMinutes> | undefined;
            return left && right ? (
              <MinutesCompare
                key={left.id + right.id}
                left={left}
                right={right}
                onSaveSelection={onSaveCompareSelection}
                onClose={onCompareClose}
                onAdopt={onAdoptCompareMinutes}
                blockedReason={compareBlockedReason}
              />
            ) : null;
          })()}

        {previewOpen && minutesDraft && meeting && (
          <MinutesPreview
            meetingId={meeting.id}
            artifactId={selectedMinutesId}
            includeCitations={showCitations}
            onClose={onPreviewClose}
            onExport={onExportMinutes}
          />
        )}

        {companionId && (
          <CompanionRecordingStatus
            key={companionId}
            id={companionId}
            onClose={onCloseCompanion}
            onNativeRecording={onCompanionNativeRecording}
            onMeeting={onCompanionMeeting}
          />
        )}
      </>
    );
  },
);
