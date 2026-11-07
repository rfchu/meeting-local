export type MinutesOptions = {
  output_language?: "zh" | "en";
  template_id: string;
  detail_level: "brief" | "standard" | "detailed";
  audience: "external" | "internal";
  method: "ai" | "analysis" | "current";
};
export const defaultMinutesOptions: MinutesOptions = {
  template_id: "general-meeting",
  detail_level: "standard",
  audience: "external",
  method: "ai",
};
export function readMinutesOptions(): MinutesOptions {
  try {
    const raw = JSON.parse(
      localStorage.getItem("tingji.minutes.options.v1") || "{}",
    );
    return {
      ...(["en", "zh"].includes(raw.output_language)
        ? { output_language: raw.output_language }
        : {}),
      template_id:
        typeof raw.template_id === "string"
          ? raw.template_id
          : "general-meeting",
      detail_level: ["brief", "standard", "detailed"].includes(raw.detail_level)
        ? raw.detail_level
        : "standard",
      audience: raw.audience === "internal" ? "internal" : "external",
      method: raw.method === "analysis" ? "analysis" : "ai",
    };
  } catch {
    return { ...defaultMinutesOptions };
  }
}
export function persistMinutesOptions(options: MinutesOptions) {
  try {
    localStorage.setItem("tingji.minutes.options.v1", JSON.stringify(options));
  } catch {
    /* Selection remains usable without browser storage. */
  }
}
