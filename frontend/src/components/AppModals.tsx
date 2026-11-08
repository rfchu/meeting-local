import React from "react";
import type {
  Meeting,
  MeetingSummary,
  Artifact,
  MeetingMinutes,
  TaskFocus,
} from "../types";
import SearchDialog, { type SearchHit } from "../features/SearchDialog";
import { RenameDialog, MinutesPreview } from "./Dialogs";
import RuntimeManagement, {
  type ManagementPage,
} from "../features/management/RuntimeManagement";
import MinutesCompare from "../features/minutes/MinutesCompare";
import FollowupsDialog from "../features/meeting/FollowupsDialog";
import RecordingDialog from "../features/notes/RecordingDialog";
import CompanionRecordingStatus from "../features/notes/CompanionRecordingStatus";
import type { CompletionMode } from "../features/notes/recordingPreferences";

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

  // Management
  managementPage: ManagementPage | null;
  recoveryCount: number;
  recoveryEngine: "qwen" | "whisper";
  taskFocus?: TaskFocus;
  onManagementClose: () => void;
  onManagementPageChange: (page: ManagementPage) => void;
  onRecoveryComplete: (id: string, options: { mode: CompletionMode; engine: "whisper" | "qwen"; goal?: "transcript" | "minutes" }) => Promise<void>;
  onRecordingFromManagement: () => void;
  onSearchFromManagement: () => void;
  onOpenMeetingFromManagement: (id: string) => void;
  onEnvironmentChanged: () => void;

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

  // Followups
  followupsOpen: boolean;
  onFollowupsClose: () => void;
  onOpenFollowupMeeting: (id: string) => Promise<void>;

  // Recording Dialog
  recordingOpen: boolean;
  targetRecordingId?: string;
  environment: any;
  minutesModel: string;
  onMinutesModelChange: (model: string) => void;
  engineLabel: string;
  onRecordingClose: () => void;
  onRecordingComplete: (id: string, options: { mode: CompletionMode; engine: "whisper" | "qwen"; goal?: "transcript" | "minutes" }) => Promise<void>;
  onRecordingModels: () => void;

  // Companion
  companionId?: string;
  onCloseCompanion: () => void;
  onCompanionNativeRecording?: (id: string) => void;
  onCompanionMeeting?: (id: string) => void;

  toast: (text: string) => void;
}

export const AppModals: React.FC<AppModalsProps> = React.memo(function AppModals(props) {
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
    managementPage,
    recoveryCount,
    recoveryEngine,
    taskFocus,
    onManagementClose,
    onManagementPageChange,
    onRecoveryComplete,
    onRecordingFromManagement,
    onSearchFromManagement,
    onOpenMeetingFromManagement,
    onEnvironmentChanged,
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
    followupsOpen,
    onFollowupsClose,
    onOpenFollowupMeeting,
    recordingOpen,
    targetRecordingId,
    environment,
    minutesModel,
    onMinutesModelChange,
    engineLabel,
    onRecordingClose,
    onRecordingComplete,
    onRecordingModels,
    companionId,
    onCloseCompanion,
    onCompanionNativeRecording,
    onCompanionMeeting,
    toast,
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

      {managementPage && (
        <RuntimeManagement
          recoveryCount={recoveryCount}
          recoveryEngine={recoveryEngine}
          onRecoveryComplete={onRecoveryComplete}
          onRecording={onRecordingFromManagement}
          onSearch={onSearchFromManagement}
          onOpenMeeting={onOpenMeetingFromManagement}
          page={managementPage}
          taskFocus={taskFocus}
          onPage={onManagementPageChange}
          onClose={onManagementClose}
          onChanged={onEnvironmentChanged}
          toast={toast}
        />
      )}

      {compareIds &&
        meeting &&
        (() => {
          const left = meeting.artifacts.find((a) => a.id === compareIds[0]) as
            | Artifact<MeetingMinutes>
            | undefined;
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

      {followupsOpen && (
        <FollowupsDialog
          onClose={onFollowupsClose}
          onOpen={onOpenFollowupMeeting}
        />
      )}

      {recordingOpen && (
        <RecordingDialog
          targetRecordingId={targetRecordingId}
          environment={environment}
          minutesModel={minutesModel}
          onMinutesModel={onMinutesModelChange}
          flowReady={environment?.meeting_flow_ready === true}
          meetings={meetings}
          engineLabel={engineLabel}
          engines={environment?.engines || []}
          onClose={onRecordingClose}
          onComplete={onRecordingComplete}
          onModels={onRecordingModels}
          toast={toast}
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
});
