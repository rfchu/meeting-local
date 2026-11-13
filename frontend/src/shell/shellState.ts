import type { ManagementPage } from "../features/management/RuntimeManagement";
import type { MeetingSummary, TaskFocus } from "../types";

export const UNIFIED_MANAGEMENT_LABEL = "运行与模型管理";

export type MeetingStatusTone = "running" | "failed" | "pending" | "ready";

const JOB_STATE_LABELS: Record<string, string> = {
  queued: "排队中",
  running: "处理中",
  completed: "任务完成",
  failed: "失败",
  cancelled: "已取消",
};

/** Resolve sidebar status label and semantic dot tone for a meeting item. */
export function resolveMeetingStatusIndicator(
  meeting: Pick<MeetingSummary, "job" | "revision">,
): { tone: MeetingStatusTone; label: string } {
  if (meeting.job) {
    const label = JOB_STATE_LABELS[meeting.job.state] || "处理中";
    if (meeting.job.state === "failed") {
      return { tone: "failed", label };
    }
    if (meeting.job.state === "running" || meeting.job.state === "queued") {
      return { tone: "running", label };
    }
    if (meeting.job.state === "cancelled") {
      return { tone: "pending", label };
    }
    return {
      tone: meeting.revision ? "ready" : "pending",
      label,
    };
  }
  return meeting.revision
    ? { tone: "ready", label: "已有文字稿" }
    : { tone: "pending", label: "待转写" };
}

export type RecordingShellPhase = "setup" | "active";

export type ShellRoute =
  | { kind: "meeting" }
  | { kind: "library" }
  | { kind: "followups" }
  | { kind: "management"; page: ManagementPage; taskFocus?: TaskFocus }
  | {
      kind: "recording";
      targetRecordingId?: string;
      phase: RecordingShellPhase;
    };

export type TransientModal =
  | { kind: "search" }
  | { kind: "rename" }
  | { kind: "retranscribe" }
  | { kind: "prepare" }
  | { kind: "notes" }
  | { kind: "preview" }
  | { kind: "compare"; ids: [string, string] }
  | { kind: "companion"; id: string }
  | null;

export type AppShellState = {
  route: ShellRoute;
  returnRoute: ShellRoute;
  modal: TransientModal;
  browserWorkspace: boolean;
};

export type AppShellAction =
  | { type: "OPEN_MEETING" }
  | { type: "OPEN_LIBRARY" }
  | { type: "OPEN_FOLLOWUPS" }
  | { type: "OPEN_MANAGEMENT"; page: ManagementPage; taskFocus?: TaskFocus }
  | { type: "SET_MANAGEMENT_PAGE"; page: ManagementPage; taskFocus?: TaskFocus }
  | { type: "CLOSE_MANAGEMENT" }
  | {
      type: "OPEN_RECORDING";
      targetRecordingId?: string;
      phase?: RecordingShellPhase;
    }
  | { type: "SET_RECORDING_PHASE"; phase: RecordingShellPhase }
  | { type: "EXIT_RECORDING" }
  | { type: "OPEN_MODAL"; modal: Exclude<TransientModal, null> }
  | { type: "TOGGLE_SEARCH" }
  | { type: "CLOSE_MODAL" }
  | { type: "APPLY_HASH"; hash: string };

const COMPANION_HASH_RE = /^#companion-recording=([0-9a-f]{32})$/;

export function parseShellHash(hash: string): {
  managementPage?: ManagementPage;
  companionId?: string;
} {
  if (hash === "#management/tasks") {
    return { managementPage: "tasks" };
  }
  if (hash === "#management/recovery") {
    return { managementPage: "recovery" };
  }
  const companion = COMPANION_HASH_RE.exec(hash)?.[1];
  if (companion) {
    return { companionId: companion };
  }
  return {};
}

export function isRecordingLocked(
  state: Pick<AppShellState, "route">,
): boolean {
  return state.route.kind === "recording" && state.route.phase === "active";
}

export function canSwitchRoute(
  state: AppShellState,
  targetKind: ShellRoute["kind"],
): boolean {
  if (isRecordingLocked(state)) {
    return false;
  }
  if (
    state.browserWorkspace &&
    (targetKind === "recording" || targetKind === "management")
  ) {
    return false;
  }
  return true;
}

