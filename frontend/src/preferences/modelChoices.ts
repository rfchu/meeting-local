/** Browser-local model IDs only; never persist credentials or cloud consent. */
export type ModelChoice =
  "analysis" | "minutes" | "notes" | "speakers" | "qa" | "transcription";
const prefix = "tingji.model-choice.v1.";
export function readModelChoice(
  slot: ModelChoice,
  storage: Pick<Storage, "getItem"> | undefined,
): string {
  try {
    const value = storage?.getItem(prefix + slot);
    return typeof value === "string" && value.length <= 500 ? value : "";
  } catch {
    return "";
  }
}
export function saveModelChoice(
  slot: ModelChoice,
  value: string,
  storage: Pick<Storage, "setItem"> | undefined,
): boolean {
  try {
    if (!storage) return false;
    storage.setItem(prefix + slot, value);
    return true;
  } catch {
    return false;
  }
}
export function resolveModelChoice(
  value: string,
  choices: string[],
  fallback = "",
): string {
  return choices.includes(value)
    ? value
    : choices.includes(fallback)
      ? fallback
      : choices[0] || "";
}
