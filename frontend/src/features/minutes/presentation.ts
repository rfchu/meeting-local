import type {
  Artifact,
  Meeting,
  MeetingMinutes,
  MinutesItem,
} from "../../types";

export const minutesSectionLabels = {
  overview: "会议概述",
  conclusions: "核心讨论",
  decisions: "决策",
  action_items: "待办",
  open_questions: "风险与待确认",
  summary: "会议总结",
} as const;

export function canonicalMinutes(document: MeetingMinutes): MeetingMinutes {
  return document.style === "ai" && (document.template_version || 1) < 2
    ? {
        ...document,
        overview: [],
        conclusions: document.overview,
        summary: document.conclusions,
      }
    : document;
}

// Count matching rows so duplicated items are compared without hiding removals.
function rowKey(item: MinutesItem) {
  return JSON.stringify([
    item.content,
    item.owner || "",
    item.due_date || "",
    item.status || "",
    [...(item.source_segment_ids || [])].sort(),
  ]);
}
function changeReasons(item: MinutesItem, other: MinutesItem[]) {
  const candidates = other
    .filter((row) => row.content === item.content)
    .map((row) => {
      const changes = [
        ["负责人", item.owner || "", row.owner || ""],
        ["期限", item.due_date || "", row.due_date || ""],
        ["状态", item.status || "", row.status || ""],
        [
          "原文引用",
          JSON.stringify([...(item.source_segment_ids || [])].sort()),
          JSON.stringify([...(row.source_segment_ids || [])].sort()),
        ],
      ];
      return changes.filter(([, a, b]) => a !== b).map(([label]) => label);
    })
    .sort((a, b) => a.length - b.length);
  return candidates.length
    ? candidates[0].length
      ? candidates[0].join("、") + "不同"
      : "重复条目数量不同"
    : "";
}
function markRows(items: MinutesItem[], other: MinutesItem[]) {
  const remaining = new Map<string, number>();
  for (const item of other)
    remaining.set(rowKey(item), (remaining.get(rowKey(item)) || 0) + 1);
  return items.map((item) => {
    const key = rowKey(item),
      count = remaining.get(key) || 0;
    if (count) remaining.set(key, count - 1);
    return {
      item,
      changed: count === 0,
      reason: count === 0 ? changeReasons(item, other) : "",
    };
  });
}

export function compareDocuments(left: MeetingMinutes, right: MeetingMinutes) {
  const originals = [canonicalMinutes(left), canonicalMinutes(right)];
  const metadata = (
    [
      ["title", "标题"],
      ["meeting_date", "日期"],
      ["meeting_form", "会议形式"],
      ["location", "地点"],
      ["attendees", "参会人"],
      ["recorder", "记录人"],
      ["purpose", "会议目的"],
    ] as const
  ).map(([key, label]) => ({
    label,
    left: originals[0][key] || "",
    right: originals[1][key] || "",
    changed: (originals[0][key] || "") !== (originals[1][key] || ""),
  }));
  const sections = Object.entries(minutesSectionLabels).map(([key, title]) => {
    const name = key as keyof typeof minutesSectionLabels;
    const a = originals[0][name] || [],
      b = originals[1][name] || [];
    return { key, title, left: markRows(a, b), right: markRows(b, a) };
  });
  return {
    metadata,
    sections,
    metadataChanges: metadata.filter((row) => row.changed).length,
    leftChanges: sections.reduce(
      (sum, section) => sum + section.left.filter((row) => row.changed).length,
      0,
    ),
    rightChanges: sections.reduce(
      (sum, section) => sum + section.right.filter((row) => row.changed).length,
      0,
    ),
  };
}

export function hasMinutesFollowup(config: string) {
  try {
    const value = JSON.parse(config);
    return (
      !!value &&
      typeof value.minutes_followup === "object" &&
      value.minutes_followup !== null
    );
  } catch {
    return false;
  }
}

export function pendingMinutesDrafts(meeting: Meeting) {
  const versions = meeting.artifacts.filter(
    (a) => a.kind === "meeting_minutes",
  ) as Artifact<MeetingMinutes>[];
  return versions.filter(
    (a) =>
      a.provenance.draft &&
      !a.stale &&
      a.revision === meeting.revision &&
      a.id !== meeting.active_minutes_id &&
      !versions.some(
        (child) =>
          !child.stale &&
          child.revision === meeting.revision &&
          child.provenance.parent_minutes_id === a.id,
      ),
  );
}

export function minutesVersionDescription(artifact: Artifact<MeetingMinutes>) {
  const raw = artifact.provenance.composition;
  const settings =
    raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  const method = artifact.provenance.edited
    ? "人工修订"
    : artifact.provenance.automatic
      ? "AI 自动成稿"
      : artifact.provenance.llm_polished
        ? "AI 润色"
        : settings.method === "analysis"
          ? "规则成稿"
          : settings.method === "current"
            ? "AI 派生"
            : artifact.payload.style === "ai"
              ? "AI 全文"
              : "规则成稿";
  const detail =
    settings.method === "analysis"
      ? ""
      : (
          { brief: "精简", standard: "标准", detailed: "详细" } as Record<
            string,
            string
          >
        )[String(settings.detail_level)] || "";
  const name = artifact.payload.template_snapshot?.name;
  return [method, typeof name === "string" ? name : "", detail]
    .filter(Boolean)
    .join(" · ");
}

export function minutesAdoptionBlockReason(
  meeting: Meeting,
  artifact: Artifact<MeetingMinutes>,
  transcriptDirty: boolean,
) {
  if (transcriptDirty) return "文字稿有未保存修改，请保存后重新核对纪要。";
  if (artifact.stale || artifact.revision !== meeting.revision)
    return "此版本来源已变化，请根据当前来源重新生成。";
  return undefined;
}
