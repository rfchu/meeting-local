import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api";

export default function AudienceTemplateEditor({
  audience,
  onSaved,
}: {
  audience: "internal" | "external";
  onSaved?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<string[]>();
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const query = useQuery({
    queryKey: ["audience-template", audience],
    queryFn: () => api.audienceTemplate(audience),
    enabled: open,
  });
  const template = query.data;
  const columns = draft ?? template?.columns ?? [];
  return (
    <details onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>
        自定义栏目 {template?.version ? `· v${template.version}` : ""}
      </summary>
      {open && (
        <>
          {(error || query.isError) && (
            <p role="alert">
              {error || query.error?.message}{" "}
              <button
                onClick={() => {
                  setDraft(undefined);
                  setError("");
                  void query.refetch();
                }}
              >
                重新读取
              </button>
            </p>
          )}
          {template && (
            <fieldset disabled={saving}>
              <legend>栏目及顺序</legend>
              <ol>
                {columns.map((key, index) => (
                  <li key={key}>
                    {template.allowed[key]}{" "}
                    <button
                      type="button"
                      disabled={index === 0}
                      aria-label={`上移${template.allowed[key]}`}
                      onClick={() => {
                        const updated = [...columns];
                        [updated[index - 1], updated[index]] = [
                          updated[index],
                          updated[index - 1],
                        ];
                        setDraft(updated);
                      }}
                    >
                      上移
                    </button>{" "}
                    <button
                      type="button"
                      disabled={columns.length < 2}
                      onClick={() =>
                        setDraft(columns.filter((column) => column !== key))
                      }
                    >
                      移除
                    </button>
                  </li>
                ))}
              </ol>
              <select
                aria-label="添加栏目"
                value=""
                onChange={(event) => {
                  if (event.target.value)
                    setDraft([...columns, event.target.value]);
                }}
              >
                <option value="">添加栏目…</option>
                {Object.entries(template.allowed)
                  .filter(([key]) => !columns.includes(key))
                  .map(([key, label]) => (
                    <option key={key} value={key}>
                      {label}
                    </option>
                  ))}
              </select>{" "}
              <button
                disabled={!draft}
                onClick={async () => {
                  setSaving(true);
                  setError("");
                  try {
                    await api.saveAudienceTemplate(
                      audience,
                      columns,
                      template.version,
                    );
                    await query.refetch();
                    setDraft(undefined);
                    onSaved?.();
                  } catch (failure) {
                    setError((failure as Error).message);
                  } finally {
                    setSaving(false);
                  }
                }}
              >
                保存模板
              </button>
            </fieldset>
          )}
        </>
      )}
    </details>
  );
}
