import type { MeetingView } from "../preferences/meetingView";
import { useEffect, useRef, useState } from "react";
import type { Segment } from "../types";
import { clock } from "../utils";

interface Props {
  initialView?: MeetingView;
  onActiveSegment?: (id: string | undefined) => void;
  onBookmark?: (bookmark: MeetingView) => void;
  meetingId: string;
  segments: Segment[];
  seekRef: React.MutableRefObject<((time: number) => void) | null>;
  audioTracks?: string[];
  pauseRef?: React.MutableRefObject<(() => void) | null>;
}
export default function Player({
  meetingId,
  segments,
  seekRef,
  pauseRef,
  audioTracks = [],
  initialView = {},
  onBookmark,
  onActiveSegment,
}: Props) {
  const audio = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false),
    [time, setTime] = useState(initialView.position || 0),
    [duration, setDuration] = useState(0),
    [track, setTrack] = useState(initialView.track || "");
  const [rate, setRate] = useState(initialView.rate || 1);
  const pendingPosition = useRef(initialView.position || 0),
    ready = useRef(false),
    lastSave = useRef(0);
  const bookmarkRef = useRef(onBookmark);
  bookmarkRef.current = onBookmark;
  const savePosition = () => {
    const a = audio.current;
    if (a && ready.current)
      bookmarkRef.current?.({
        position: a.currentTime,
        track,
        rate: a.playbackRate,
      });
  };
  useEffect(() => {
    if (pauseRef) pauseRef.current = () => audio.current?.pause();
    seekRef.current = (value) => {
      if (!audio.current) return;
      pendingPosition.current = value;
      if (ready.current) audio.current.currentTime = value;
      setTime(value);
      void audio.current.play().catch(() => setPlaying(false));
    };
    return () => {
      seekRef.current = null;
      if (pauseRef) pauseRef.current = null;
    };
  }, [seekRef, pauseRef]);
  const activeSegment = segments.find(
    (segment) => segment.start <= time && segment.end > time,
  )?.id;
  useEffect(() => {
    onActiveSegment?.(activeSegment);
  }, [activeSegment, onActiveSegment]);
  return (
    <div className="player">
      <button
        className="play"
        aria-label="播放暂停"
        onClick={() => {
          const a = audio.current!;
          if (a.paused) void a.play().catch(() => setPlaying(false));
          else a.pause();
        }}
      >
        {playing ? "Ⅱ" : "▶"}
      </button>
      <span>{clock(time)}</span>
      <input
        id="seek"
        type="range"
        min="0"
        max={duration || 1}
        step=".1"
        value={time}
        aria-label="播放位置"
        onChange={(e) => {
          audio.current!.currentTime = Number(e.target.value);
          setTime(Number(e.target.value));
          savePosition();
        }}
      />
      <span>{clock(duration)}</span>
      {!!audioTracks.length && (
        <select
          aria-label="回听音轨"
          value={track}
          onChange={(e) => {
            pendingPosition.current = audio.current?.currentTime || time;
            ready.current = false;
            setTrack(e.target.value);
          }}
        >
          <option value="">混合录音</option>
          {audioTracks.map((name) => (
            <option value={name} key={name}>
              {name === "system" ? "电脑声音" : "麦克风"}
            </option>
          ))}
        </select>
      )}
      <select
        aria-label="播放速度"
        value={String(rate)}
        onChange={(e) => {
          audio.current!.playbackRate = Number(e.target.value);
          setRate(Number(e.target.value));
          savePosition();
        }}
      >
        <option value="0.75">0.75×</option>
        <option value="1">1.0×</option>
        <option value="1.25">1.25×</option>
        <option value="1.5">1.5×</option>
        <option value="2">2.0×</option>
      </select>
      <audio
        ref={audio}
        src={
          track
            ? `/api/meetings/${meetingId}/audio-tracks/${track}`
            : `/api/meetings/${meetingId}/audio`
        }
        preload="metadata"
        onPlay={() => setPlaying(true)}
        onPause={() => {
          setPlaying(false);
          savePosition();
        }}
        onTimeUpdate={(e) => {
          setTime(e.currentTarget.currentTime);
          if (Date.now() - lastSave.current > 1500) {
            lastSave.current = Date.now();
            savePosition();
          }
        }}
        onLoadedMetadata={(e) => {
          const a = e.currentTarget;
          const duration = Number.isFinite(a.duration) ? a.duration : 0;
          setDuration(duration);
          a.currentTime = Math.min(
            pendingPosition.current,
            duration || pendingPosition.current,
          );
          a.playbackRate = rate;
          setTime(a.currentTime);
          ready.current = true;
          savePosition();
        }}
      />
    </div>
  );
}
