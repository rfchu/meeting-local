import type { NativeCaptureStatus } from "../../types";
export type RecordingCommand =
  "pause" | "resume" | "stop" | "report_interrupted";
export type ObservedCaptureState =
  "recording" | "paused" | "stopped" | "interrupted";
export interface CommandBody {
  command: RecordingCommand;
  request_id: string;
  expected_version: number;
  observed_state?: ObservedCaptureState;
}
export interface CommandReply {
  recording_id: string;
  control: { version: number; status: string; pending_command: string | null };
  receipt: {
    request_id: string;
    command: RecordingCommand;
    version: number;
    replayed: boolean;
    outcome: string;
    state: string;
  };
  state: string;
  native: NativeCaptureStatus | null;
}
interface Intent {
  key: string;
  requestId: string;
  version?: number;
  sent: boolean;
  task?: Promise<CommandReply>;
}
/** Retain request identity after uncertain transport failures; never auto-repeat an intent. */
export class RecordingCommandClient {
  private intents = new Map<string, Intent>();
  constructor(
    private version: (id: string) => Promise<number>,
    private send: (id: string, body: CommandBody) => Promise<CommandReply>,
    private definitive: (error: unknown) => boolean,
    private requestId = () => crypto.randomUUID().replaceAll("-", ""),
  ) {}
  pendingNativeCommand(id: string): "pause" | "resume" | "stop" | null {
    const intent = this.intents.get(id);
    if (!intent?.sent || !intent.key.endsWith(":native")) return null;
    const command = intent.key.split(":")[0];
    return command === "pause" || command === "resume" || command === "stop"
      ? command
      : null;
  }
  execute(
    id: string,
    command: RecordingCommand,
    observed?: ObservedCaptureState,
  ) {
    const key = `${command}:${observed || "native"}`;
    let intent = this.intents.get(id);
    if (intent?.task) {
      if (intent.key === key) return intent.task;
      return Promise.reject(new Error("上一项录音操作尚在处理中，请等待结果"));
    }
    if (intent && intent.key !== key) {
      if (intent.sent && !observed && command !== "stop")
        return Promise.reject(
          new Error("上一操作结果尚未确认，请先重查同一操作，必要时停止录音"),
        );
      intent = undefined;
    }
    if (!intent) {
      if (this.intents.size >= 64) {
        const old = [...this.intents].find(([, value]) => !value.task);
        if (!old)
          return Promise.reject(new Error("并行录音操作过多，请稍候重试"));
        this.intents.delete(old[0]);
      }
      intent = { key, requestId: this.requestId(), sent: false };
      this.intents.set(id, intent);
    }
    const current = intent;
    const task = Promise.resolve().then(async () => {
      try {
        if (current.version === undefined) {
          const version = await this.version(id);
          if (!Number.isSafeInteger(version) || version < 0)
            throw new Error("录音控制版本无效，请重新检查服务");
          current.version = version;
        }
        current.sent = true;
        const result = await this.send(id, {
          command,
          request_id: current.requestId,
          expected_version: current.version,
          ...(observed ? { observed_state: observed } : {}),
        });
        if (this.intents.get(id) === current) this.intents.delete(id);
        return result;
      } catch (error) {
        if (this.definitive(error) && this.intents.get(id) === current)
          this.intents.delete(id);
        throw error;
      } finally {
        current.task = undefined;
      }
    });
    current.task = task;
    return task;
  }
}
