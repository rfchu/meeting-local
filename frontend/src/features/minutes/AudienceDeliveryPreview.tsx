import AudienceTemplateEditor from "./AudienceTemplateEditor";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api";

export default function AudienceDeliveryPreview({
  meetingId,
  artifactId,
  includeCitations,
  onSaved,
}: {
  meetingId: string;
  artifactId: string;
  includeCitations: boolean;
  onSaved: () => Promise<void>;
}) {
  const [audience, setAudience] = useState<"internal" | "external">("external");
  const [selection, setSelection] = useState<string[]>([]);
  const [checkedFingerprint, setCheckedFingerprint] = useState("");
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState("");
  const [custom, setCustom] = useState(false);
  const template = useQuery({
    queryKey: ["audience-template", audience],
    queryFn: () => api.audienceTemplate(audience),
  });
  const templateVersion = custom ? template.data?.version || 0 : 0;
  const input = {
    artifact_id: artifactId,
    audience,
    selection,
    include_citations: includeCitations,
    template_version: templateVersion,
  };
  const preview = useQuery({
    queryKey: [
      "audience-preview",
      meetingId,
      artifactId,
      audience,
      selection,
      includeCitations,
      templateVersion,
    ],
    queryFn: () => api.audiencePreview(meetingId, input),
  });
  return (
    <section aria-label="按受众预览">
      <label>
        交付受众{" "}
        <select
          disabled={saving}
          value={audience}
          onChange={(event) => {
            setAudience(
              event.target.value === "internal" ? "internal" : "external",
            );
            setCustom(false);
            setSelection([]);
            setResult("");
            setCheckedFingerprint("");
          }}
        >
          <option value="external">对外版</option>
          <option value="internal">内部版</option>
        </select>
      </label>
      <label>
        交付模板{" "}
        <select
          aria-label="交付模板"
          disabled={saving}
          value={custom ? "custom" : "default"}
          onChange={(event) => {
            setCustom(event.target.value === "custom");
            setSelection([]);
            setCheckedFingerprint("");
            setResult("");
          }}
        >
          <option value="default">内置默认</option>
          {!!template.data?.version && (
            <option value="custom">自定义 · v{template.data.version}</option>
          )}
        </select>
      </label>
      <AudienceTemplateEditor
        key={audience}
        audience={audience}
        onSaved={() => {
          setSelection([]);
          setCheckedFingerprint("");
          setResult("");
        }}
      />
      {audience === "external" && (
        <p>逐项选入可分享的内容。对外版使用通用标题，不附原文或引用。</p>
      )}
      {preview.isPending && <p role="status">正在生成预览…</p>}
      {preview.isError && (
        <p role="alert">
          {preview.error.message}{" "}
          <button
            onClick={() =>
              selection.length
                ? setSelection([])
                : void template.refetch().then(() => preview.refetch())
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
                  disabled={saving}
                  checked={selection.includes(item.id)}
                  onChange={(event) => {
                    setResult("");
                    setCheckedFingerprint("");
                    setSelection((old) =>
                      event.target.checked
                        ? [...old, item.id].sort()
                        : old.filter((id) => id !== item.id),
                    );
                  }}
                />
                {item.label} · {item.content}
              </label>
            </div>
          ))}
          <pre style={{ whiteSpace: "pre-wrap" }}>{preview.data.markdown}</pre>
          <label>
            <input
              type="checkbox"
              disabled={
                !preview.data.can_finalize || saving || preview.isFetching
              }
              checked={checkedFingerprint === preview.data.fingerprint}
              onChange={(event) =>
                setCheckedFingerprint(
                  event.target.checked ? preview.data.fingerprint : "",
                )
              }
            />
            已核对本次预览，确认保存
            {audience === "external" ? "对外版" : "内部版"}
          </label>
          <button
            className="primary"
            disabled={
              !preview.data.can_finalize ||
              preview.isError ||
              preview.isFetching ||
              saving ||
              checkedFingerprint !== preview.data.fingerprint
            }
            onClick={async () => {
              setSaving(true);
              setResult("");
              try {
                await api.finalizeAudience(
                  meetingId,
                  input,
                  checkedFingerprint,
                );
                setResult("已保存交付版本，可在下方历史记录中查看。");
                await onSaved();
              } catch (error) {
                setResult((error as Error).message);
              } finally {
                setSaving(false);
                setCheckedFingerprint("");
                await preview.refetch();
              }
            }}
          >
            {saving ? "正在保存…" : "确认并保存交付版本"}
          </button>
        </>
      )}
      {result && <p role="status">{result}</p>}
    </section>
  );
}
