/** Find the first enabled index in a menu item list, or -1 if none. */
export function firstEnabledMenuIndex(disabledFlags: boolean[]): number {
  return disabledFlags.findIndex((disabled) => !disabled);
}

/** Find the last enabled index in a menu item list, or -1 if none. */
export function lastEnabledMenuIndex(disabledFlags: boolean[]): number {
  for (let i = disabledFlags.length - 1; i >= 0; i--) {
    if (!disabledFlags[i]) return i;
  }
  return -1;
}

/** Find the next/previous enabled index with wraparound, skipping disabled items. */
export function nextEnabledMenuIndex(
  disabledFlags: boolean[],
  currentIndex: number,
  direction: 1 | -1,
): number {
  const count = disabledFlags.length;
  if (!count) return -1;
  const start =
    currentIndex >= 0 && currentIndex < count
      ? currentIndex
      : direction === 1
        ? -1
        : 0;
  for (let step = 1; step <= count; step++) {
    const candidate = (start + direction * step + count) % count;
    if (!disabledFlags[candidate]) return candidate;
  }
  return -1;
}
