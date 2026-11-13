import type { MeetingSummary } from "../types";
import type { ShellRoute } from "../shell/shellState";
import {
  UNIFIED_MANAGEMENT_LABEL,
  resolveMeetingStatusIndicator,
} from "../shell/shellState";
import { IconCheckSquare, IconFolder, IconSearch } from "../styles/icons";

interface Props {
  onHome: () => void;
  onLibrary: () => void;
  meetings: MeetingSummary[];
  currentId?: string;
  activeRoute?: ShellRoute["kind"];
  recordingLocked?: boolean;
  uploading: boolean;
  onSelect: (id: string) => void;
  onUpload: (file: File) => void;
  onRecord?: () => void;
  onFollowups: () => void;
  onManage?: () => void;
  onSearch: () => void;
  recoveryCount: number;
  browserWorkspace?: boolean;
}

export default function Sidebar({
  onHome,
  onLibrary,
  meetings,
  currentId,
  activeRoute = "meeting",
  recordingLocked = false,
  uploading,
  onSelect,
  onUpload,
  onRecord,
  onFollowups,
  onManage,
  onSearch,
  recoveryCount,
  browserWorkspace,
}: Props) {
  const choose = () => document.getElementById("file")?.click();
  return (
    <aside>
      <a
        className="brand"
        href="/"
        onClick={(event) => {
          event.preventDefault();
          if (!recordingLocked) onHome();
        }}
      >
        ◉{" "}
        <span>
          听记<small>LOCAL MEETING</small>
        </span>
      </a>
      <div className="sidebar-action-group">
        {onRecord && (
          <button
            id="recordBtn"
            className={`wide record-action-btn${activeRoute === "recording" ? " active" : ""}`}
            disabled={uploading || recordingLocked}
            onClick={onRecord}
          >
            <span className="record-dot" /> 开始实时记录
          </button>
        )}
        <button
          id="uploadBtn"
          className="wide upload-action-btn"
          disabled={uploading || recordingLocked}
          onClick={choose}
        >
          {uploading ? "正在导入…" : "＋ 导入会议录音"}
        </button>
      </div>
      <input
        id="file"
        type="file"
        accept=".wav,.mp3,.m4a,.mp4,.mov,.aac,.flac,.ogg,.webm"
        hidden
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) onUpload(file);
          event.target.value = "";
        }}
      />
      <button
        type="button"
        className="sidebar-search-btn"
        disabled={recordingLocked}
        onClick={onSearch}
        title="全局搜索会议与问答 (⌘K)"
      >
        <span className="search-icon">
          <IconSearch size={14} />
        </span>
        <span className="search-placeholder">搜索与问答…</span>
        <kbd className="search-shortcut">⌘K</kbd>
      </button>
      <div className="sidebar-nav-group">
        <button
          type="button"
          className={`sidebar-nav-item${activeRoute === "followups" ? " active" : ""}`}
          aria-current={activeRoute === "followups" ? "page" : undefined}
          disabled={recordingLocked}
          onClick={onFollowups}
          title="集中管理各场会议中已确认的跟进项与待办"
        >
          <span className="sidebar-nav-icon">
            <IconCheckSquare size={15} />
          </span>
          <span className="sidebar-nav-label">跨会议跟进</span>
        </button>
        <button
          type="button"
          className={`sidebar-nav-item${activeRoute === "library" ? " active" : ""}`}
          aria-current={activeRoute === "library" ? "page" : undefined}
          disabled={recordingLocked}
          onClick={onLibrary}
          title="查看与检索所有历史会议资料"
        >
          <span className="sidebar-nav-icon">
            <IconFolder size={15} />
          </span>
          <span className="sidebar-nav-label">会议资料库</span>
          <span className="sidebar-nav-badge">
            {meetings.filter((item) => !item.library?.archived).length}
          </span>
        </button>
      </div>
      <div className="sidebar-section-header">
        <span>最近会议</span>
      </div>
      <div id="meetingList">
        {meetings
          .filter((item) => !item.library?.archived)
          .map((meeting) => {
            const status = resolveMeetingStatusIndicator(meeting);
            return (
              <button
                key={meeting.id}
                className={`meeting-item${activeRoute === "meeting" && currentId === meeting.id ? " active" : ""}`}
                disabled={recordingLocked}
                onClick={() => onSelect(meeting.id)}
              >
                <strong>{meeting.title}</strong>
                <small className="meeting-item-meta">
                  <span
                    className={`meeting-status-dot ${status.tone}`}
                    aria-hidden="true"
                  />
                  <span>
                    {meeting.created.slice(0, 10)} ·{" "}
                    <span className={`meeting-status-label ${status.tone}`}>
                      {status.label}
                    </span>
                  </span>
                </small>
              </button>
            );
          })}
      </div>
      <div className="sidebar-bottom">
        <span className="dot" /> 会议数据保存在本机
        {browserWorkspace ? (
          <a
            href="tingji://open"
            className="sidebar-app-link"
            title="返回听记原生应用"
          >
            打开听记 App ↗
          </a>
        ) : (
          onManage && (
            <button
              className={`text-btn sidebar-management${activeRoute === "management" ? " active" : ""}`}
              aria-current={activeRoute === "management" ? "page" : undefined}
              disabled={recordingLocked}
              onClick={onManage}
            >
              {UNIFIED_MANAGEMENT_LABEL}
              {recoveryCount > 0 && (
                <span className="recovery-count">待恢复 {recoveryCount}</span>
              )}
            </button>
          )
        )}
      </div>
    </aside>
  );
}
