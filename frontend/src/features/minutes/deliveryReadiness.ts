import type { Meeting, MeetingReview } from "../../types";
export interface DeliveryIssue {
  code: string;
  label: string;
  target: "transcript" | "speakers" | "notes" | "minutes" | "actions";
}
export interface DeliveryReadiness {
  version: number;
  revision: number;
  artifact_id: string | null;
  include_citations: boolean;
  fingerprint: string;
  can_finalize: boolean;
  blockers: DeliveryIssue[];
  warnings: DeliveryIssue[];
  minutes_review_state: string;
  current_delivery_id: string | null;
  historical_count: number;
  review: MeetingReview;
}
export function deliveryReadyForView(
  value: DeliveryReadiness | undefined,
  meeting: Meeting,
  selectedId: string | null,
  dirty: boolean,
  includeCitations: boolean,
) {
  return (
    !!value &&
    value.version === 1 &&
    !dirty &&
    value.artifact_id === selectedId &&
    value.artifact_id === meeting.active_minutes_id &&
    value.revision === meeting.revision &&
    value.review.title === meeting.title &&
    value.include_citations === includeCitations
  );
}
export function deliveryIsCurrent(
  value: DeliveryReadiness | undefined,
  id: string,
) {
  return value?.current_delivery_id === id;
}
