import test from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";

const result = await build({
  entryPoints: [new URL("../src/features/minutes/deliveryHistory.ts", import.meta.url).pathname],
  bundle: true, format: "esm", write: false,
});
const { matchesDelivery, defaultDeliveryFilters } = await import(
  "data:text/javascript;base64," + Buffer.from(result.outputFiles[0].text).toString("base64")
);

test("audience and local dates combine with inclusive endpoints", () => {
  const filters = { ...defaultDeliveryFilters, audience: "internal", from: "2026-11-07", through: "2026-11-07" };
  const created = (day, hour, minute) =>
    new Date(2026, 10, day, hour, minute).toISOString().slice(0, 19).replace("T", " ");
  assert(matchesDelivery({ created: created(7, 0, 0) }, filters));
  assert(matchesDelivery({ audience: "internal", created: created(7, 23, 59) }, filters));
  assert(!matchesDelivery({ audience: "external", created: created(7, 0, 0) }, filters));
  assert(!matchesDelivery({ created: created(8, 0, 0) }, filters));
});

test("send status separates legacy unknown from newer unmarked and explicit sent", () => {
  const filters = { ...defaultDeliveryFilters, audience: "internal" };
  const legacy = { audience: "internal" };
  const unmarked = { audience: "internal", sent_status: "unmarked" };
  const sent = { audience: "internal", sent_at: "2026-11-07 00:00:00" };
  assert(matchesDelivery(legacy, { ...filters, sent: "unknown" }));
  assert(!matchesDelivery(legacy, { ...filters, sent: "unmarked" }));
  assert(matchesDelivery(unmarked, { ...filters, sent: "unmarked" }));
  assert(!matchesDelivery(unmarked, { ...filters, sent: "sent" }));
  assert(matchesDelivery(sent, { ...filters, sent: "sent" }));
});
