type WaveformTrack = { label: string; levels: number[]; color: string };

export default function RecordingWaveform({
  tracks,
  active,
  paused,
  status,
  onRetry,
}: {
  tracks: WaveformTrack[];
  active: boolean;
  paused: boolean;
  status: string;
  onRetry?: () => void;
}) {
  return (
    <section className="recording-waveform" aria-label="实时音频波形">
      <header>
        <span>
          <i className={active && !paused ? "live" : ""} />
          {paused ? "录音已暂停" : active ? "正在聆听" : "音频采集"}
        </span>
        <small>声音强度 · 实时波形</small>
      </header>
      {tracks.map((track) => {
        const history = track.levels.slice(-72),
          levels = [...Array<number>(72 - history.length).fill(0), ...history];
        return (
          <div className="waveform-track" key={track.label}>
            <span>{track.label}</span>
            <svg
              viewBox="0 0 720 104"
              preserveAspectRatio="none"
              role="img"
              aria-label={`${track.label}声音强度波形`}
            >
              <line
                x1="0"
                x2="720"
                y1="52"
                y2="52"
                stroke="currentColor"
                opacity=".09"
              />
              {levels.map((level, index) => {
                const height =
                  2 + Math.min(1, Math.sqrt(Math.max(0, level)) * 2.4) * 88;
                return (
                  <line
                    key={index}
                    x1={index * 10 + 4}
                    x2={index * 10 + 4}
                    y1={52 - height / 2}
                    y2={52 + height / 2}
                    stroke={track.color}
                    strokeWidth="2"
                    strokeLinecap="round"
                    opacity={active && !paused ? 1 : 0.45}
                  />
                );
              })}
              <line
                x1="715"
                x2="715"
                y1="2"
                y2="102"
                stroke="#3968e8"
                strokeWidth="1"
                opacity={active ? 1 : 0.2}
              />
            </svg>
          </div>
        );
      })}
      <footer>
        <span role="status">{status}</span>
        {onRetry && <button onClick={onRetry}>重试同步</button>}
      </footer>
    </section>
  );
}
