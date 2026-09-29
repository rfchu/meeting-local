import type {
  Artifact,
  Environment,
  LLMConfig,
  LLMConfigInput,
  Meeting,
  MeetingMinutes,
  MinutesTemplate,
  MeetingSummary,
  Revision,
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, init);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: unknown };
      detail =
        typeof body.detail === "string"
          ? body.detail
          : JSON.stringify(body.detail);
    } catch {
      /* retain status text */
    }
    throw new Error(detail);
  }
  return response.status === 204
    ? (undefined as T)
    : ((await response.json()) as T);
}

const json = (method: string, body?: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: body === undefined ? undefined : JSON.stringify(body),
});

export const api = {
  environment: () => request<Environment>("/status"),
  meetings: () => request<MeetingSummary[]>("/meetings"),
  meeting: (id: string) => request<Meeting>(`/meetings/${id}`),
  upload: (file: File) => {
    const data = new FormData();
    data.append("file", file);
    return request<Meeting>("/meetings", { method: "POST", body: data });
  },
  rename: (id: string, title: string) =>
    request(`/meetings/${id}`, json("PATCH", { title })),
  remove: (id: string) => request(`/meetings/${id}`, { method: "DELETE" }),
  transcribe: (
    id: string,
    body: { engine: string; diarization: boolean; num_speakers: number },
  ) => request<{ id: string }>(`/meetings/${id}/jobs`, json("POST", body)),
  jobAction: (id: string, action: "cancel" | "retry") =>
    request(`/jobs/${id}/${action}`, { method: "POST" }),
  saveTranscript: (id: string, revision: number, segments: unknown[]) =>
    request<{ revision: number }>(
      `/meetings/${id}/transcript`,
      json("PUT", { revision, segments }),
    ),
  revision: (id: string, revision: number) =>
    request<Revision>(`/meetings/${id}/revisions/${revision}`),
  analyze: (id: string, body: { profile_id?: string; model?: string }) =>
    request<{ id: string }>(
      `/meetings/${id}/analysis-jobs`,
      json("POST", body),
    ),
  minutesTemplates: () => request<MinutesTemplate[]>("/minutes-templates"),
  generateMinutes: (id: string, style: string, templateId: string) =>
    request<Artifact<MeetingMinutes>>(
      `/meetings/${id}/minutes`,
      json("POST", { style, template_id: templateId }),
    ),
  generateAiMinutes: (
    id: string,
    body: {
      profile_id?: string;
      model?: string;
      mode: "quick" | "full";
      template_id: string;
    },
  ) =>
    request<Artifact<MeetingMinutes>>(
      `/meetings/${id}/minutes/ai`,
      json("POST", body),
    ),
  saveMinutes: (
    id: string,
    sourceAnalysisId: string | null,
    document: MeetingMinutes,
  ) =>
    request<Artifact<MeetingMinutes>>(
      `/meetings/${id}/minutes`,
      json("PUT", { source_analysis_id: sourceAnalysisId, document }),
    ),
  configs: () => request<LLMConfig[]>("/llm-configs"),
  saveConfig: (body: LLMConfigInput) =>
    body.config_id
      ? request<LLMConfig>(`/llm-configs/${body.config_id}`, json("PUT", body))
      : request<LLMConfig>("/llm-configs", json("POST", body)),
  testConfig: (body: LLMConfigInput) =>
    request<{
      ok: boolean;
      models: string[];
      model_available: boolean;
      detected_context_window: number | null;
      effective_context_window: number;
      analysis_max_chars: number;
    }>("/llm-configs/test", json("POST", body)),
  deleteConfig: (id: string) =>
    request(`/llm-configs/${id}`, { method: "DELETE" }),
};

export const downloadUrl = (path: string) => `/api${path}`;
export const download = (path: string) => {
  const link = document.createElement("a");
  link.href = path.startsWith("/api/") ? path : downloadUrl(path);
  link.download = "";
  link.click();
};
