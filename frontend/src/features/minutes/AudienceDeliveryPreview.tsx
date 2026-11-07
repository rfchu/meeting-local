import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api";

export default function AudienceDeliveryPreview({
  meetingId,
  artifactId,
  includeCitations,
}: {
  meetingId: string;
  artifactId: string;
  includeCitations: boolean;
}) {
  const [audience, setAudience] = useState<"internal" | "external">("external");
  const [selection, setSelection] = useState<string[]>([]);
  const preview = useQuery({
    queryKey: [
      "audience-preview",
      meetingId,
      artifactId,
      audience,
      selection,
      includeCitations,
    ],
    queryFn: () =>
      api.audiencePreview(meetingId, {
        artifact_id: artifactId,
        audience,
        selection,
        include_citations: includeCitations,
      }),
  });
  return (
    <section aria-label="按受众预览">
      <label>
        交付受众{" "}
        <select
          value={audience}
          onChange={(event) => {
            setAudience(
              event.target.value === "internal" ? "internal" : "external",
            );
            setSelection([]);
          }}
        >
          <option value="external">对外版</option>
          <option value="internal">内部版</option>
        </select>
      </label>
      {audience === "external" && (
        <p>逐项选入可分享的内容。对外版使用通用标题，不附原文或引用。</p>
      )}
      {preview.isPending && <p role="status">正在生成预览…</p>}
      {preview.isError && (
        <p role="alert">
          {preview.error.message}{" "}
          <button
            onClick={() =>
              selection.length ? setSelection([]) : void preview.refetch()
            }
          >
            {selection.length ? "清空选择并重试" : "重试"}
          </button>
        </p>
      )}
      {preview.data && (
        <>
          {preview.data.candidates.map((item) => (
            <div key={item.id}>
              <label>
                <input
                  type="checkbox"
                  checked={selection.includes(item.id)}
                  onChange={(event) =>
                    setSelection((old) =>
                      event.target.checked
                        ? [...old, item.id].sort()
                        : old.filter((id) => id !== item.id),
                    )
                  }
                />
                {item.label} · {item.content}
              </label>
            </div>
          ))}
          <pre style={{ whiteSpace: "pre-wrap" }}>{preview.data.markdown}</pre>
        </>
      )}
    </section>
  );
}
