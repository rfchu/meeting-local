import type { MeetingSummary } from "../types";

const states: Record<string, string> = {
  queued: "排队中",
  running: "处理中",
  completed: "任务完成",
  failed: "失败",
  cancelled: "已取消",
};

interface Props {
  onHome: () => void;
  onLibrary: () => void;
  meetings: MeetingSummary[];
  currentId?: string;
  uploading: boolean;
  onSelect: (id: string) => void;
  onUpload: (file: File) => void;
  onRecord: () => void;
  onFollowups: () => void;
  onManage: () => void;
  onSearch: () => void;
  onRecovery: () => void;
  recoveryCount: number;
}

export default function Sidebar({
  onHome,
  onLibrary,
  meetings,
  currentId,
  uploading,
  onSelect,
  onUpload,
  onRecord,
  onFollowups,
  onManage,
  onSearch,
  onRecovery,
  recoveryCount,
}: Props) {
  const choose = () => document.getElementById("file")?.click();
  return (
    <aside>
      <a
        className="brand"
        href="/"
        onClick={(event) => {
          event.preventDefault();
          onHome();
        }}
      >
        ◉{" "}
        <span>
          听记<small>LOCAL MEETING</small>
        </span>
      </a>
      <button
        id="uploadBtn"
        className="primary wide"
        disabled={uploading}
        onClick={choose}
      >
        {uploading ? "正在导入…" : "＋ 导入会议录音"}
      </button>
      <button
        className="wide record-button"
        disabled={uploading}
        onClick={onRecord}
      >
        ● 开始实时记录
      </button>
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
      <button className="wide" onClick={onSearch}>
        ⌕ 搜索与引用问答
      </button>
      <button className="wide" onClick={onFollowups}>
        ☑ 跨会议跟进
      </button>
      <div className="nav-label">
        <button className="text-btn" onClick={onLibrary}>
          会议资料库 ↗
        </button>{" "}
        <span>{meetings.filter((item) => !item.library?.archived).length}</span>
      </div>
      <div id="meetingList">
        {meetings
          .filter((item) => !item.library?.archived)
          .map((meeting) => (
            <button
              key={meeting.id}
              className={`meeting-item${currentId === meeting.id ? " active" : ""}`}
              onClick={() => onSelect(meeting.id)}
            >
              <strong>{meeting.title}</strong>
              <small>
                {meeting.created.slice(0, 10)} ·{" "}
                {meeting.job
                  ? states[meeting.job.state]
                  : meeting.revision
                    ? "已有文字稿"
                    : "待转写"}
              </small>
            </button>
          ))}
      </div>
      <div className="sidebar-bottom">
        <span className="dot" /> 会议数据保存在本机
        <button className="text-btn sidebar-recovery" onClick={onRecovery}>
          录音恢复
          {recoveryCount > 0 && (
            <span className="recovery-count">{recoveryCount}</span>
          )}
        </button>
        <button className="text-btn" onClick={onManage}>
          运行管理 ↗
        </button>
      </div>
    </aside>
  );
}
