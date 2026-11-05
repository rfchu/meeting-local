import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  readModelChoice,
  saveModelChoice,
  type ModelChoice,
} from "./modelChoices";
export interface ModelPreference {
  default_value: string;
  last_value: string;
  effective_value: string;
  source: string;
  version: number;
  invalid_last: boolean;
  invalid_default: boolean;
}
export interface ModelPreferences {
  roles: Record<ModelChoice, ModelPreference>;
  profiles: {
    id: string;
    name: string;
    model: string;
    provider_type: string;
  }[];
}
const roles: ModelChoice[] = [
  "analysis",
  "minutes",
  "overview",
  "notes",
  "speakers",
  "qa",
  "terminology",
  "transcription",
  "live_transcription",
  "correction",
];
function storage() {
  try {
    return window.localStorage;
  } catch {
    return undefined;
  }
}
async function request(
  path: string,
  body?: unknown,
): Promise<ModelPreferences> {
  const response = await fetch(
    "/api/model-preferences" + path,
    body === undefined
      ? undefined
      : {
          method: path === "/import" ? "POST" : "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || "模型偏好同步失败");
  }
  return response.json();
}
let migration: Promise<ModelPreferences> | undefined;
export function loadModelPreferences() {
  if (!migration) {
    const choices: Record<string, string> = {};
    for (const role of roles) {
      const value = readModelChoice(role, storage());
      if (value) choices[role] = value;
    }
    try {
      const old = JSON.parse(
        storage()?.getItem("tingji.recording.preferences.v1") || "null",
      );
      if (old?.version === 1) {
        if (
          !choices.live_transcription &&
          ["qwen", "whisper"].includes(old.draftEngine)
        )
          choices.live_transcription = old.draftEngine;
        if (!choices.correction && typeof old.correctionProfileId === "string")
          choices.correction = old.correctionProfileId;
      }
    } catch {
      /* Invalid browser preferences cannot overwrite server settings. */
    }
    const marker = "tingji.model-preferences.imported.v1";
    try {
      if (storage()?.getItem(marker) === "yes") return request("");
    } catch {}
    migration = request("/import", { choices })
      .then((result) => {
        try {
          storage()?.setItem(marker, "yes");
        } catch {}
        return result;
      })
      .catch((error) => {
        migration = undefined;
        throw error;
      });
  }
  return migration.then(() => request(""));
}
export function saveModelPreference(
  role: ModelChoice,
  kind: "default" | "last",
  value: string,
  version?: number,
) {
  return request(`/${role}/${kind}`, { value, version });
}
export function useModelChoice(slot: ModelChoice, scope = "") {
  const client = useQueryClient();
  const [value, setValue] = useState(
    () =>
      client.getQueryData<ModelPreferences>(["model-preferences"])?.roles[slot]
        ?.effective_value || "",
  );
  const override = useRef(false);
  const [error, setError] = useState("");
  const serial = useRef(Promise.resolve());
  const query = useQuery({
    queryKey: ["model-preferences"],
    queryFn: loadModelPreferences,
    staleTime: 30000,
  });
  useEffect(() => {
    override.current = false;
  }, [slot, scope]);
  useEffect(() => {
    if (!override.current && query.data)
      setValue(query.data.roles[slot]?.effective_value || "");
  }, [slot, scope, query.data]);
  const select = useCallback(
    (next: string) => {
      override.current = true;
      setValue(next);
      saveModelChoice(slot, next, storage());
      serial.current = serial.current
        .catch(() => {})
        .then(async () => {
          try {
            const result = await saveModelPreference(slot, "last", next);
            client.setQueryData(["model-preferences"], result);
            setError("");
          } catch (e) {
            setError((e as Error).message);
            window.dispatchEvent(
              new CustomEvent("tingji-model-preference-error", {
                detail: (e as Error).message,
              }),
            );
          }
        });
    },
    [slot, client],
  );
  return [
    value,
    select,
    {
      ready:
        !!query.data &&
        (override.current || value === query.data.roles[slot]?.effective_value),
      error: error || query.error?.message || "",
    },
  ] as const;
}
