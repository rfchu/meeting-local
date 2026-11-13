import test from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';

const compile = async (path) => {
  const output = await build({
    entryPoints: [new URL(path, import.meta.url).pathname],
    bundle: true,
    format: 'esm',
    external: ['react', '@tanstack/react-virtual'],
    write: false,
  });
  return import(
    'data:text/javascript;base64,' +
      Buffer.from(output.outputFiles[0].text).toString('base64')
  );
};

const { resolveReviewFocusedSegment } = await compile(
  '../src/features/meeting/reviewFocus.ts',
);
const {
  firstEnabledMenuIndex,
  lastEnabledMenuIndex,
  nextEnabledMenuIndex,
} = await compile('../src/components/menuNavigation.ts');

test('resolveReviewFocusedSegment prioritizes segmentId and track during dual-track overlap', () => {
  const segments = [
    {
      id: 'sys-1',
      start: 10,
      end: 18,
      text: '系统音频同步讨论',
      speaker: '远程参会人',
      source_track: 'system',
      boundary_review: false,
    },
    {
      id: 'mic-1',
      start: 10,
      end: 16,
      text: '本机麦克风同时回应',
      speaker: '主持人',
      source_track: 'microphone',
      boundary_review: false,
    },
  ];

  // Exact segmentId wins even when overlapping system segment appears first
  assert.equal(
    resolveReviewFocusedSegment(segments, {
      time: 12,
      segmentId: 'mic-1',
      track: 'microphone',
    })?.id,
    'mic-1',
  );

  // Track-specific fallback disambiguates when segmentId is omitted
  assert.equal(
    resolveReviewFocusedSegment(segments, {
      time: 12,
      track: 'microphone',
    })?.id,
    'mic-1',
  );

  // System track matches system segment
  assert.equal(
    resolveReviewFocusedSegment(segments, {
      time: 12,
      segmentId: 'sys-1',
      track: 'system',
    })?.id,
    'sys-1',
  );

  // Returns undefined when focus is null
  assert.equal(resolveReviewFocusedSegment(segments, null), undefined);
});

test('ActionMenu keyboard helpers skip disabled items and wrap cleanly', () => {
  const disabledFlags = [true, false, true, false, false];
  assert.equal(firstEnabledMenuIndex(disabledFlags), 1);
  assert.equal(lastEnabledMenuIndex(disabledFlags), 4);
  assert.equal(nextEnabledMenuIndex(disabledFlags, 1, 1), 3);
  assert.equal(nextEnabledMenuIndex(disabledFlags, 4, 1), 1);
  assert.equal(nextEnabledMenuIndex(disabledFlags, 1, -1), 4);
  assert.equal(nextEnabledMenuIndex(disabledFlags, -1, 1), 1);
  assert.equal(nextEnabledMenuIndex([true, true], 0, 1), -1);
});
