import { useCallback, useRef, useState } from "react";
import type { MeetingView } from "../preferences/meetingView";

export function useMeetingPlayback() {
  const [activeSegment, setActiveSegment] = useState<string>();
  const seekRef = useRef<(time: number, track?: string) => void>(() => {});
  const pausePlaybackRef = useRef<() => void>(() => {});
  const [playbackRestore, setPlaybackRestore] = useState<MeetingView>({});

  const seek = useCallback((time: number, track?: string) => {
    seekRef.current?.(time, track);
  }, []);

  const pause = useCallback(() => {
    pausePlaybackRef.current?.();
  }, []);

  return {
    activeSegment,
    setActiveSegment,
    seekRef,
    pausePlaybackRef,
    playbackRestore,
    setPlaybackRestore,
    seek,
    pause,
  };
}
