import { useEffect, useRef, useState } from "react";
import type { Segment } from "../types";
import { clock } from "../utils";

interface Props {
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
}: Props) {
  const audio = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false),
    [time, setTime] = useState(0),
    [duration, setDuration] = useState(0),
    [track, setTrack] = useState("");
  useEffect(() => {
    if (pauseRef) pauseRef.current = () => audio.current?.pause();
    seekRef.current = (value) => {
      if (!audio.current) return;
      audio.current.currentTime = value;
      void audio.current.play();
    };
    return () => {
      seekRef.current = null;
      if (pauseRef) pauseRef.current = null;
    };
  }, [seekRef, pauseRef]);
  useEffect(() => {
    setTime(0);
    setDuration(0);
    setPlaying(false);
    setTrack("");
  }, [meetingId]);
  useEffect(() => {
    const active = segments.find(
      (segment) => segment.start <= time && segment.end > time,
    )?.id;
    document
      .querySelectorAll(".segment")
      .forEach((row) =>
        row.classList.toggle(
          "playing",
          (row as HTMLElement).dataset.id === active,
        ),
      );
  }, [segments, time]);
  return (
    <div className="player">
      <button
        className="play"
        aria-label="播放暂停"
        onClick={() => {
          const a = audio.current!;
          if (a.paused) void a.play();
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
        }}
      />
      <span>{clock(duration)}</span>
      {!!audioTracks.length && (
        <select
          aria-label="回听音轨"
          value={track}
          onChange={(e) => setTrack(e.target.value)}
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
        defaultValue="1"
        onChange={(e) => {
          audio.current!.playbackRate = Number(e.target.value);
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
        onPause={() => setPlaying(false)}
        onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)}
        onLoadedMetadata={(e) => setDuration(e.currentTarget.duration || 0)}
      />
    </div>
  );
}
