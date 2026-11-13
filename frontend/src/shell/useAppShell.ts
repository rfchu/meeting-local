import { useCallback, useEffect, useReducer } from "react";
import type { ManagementPage } from "../features/management/RuntimeManagement";
import type { TaskFocus } from "../types";
import {
  createInitialShellState,
  isRecordingLocked,
  parseShellHash,
  reduceShellState,
  type RecordingShellPhase,
  type TransientModal,
} from "./shellState";

export * from "./shellState";

export function useAppShell({
  browserWorkspace = false,
}: { browserWorkspace?: boolean } = {}) {
  const [state, dispatch] = useReducer(reduceShellState, undefined, () =>
    createInitialShellState({
      hash: typeof window !== "undefined" ? window.location.hash : "",
      browserWorkspace,
    }),
  );

  useEffect(() => {
    const onHashChange = () => {
      dispatch({ type: "APPLY_HASH", hash: window.location.hash });
    };
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  useEffect(() => {
    const handleGlobalShortcuts = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        dispatch({ type: "TOGGLE_SEARCH" });
      }
    };
    window.addEventListener("keydown", handleGlobalShortcuts);
    return () => window.removeEventListener("keydown", handleGlobalShortcuts);
  }, []);

  const clearCompanionHash = useCallback(() => {
    if (
      typeof window !== "undefined" &&
      parseShellHash(window.location.hash).companionId
    ) {
      window.history.replaceState(
        window.history.state,
        "",
        window.location.pathname + window.location.search,
      );
    }
  }, []);

  const openMeeting = useCallback(() => {
    clearCompanionHash();
    dispatch({ type: "OPEN_MEETING" });
  }, [clearCompanionHash]);

  const openLibrary = useCallback(() => {
    clearCompanionHash();
    dispatch({ type: "OPEN_LIBRARY" });
  }, [clearCompanionHash]);

  const openFollowups = useCallback(() => {
    clearCompanionHash();
    dispatch({ type: "OPEN_FOLLOWUPS" });
  }, [clearCompanionHash]);

  const openManagement = useCallback(
    (page: ManagementPage, taskFocus?: TaskFocus) => {
      clearCompanionHash();
      dispatch({ type: "OPEN_MANAGEMENT", page, taskFocus });
    },
    [clearCompanionHash],
  );

  const setManagementPage = useCallback(
    (page: ManagementPage, taskFocus?: TaskFocus) => {
      dispatch({ type: "SET_MANAGEMENT_PAGE", page, taskFocus });
    },
    [],
  );

  const closeManagement = useCallback(() => {
    dispatch({ type: "CLOSE_MANAGEMENT" });
  }, []);

  const openRecording = useCallback(
    (targetRecordingId?: string, phase?: RecordingShellPhase) => {
      clearCompanionHash();
      dispatch({ type: "OPEN_RECORDING", targetRecordingId, phase });
    },
    [clearCompanionHash],
  );

  const setRecordingPhase = useCallback((phase: RecordingShellPhase) => {
    dispatch({ type: "SET_RECORDING_PHASE", phase });
  }, []);

  const exitRecording = useCallback(() => {
    dispatch({ type: "EXIT_RECORDING" });
  }, []);

  const openModal = useCallback((modal: Exclude<TransientModal, null>) => {
    dispatch({ type: "OPEN_MODAL", modal });
  }, []);

  const closeModal = useCallback(() => {
    clearCompanionHash();
    dispatch({ type: "CLOSE_MODAL" });
  }, [clearCompanionHash]);

  return {
    state,
    route: state.route,
    modal: state.modal,
    recordingLocked: isRecordingLocked(state),
    openMeeting,
    openLibrary,
    openFollowups,
    openManagement,
    setManagementPage,
    closeManagement,
    openRecording,
    setRecordingPhase,
    exitRecording,
    openModal,
    closeModal,
  };
}
