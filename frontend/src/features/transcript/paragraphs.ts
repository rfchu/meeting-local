export interface ContinuousPart {
  start: number;
  end: number;
  text: string;
  speaker?: string;
  source_track?: string | null;
  continuation_id?: string;
  continues_previous?: boolean;
  continues_next?: boolean;
  boundary_reason?: string;
}
export function continuous(left: ContinuousPart, right: ContinuousPart) {
  return !!(
    (left.source_track ?? null) === (right.source_track ?? null) &&
    left.continues_next &&
    right.continues_previous &&
    left.continuation_id &&
    left.continuation_id === right.continuation_id &&
    Math.abs(left.end - right.start) <= 1 / 16000 + 0.000001 &&
    (left.speaker || "") === (right.speaker || "")
  );
}
export function paragraphs<T extends ContinuousPart>(
  segments: T[],
): (T & { parts: T[] })[] {
  const result: (T & { parts: T[] })[] = [];
  for (const segment of segments) {
    const previous = result[result.length - 1];
    if (
      previous &&
      continuous(previous.parts[previous.parts.length - 1], segment) &&
      previous.text.length + segment.text.length <= 3000 &&
      segment.end - previous.start <= 120
    ) {
      const separator =
        /[A-Za-z0-9]$/.test(previous.text) && /^[A-Za-z0-9]/.test(segment.text)
          ? " "
          : "";
      const lastPart = previous.parts[previous.parts.length - 1];
      if (
        lastPart.source_track &&
        lastPart.boundary_reason === "hard_limit" &&
        /^的[百千万]分之/.test(segment.text)
      ) {
        previous.text = previous.text.replace(/。$/, "");
      }
      previous.text += separator + segment.text;
      previous.end = segment.end;
      previous.parts.push(segment);
      previous.continues_next = segment.continues_next;
    } else result.push({ ...segment, parts: [segment] });
  }
  return result;
}
