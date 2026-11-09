import { useCallback, useEffect, useState } from "react";
import { api } from "../../api";
import Modal from "../../components/Modal";
import AudioCheck from "./AudioCheck";
import SampleMeetingDialog from "./SampleMeetingDialog";
import { captureState, type CaptureSource, type Readiness } from "./readiness";
import "./onboarding.css";
const key = "tingji.recording-guide.v1";
export default function RecordingReadiness({
  source,
  nativeCapability,
  engine,
  onConfigure,
  onModels,
  onReadiness,
}: {
  source: CaptureSource;
  nativeCapability?: Readiness["native"] | null;
  engine: "qwen" | "whisper";
  onConfigure: () => void;
  onModels: () => void;
  onReadiness: (value: Readiness) => void;
}) {
  const [data, setData] = useState<Readiness | null>(null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(false);
  const [permission, setPermission] = useState("unknown"),
    [guide, setGuide] = useState(false),
    [sample, setSample] = useState(false),
    [tested, setTested] = useState(false);
  const [completed, setCompleted] = useState(() => {
    try {
      return !!localStorage.getItem(key);
    } catch {
      return false;
    }
  });
  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const value = await api.recordingReadiness();
      setData(value);
      onReadiness(value);
      try {
        const result = await navigator.permissions?.query({
          name: "microphone" as PermissionName,
        });
        setPermission(result?.state || "unknown");
      } catch {
        setPermission("unknown");
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [onReadiness]);
  useEffect(() => {
    void refresh();
  }, [refresh]);
  const input = captureState(
    source,
    nativeCapability ?? data?.native,
    !!navigator.mediaDevices?.getUserMedia &&
      typeof MediaRecorder !== "undefined",
    permission,
  );
  const asr = data?.engines.find((item) => item.id === engine),
    llm = data?.llm.configured;
  const items = [
    {
      title: "录音输入",
      value: input.label,
      state: input.state,
      action: onConfigure,
      label: "选择音源",
    },
    {
      title: "实时识别",
      value: asr?.ready
        ? `${asr.label} 已就绪`
        : "模型未就绪，可先录音并在会后转写",
      state: asr?.ready ? "ready" : "optional",
      action: onConfigure,
      label: "配置识别",
    },
    {
      title: "AI 纪要",
      value: llm ? "已配置服务，生成时检查连接" : "尚未配置，不影响录音和简记",
      state: llm ? "configured" : "optional",
      action: onModels,
      label: "配置模型",
    },
  ];
  return (
    <section className="recording-readiness" aria-label="开始前检查">
      <div className="readiness-heading">
        <div>
          <h3>
            {input.state === "blocked"
              ? "请先检查录音输入"
              : completed
                ? "准备好就开始"
                : "第一次使用？先检查声音"}
          </h3>
          <p>录音、识别和 AI 整理分别就绪，按需配置。</p>
        </div>
        <div className="guide-actions">
          <button
            onClick={() => {
              setTested(false);
              setGuide(true);
            }}
          >
            检查与试录
          </button>
          <button className="text-btn" onClick={() => setSample(true)}>
            体验样例会议
          </button>
        </div>
      </div>
      <div className="readiness-grid">
        {items.map((item) => (
          <article key={item.title}>
            <span className={`readiness-dot ${item.state}`} />
            <div>
              <strong>{item.title}</strong>
              <p>{loading && !data ? "正在检查…" : item.value}</p>
              <button className="text-btn" onClick={item.action}>
                {item.label}
              </button>
            </div>
          </article>
        ))}
      </div>
      {data && !data.storage.writable && (
        <p role="alert" className="error">
          数据目录无法写入，请修复后再开始录音。
        </p>
      )}
      {data && data.storage.free_bytes < 1024 * 1024 * 1024 && (
        <p role="alert">磁盘可用空间不足 1GB，建议清理空间后录音。</p>
      )}
      {data && !data.ffmpeg_ready && (
        <p role="alert">
          音频处理工具未就绪，请在运行环境中完成安装后整理录音。
        </p>
      )}
      {error && (
        <p role="alert">
          检查未完成：{error}{" "}
          <button disabled={loading} onClick={() => void refresh()}>
            重试
          </button>
        </p>
      )}
      {guide && (
        <Modal
          className="recording-guide-dialog"
          ariaLabel="录音检查与十秒试录"
          onClose={() => setGuide(false)}
        >
          <div className="guide-heading">
            <div>
              <span className="eyebrow">开始前检查</span>
              <h2>先确认声音，再开始会议</h2>
            </div>
            <button onClick={() => setGuide(false)}>关闭</button>
          </div>
          <p>
            {input.label}。
            {source === "browser"
              ? "本次测试使用浏览器麦克风。"
              : source === "macos-both"
                ? "本次测试采集电脑声音与麦克风。"
                : source === "macos-microphone"
                  ? "本次测试仅采集麦克风。"
                  : "本次测试仅采集电脑声音。"}
          </p>
          <div className="guide-actions">
            <button disabled={loading} onClick={() => void refresh()}>
              {loading ? "正在检查…" : "重新检查权限与模型"}
            </button>
            <button
              onClick={() => {
                setGuide(false);
                onConfigure();
              }}
            >
              更换音源或识别模型
            </button>
          </div>
          <AudioCheck
            key={source}
            source={source}
            engine={engine}
            engineReady={!!asr?.ready}
            onTested={() => {
              setTested(true);
              void refresh();
            }}
          />
          <div className="guide-complete">
            <button
              className="primary"
              disabled={!tested}
              onClick={() => {
                try {
                  localStorage.setItem(key, new Date().toISOString());
                } catch {
                  /* completion does not require storage */
                }
                setCompleted(true);
                setGuide(false);
              }}
            >
              已回放确认，完成检查
            </button>
            <small>确认后回到准备页，点击“开始录音”建立正式会议。</small>
          </div>
        </Modal>
      )}
      {sample && <SampleMeetingDialog onClose={() => setSample(false)} />}
    </section>
  );
}
