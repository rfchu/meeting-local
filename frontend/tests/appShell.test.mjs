import test from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';

const compile = async (path) => {
  const output = await build({
    entryPoints: [new URL(path, import.meta.url).pathname],
    bundle: true,
    format: 'esm',
    external: ['react'],
    write: false,
  });
  return import(
    'data:text/javascript;base64,' +
      Buffer.from(output.outputFiles[0].text).toString('base64')
  );
};

const {
  UNIFIED_MANAGEMENT_LABEL,
  resolveMeetingStatusIndicator,
  createInitialShellState,
  reduceShellState,
  parseShellHash,
  isRecordingLocked,
  canSwitchRoute,
} = await compile('../src/shell/shellState.ts');

test('unified management label matches design decision', () => {
  assert.equal(UNIFIED_MANAGEMENT_LABEL, '运行与模型管理');
});

test('route transitions clear any open transient modal and stay mutually exclusive', () => {
  let state = createInitialShellState();
  assert.deepEqual(state.route, { kind: 'meeting' });
  assert.equal(state.modal, null);

  // Open search modal, then open rename modal -> replaces search modal
  state = reduceShellState(state, { type: 'OPEN_MODAL', modal: { kind: 'search' } });
  assert.deepEqual(state.modal, { kind: 'search' });
  state = reduceShellState(state, { type: 'OPEN_MODAL', modal: { kind: 'rename' } });
  assert.deepEqual(state.modal, { kind: 'rename' });

  // Switch route to library -> clears transient modal
  state = reduceShellState(state, { type: 'OPEN_LIBRARY' });
  assert.deepEqual(state.route, { kind: 'library' });
  assert.equal(state.modal, null);

  // Open followups directly from library and toggle search inside followups
  state = reduceShellState(state, { type: 'OPEN_FOLLOWUPS' });
  assert.deepEqual(state.route, { kind: 'followups' });
  state = reduceShellState(state, { type: 'TOGGLE_SEARCH' });
  assert.deepEqual(state.modal, { kind: 'search' });
  state = reduceShellState(state, { type: 'TOGGLE_SEARCH' });
  assert.equal(state.modal, null);
});

test('management route preserves returnRoute across page switches and restores it on close', () => {
  let state = createInitialShellState();
  state = reduceShellState(state, { type: 'OPEN_LIBRARY' });
  state = reduceShellState(state, {
    type: 'OPEN_MANAGEMENT',
    page: 'overview',
  });
  assert.deepEqual(state.route, {
    kind: 'management',
    page: 'overview',
    taskFocus: undefined,
  });

  state = reduceShellState(state, {
    type: 'SET_MANAGEMENT_PAGE',
    page: 'models',
    taskFocus: { category: 'minutes' },
  });
  assert.deepEqual(state.route, {
    kind: 'management',
    page: 'models',
    taskFocus: { category: 'minutes' },
  });

  state = reduceShellState(state, { type: 'CLOSE_MANAGEMENT' });
  assert.deepEqual(state.route, { kind: 'library' });
});

test('recording setup allows sidebar navigation, while active recording locks route switching until exit', () => {
  let state = createInitialShellState();
  state = reduceShellState(state, { type: 'OPEN_RECORDING' });
  assert.deepEqual(state.route, {
    kind: 'recording',
    targetRecordingId: undefined,
    phase: 'setup',
  });
  assert.equal(isRecordingLocked(state), false);
  assert.equal(canSwitchRoute(state, 'meeting'), true);

  // In setup phase, user can freely jump back to a meeting or library
  const backToMeeting = reduceShellState(state, { type: 'OPEN_MEETING' });
  assert.deepEqual(backToMeeting.route, { kind: 'meeting' });

  // Transition into active recording phase -> navigation locked & open modals cleared
  let active = reduceShellState(state, {
    type: 'OPEN_MODAL',
    modal: { kind: 'search' },
  });
  active = reduceShellState(active, {
    type: 'SET_RECORDING_PHASE',
    phase: 'active',
  });
  assert.equal(isRecordingLocked(active), true);
  assert.equal(active.modal, null);
  assert.equal(canSwitchRoute(active, 'library'), false);
  assert.equal(canSwitchRoute(active, 'management'), false);

  // Route navigation & hash changes are ignored while recording is active
  assert.strictEqual(
    reduceShellState(active, { type: 'OPEN_LIBRARY' }),
    active,
  );
  assert.strictEqual(
    reduceShellState(active, { type: 'TOGGLE_SEARCH' }),
    active,
  );
  assert.strictEqual(
    reduceShellState(active, { type: 'APPLY_HASH', hash: '#management/tasks' }),
    active,
  );

  // Exiting recording restores meeting route and unlocks navigation
  const exited = reduceShellState(active, { type: 'EXIT_RECORDING' });
  assert.deepEqual(exited.route, { kind: 'meeting' });
  assert.equal(isRecordingLocked(exited), false);
});

