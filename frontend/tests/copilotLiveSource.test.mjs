import test from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";

const compiled = await build({
  entryPoints: [new URL("../src/features/copilot/liveSource.ts", import.meta.url).pathname],
  bundle: true, write: false, format: "esm",
});
const { locateLiveSource } = await import(
  "data:text/javascript;base64," + Buffer.from(compiled.outputFiles[0].text).toString("base64")
);

test("an overlapping shorthand citation opens only its recorded track", () => {
  const system = { start: 4, end: 10, source_track: "system", live_range_id: "r", live_segment_index: 0 };
  const mic = { start: 4, end: 10, source_track: "microphone", live_range_id: "r", live_segment_index: 1 };
  assert.equal(locateLiveSource([system, mic], 5, "note", "microphone"), "r:1");
  assert.equal(locateLiveSource([system], 5, "note", "microphone"), null);
  assert.equal(locateLiveSource([system, mic], 5, "note"), null);
  assert.equal(locateLiveSource([system, mic], 5, "r:0"), "r:0");
});
