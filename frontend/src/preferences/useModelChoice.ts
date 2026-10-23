import { useCallback, useState } from "react";
import {
  readModelChoice,
  saveModelChoice,
  type ModelChoice,
} from "./modelChoices";
function storage() {
  try {
    return window.localStorage;
  } catch {
    return undefined;
  }
}
export function useModelChoice(slot: ModelChoice) {
  const [value, setValue] = useState(() => readModelChoice(slot, storage()));
  const select = useCallback(
    (next: string) => {
      setValue(next);
      if (next) saveModelChoice(slot, next, storage());
    },
    [slot],
  );
  return [value, select] as const;
}