test('hash deep links resolve #management/tasks, #management/recovery, and #companion-recording', () => {
  assert.deepEqual(parseShellHash('#management/tasks'), {
    managementPage: 'tasks',
  });
  assert.deepEqual(parseShellHash('#management/recovery'), {
    managementPage: 'recovery',
  });
  const cid = '0123456789abcdef0123456789abcdef';
  assert.deepEqual(parseShellHash(`#companion-recording=${cid}`), {
    companionId: cid,
  });
  assert.deepEqual(parseShellHash('#companion-recording=short'), {});

  const tasksInit = createInitialShellState({ hash: '#management/tasks' });
  assert.deepEqual(tasksInit.route, { kind: 'management', page: 'tasks' });
  assert.equal(tasksInit.modal, null);

  const recoveryInit = createInitialShellState({ hash: '#management/recovery' });
  assert.deepEqual(recoveryInit.route, { kind: 'management', page: 'recovery' });

  const companionInit = createInitialShellState({
    hash: `#companion-recording=${cid}`,
  });
  assert.deepEqual(companionInit.route, { kind: 'meeting' });
  assert.deepEqual(companionInit.modal, { kind: 'companion', id: cid });

  // Dynamic hashchange moves into management/recovery cleanly
  const dynamic = reduceShellState(createInitialShellState(), {
    type: 'APPLY_HASH',
    hash: '#management/recovery',
  });
  assert.deepEqual(dynamic.route, { kind: 'management', page: 'recovery' });
});

test('browserWorkspace blocks recording and management routes including deep links', () => {
  const init = createInitialShellState({
    hash: '#management/tasks',
    browserWorkspace: true,
  });
  assert.deepEqual(init.route, { kind: 'meeting' });

  const tryRecord = reduceShellState(init, { type: 'OPEN_RECORDING' });
  assert.strictEqual(tryRecord, init);

  const tryManage = reduceShellState(init, {
    type: 'OPEN_MANAGEMENT',
    page: 'overview',
  });
  assert.strictEqual(tryManage, init);
});

test('resolveMeetingStatusIndicator maps failed, running, cancelled, and ready states to semantic tones', () => {
  assert.deepEqual(
    resolveMeetingStatusIndicator({
      revision: 1,
      job: { state: 'failed' },
    }),
    { tone: 'failed', label: '失败' },
  );
  assert.deepEqual(
    resolveMeetingStatusIndicator({
      revision: 0,
      job: { state: 'running' },
    }),
    { tone: 'running', label: '处理中' },
  );
  assert.deepEqual(
    resolveMeetingStatusIndicator({
      revision: 0,
      job: { state: 'queued' },
    }),
    { tone: 'running', label: '排队中' },
  );
  assert.deepEqual(
    resolveMeetingStatusIndicator({
      revision: 0,
      job: null,
    }),
    { tone: 'pending', label: '待转写' },
  );
  assert.deepEqual(
    resolveMeetingStatusIndicator({
      revision: 2,
      job: { state: 'completed' },
    }),
    { tone: 'ready', label: '任务完成' },
  );
  assert.deepEqual(
    resolveMeetingStatusIndicator({
      revision: 2,
      job: null,
    }),
    { tone: 'ready', label: '已有文字稿' },
  );
});
