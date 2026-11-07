import type { MinutesDelivery } from "../../types";

export function deliveryLocalDate(created: string) {
  const date = new Date(
    created.includes("T") ? created : created.replace(" ", "T") + "Z",
  );
  if (!Number.isFinite(date.getTime())) return "";
  return [date.getFullYear(), date.getMonth() + 1, date.getDate()]
    .map((part) => String(part).padStart(2, "0"))
    .join("-");
}

export type DeliveryFilters = {
  audience: "" | "internal" | "external";
  from: string;
  through: string;
  sent: "" | "sent" | "unmarked" | "unknown";
};

export const defaultDeliveryFilters: DeliveryFilters = {
  audience: "",
  from: "",
  through: "",
  sent: "",
};

export function deliverySentStatus(delivery: MinutesDelivery) {
  return delivery.sent_status ?? (delivery.sent_at ? "sent" : "unknown");
}

export function matchesDelivery(
  delivery: MinutesDelivery,
  filters: DeliveryFilters,
) {
  if (
    filters.audience &&
    (delivery.audience || "internal") !== filters.audience
  )
    return false;
  if (filters.sent && deliverySentStatus(delivery) !== filters.sent)
    return false;
  if (!filters.from && !filters.through) return true;
  const date = deliveryLocalDate(delivery.created);
  return (
    !!date &&
    (!filters.from || date >= filters.from) &&
    (!filters.through || date <= filters.through)
  );
}
