import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { api } from "../../api";
import type { NativeCaptureStatus } from "../../types";
import { watchRecordingRecovery } from "./recordingRecovery";
import { StatusPolling } from "./statusPolling";

export function useNativeCaptureStatus(
  id: string,
  enabled: boolean,
  callbacks: {
    status: (value: NativeCaptureStatus) => void;
    failure: (error: unknown) => void;
    checking: () => void;
  },
  phase: string,
) {
  const client = useQueryClient();
  const handlers = useRef(callbacks);
  handlers.current = callbacks;
  useEffect(() => {
    if (!id || !enabled) return;
    void client.invalidateQueries({ queryKey: ["recording-health", id] });
    const polling = new StatusPolling(
      (signal) => api.nativeStatus(id, signal),
      (value) => handlers.current.status(value),
      (error) => handlers.current.failure(error),
      () => handlers.current.checking(),
    );
    void polling.poll();
    const timer = window.setInterval(() => void polling.poll(), 1000);
    const unwatch = watchRecordingRecovery(() => void polling.poll(true));
    return () => {
      polling.close();
      window.clearInterval(timer);
      unwatch();
    };
  }, [id, enabled, phase, client]);
}