function resolveReturnRoute(state: AppShellState): ShellRoute {
  if (state.route.kind === "management") {
    return state.returnRoute.kind === "management"
      ? { kind: "meeting" }
      : state.returnRoute;
  }
  return state.route;
}

export function createInitialShellState({
  hash = "",
  browserWorkspace = false,
}: {
  hash?: string;
  browserWorkspace?: boolean;
} = {}): AppShellState {
  const parsed = parseShellHash(hash);
  let route: ShellRoute = { kind: "meeting" };
  let modal: TransientModal = null;

  if (parsed.managementPage && !browserWorkspace) {
    route = { kind: "management", page: parsed.managementPage };
  } else if (parsed.companionId) {
    modal = { kind: "companion", id: parsed.companionId };
  }

  return {
    route,
    returnRoute: { kind: "meeting" },
    modal,
    browserWorkspace,
  };
}

export function reduceShellState(
  state: AppShellState,
  action: AppShellAction,
): AppShellState {
  switch (action.type) {
    case "OPEN_MEETING":
      if (!canSwitchRoute(state, "meeting")) return state;
      return { ...state, route: { kind: "meeting" }, modal: null };

    case "OPEN_LIBRARY":
      if (!canSwitchRoute(state, "library")) return state;
      return { ...state, route: { kind: "library" }, modal: null };

    case "OPEN_FOLLOWUPS":
      if (!canSwitchRoute(state, "followups")) return state;
      return { ...state, route: { kind: "followups" }, modal: null };

    case "OPEN_MANAGEMENT":
      if (!canSwitchRoute(state, "management")) return state;
      return {
        ...state,
        route: {
          kind: "management",
          page: action.page,
          taskFocus: action.taskFocus,
        },
        returnRoute: resolveReturnRoute(state),
        modal: null,
      };

    case "SET_MANAGEMENT_PAGE":
      if (state.browserWorkspace || isRecordingLocked(state)) return state;
      return {
        ...state,
        route: {
          kind: "management",
          page: action.page,
          taskFocus: action.taskFocus,
        },
        returnRoute: resolveReturnRoute(state),
        modal: null,
      };

    case "CLOSE_MANAGEMENT":
      if (state.route.kind !== "management") return state;
      return {
        ...state,
        route:
          state.returnRoute.kind === "management"
            ? { kind: "meeting" }
            : state.returnRoute,
        modal: null,
      };

    case "OPEN_RECORDING":
      if (!canSwitchRoute(state, "recording")) return state;
      return {
        ...state,
        route: {
          kind: "recording",
          targetRecordingId: action.targetRecordingId,
          phase: action.phase ?? "setup",
        },
        modal: null,
      };

    case "SET_RECORDING_PHASE":
      if (state.route.kind !== "recording") return state;
      if (state.route.phase === action.phase) return state;
      return {
        ...state,
        route: { ...state.route, phase: action.phase },
        modal: action.phase === "active" ? null : state.modal,
      };

    case "EXIT_RECORDING":
      if (state.route.kind !== "recording") return state;
      return {
        ...state,
        route: { kind: "meeting" },
        modal: null,
      };

    case "OPEN_MODAL":
      if (isRecordingLocked(state)) return state;
      return { ...state, modal: action.modal };

    case "TOGGLE_SEARCH":
      if (isRecordingLocked(state)) return state;
      return {
        ...state,
        modal: state.modal?.kind === "search" ? null : { kind: "search" },
      };

    case "CLOSE_MODAL":
      if (!state.modal) return state;
      return { ...state, modal: null };

    case "APPLY_HASH": {
      if (isRecordingLocked(state)) return state;
      const parsed = parseShellHash(action.hash);
      if (parsed.managementPage && !state.browserWorkspace) {
        return {
          ...state,
          route: { kind: "management", page: parsed.managementPage },
          returnRoute: resolveReturnRoute(state),
          modal: null,
        };
      }
      if (parsed.companionId) {
        return {
          ...state,
          modal: { kind: "companion", id: parsed.companionId },
        };
      }
      return state;
    }
  }
}
