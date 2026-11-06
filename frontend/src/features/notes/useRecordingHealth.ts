import { watchRecordingCommands } from "./recordingCommandEvents";
import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api";
import { pendingRecordingChunks } from "./recordingBuffer";
import { watchRecordingRecovery } from "./recordingRecovery";
import { healthFreshness } from "./healthFreshness";

export function healthQueryOptions(
  id: string,
  native: boolean,
  readServer = api.recordingHealth,
  readLocal = pendingRecordingChunks,
) {
  const common = {
    retry: false as const,
    gcTime: 0,
    refetchInterval: 5000,
    refetchOnWindowFocus: false as const,
    refetchOnReconnect: false as const,
    networkMode: "always" as const,
  };
  return {
    server: {
      ...common,
      queryKey: ["recording-health", id],
      queryFn: ({ signal }: { signal: AbortSignal }) => readServer(id, signal),
      enabled: !!id,
    },
    local: {
      ...common,
      queryKey: ["recording-pending-chunks", id],
      queryFn: () => readLocal(id),
      enabled: !!id && !native,
    },
  };
}

export function useRecordingHealth(id: string, native: boolean) {
  const [now, setNow] = useState(Date.now());
  const [rechecking, setRechecking] = useState(false);
  const epoch = useRef(0);
  const queries = healthQueryOptions(id, native);
  const server = useQuery(queries.server);
  // IndexedDB can be blocked independently. It must never hold up server health.
  const local = useQuery(queries.local);
  const { refetch: refetchServer } = server;
  const { refetch: refetchLocal } = local;
  const refresh = useCallback(async () => {
    if (!id) return;
    const attempt = ++epoch.current;
    setNow(Date.now());
    setRechecking(true);
    if (!native) void refetchLocal({ cancelRefetch: true });
    try {
      // Cancel the old HTTP snapshot so a pre-return request cannot restore a healthy label.
      await refetchServer({ cancelRefetch: true });
    } finally {
      if (epoch.current === attempt) {
        setNow(Date.now());
        setRechecking(false);
      }
    }
  }, [id, native, refetchServer, refetchLocal]);
  useEffect(() => {
    setRechecking(false);
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    const unwatch = watchRecordingRecovery(() => void refresh());
    const unwatchCommands = watchRecordingCommands((changedId) => {
      if (changedId === id) void refresh();
    });
    return () => {
      ++epoch.current;
      window.clearInterval(timer);
      unwatch();
      unwatchCommands();
    };
  }, [refresh, id]);
  const freshness = healthFreshness(
    server.dataUpdatedAt,
    Math.max(now, Date.now()),
    server.isError,
    rechecking,
  );
  const localCurrent =
    healthFreshness(
      local.dataUpdatedAt,
      Math.max(now, Date.now()),
      local.isError,
    ) === "current";
  return {
    inventory: server.data || null,
    pending: !native && localCurrent ? (local.data ?? null) : null,
    unavailable: freshness !== "current" || !server.data?.runtime,
    freshness,
    checkedAt: server.dataUpdatedAt,
    fetching: server.isFetching,
    refresh,
  };
}
