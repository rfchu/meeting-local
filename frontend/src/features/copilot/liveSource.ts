import type { LiveDraftSegment, SourceTrack } from "../../types";
import { draftSourceId } from "../overview/notes";

export function locateLiveSource(
  confirmed: LiveDraftSegment[],
  time: number,
  itemId?: string,
  track?: SourceTrack | null,
): string | null {
  const exact = confirmed.find((part) => draftSourceId(part) === itemId);
  if (exact) return draftSourceId(exact);
  const matching = confirmed.filter(
    (part) =>
      draftSourceId(part) &&
      (!track || part.source_track === track) &&
      part.start <= time &&
      part.end >= time,
  );
  // Untyped marks at a simultaneous utterance have no known source track.
  if (!track && new Set(matching.map((part) => part.source_track)).size > 1)
    return null;
  const latest = matching.at(-1);
  return latest ? draftSourceId(latest) : null;
}
