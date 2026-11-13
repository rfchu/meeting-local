import type { Segment, SourceTrack } from "../../types";

export type ReviewFocusTarget = {
  time: number;
  segmentId?: string;
  track?: SourceTrack | null;
};

/** Resolve exact target segment first, then same-track overlap, then time range fallback. */
export function resolveReviewFocusedSegment(
  segments: Segment[],
  focus: ReviewFocusTarget | null,
): Segment | undefined {
  if (focus === null) return undefined;
  if (focus.segmentId) {
    const exact = segments.find((s) => s.id === focus.segmentId);
    if (exact) return exact;
  }
  if (focus.track) {
    const byTrack = segments.find(
      (s) =>
        (!s.source_track || s.source_track === focus.track) &&
        s.start <= focus.time &&
        s.end > focus.time,
    );
    if (byTrack) return byTrack;
  }
  return (
    segments.find((s) => s.start <= focus.time && s.end > focus.time) ||
    segments.find((s) => s.start >= focus.time)
  );
}
