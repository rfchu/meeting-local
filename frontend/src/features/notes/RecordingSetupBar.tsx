interface SetupCardProps {
  kind: "capture" | "outcome" | "brief";
  label: string;
  value: string;
  description: string;
  disabled: boolean;
  onClick: () => void;
}
function SetupCard({
  kind,
  label,
  value,
  description,
  disabled,
  onClick,
}: SetupCardProps) {
  const paths = {
    capture: (
      <>
        <rect x="9" y="3" width="6" height="11" rx="3" />
        <path d="M5 10v1a7 7 0 0 0 14 0v-1M12 18v3M8 21h8" />
      </>
    ),
    outcome: (
      <>
        <path d="M8 3h8l4 4v14H4V3h4M14 3v5h6M8 12h8M8 16h5" />
      </>
    ),
    brief: (
      <>
        <rect x="3" y="5" width="18" height="16" rx="3" />
        <path d="M7 3v4M17 3v4M3 11h18M7 15h4" />
      </>
    ),
  };
  return (
    <button
      className={`recording-setup-card setup-${kind}`}
      disabled={disabled}
      onClick={onClick}
      aria-label={`编辑${label}`}
    >
      <span className="setup-card-icon" aria-hidden="true">
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          {paths[kind]}
        </svg>
      </span>
      <span className="setup-card-copy">
        <span className="setup-card-label">{label}</span>
        <strong>{value}</strong>
        <small>{description}</small>
      </span>
      {!disabled && (
        <span className="setup-card-arrow" aria-hidden="true">
          ↗
        </span>
      )}
    </button>
  );
}
export default function RecordingSetupBar({
  source,
  recognition,
  outcome,
  processing,
  title,
  brief,
  editable,
  preferencesSaved,
  onCapture,
  onOutcome,
  onBrief,
}: {
  source: string;
  recognition: string;
  outcome: string;
  processing: string;
  title: string;
  brief: string;
  editable: boolean;
  preferencesSaved: boolean;
  onCapture: () => void;
  onOutcome: () => void;
  onBrief?: () => void;
}) {
  return (
    <section className="recording-setup" aria-label="本次录音方案">
      <div className="recording-setup-caption">
        <span>本次会议</span>
        <small>
          {preferencesSaved
            ? "沿用上次设置，可按需调整"
            : "当前浏览器无法记住设置，本次仍可使用"}
        </small>
      </div>
      <div className="recording-setup-cards">
        <SetupCard
          kind="capture"
          label="采集与识别"
          value={source}
          description={recognition}
          disabled={!editable}
          onClick={onCapture}
        />
        <SetupCard
          kind="outcome"
          label="会后产物"
          value={outcome}
          description={processing}
          disabled={!editable}
          onClick={onOutcome}
        />
        {onBrief && (
          <SetupCard
            kind="brief"
            label="会议准备"
            value={title || "添加会议主题"}
            description={brief || "主题、参会人和关注问题 · 可选"}
            disabled={!editable}
            onClick={onBrief}
          />
        )}
      </div>
    </section>
  );
}
