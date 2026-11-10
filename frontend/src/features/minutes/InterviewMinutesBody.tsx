import type { InterviewResult, MeetingMinutes, Segment } from "../../types";
export const coverageLabels = {
  covered: "已找到相关回答",
  needs_examples: "需要补充例证",
  not_discussed: "本轮未找到相关回答",
  unclear: "原文待回听",
};
export function projectInterview(doc: MeetingMinutes): MeetingMinutes {
  if (!doc.interview) return doc;
  const data = doc.interview;
  const row = (
    x: {
      content: string;
      evidence: { segment_id: string; span_segment_ids?: string[] }[];
      owner?: string | null;
      due_date?: string | null;
    },
    content = x.content,
  ) => ({
    content,
    source_segment_ids: [...new Set(x.evidence.flatMap((e) => e.span_segment_ids?.length ? e.span_segment_ids : [e.segment_id]))],
    owner: x.owner || null,
    due_date: x.due_date || null,
    status: null,
  });
  const overview = data.background.map((x) => row(x));
  const labels = {
    candidate: "候选人（代称）",
    role: "应聘岗位",
    round: "面试轮次",
    interviewers: "计划面试官",
    focus: "本轮考察重点",
  };
  const context = doc.interview_context;
  if (context) {
    const text = Object.entries(labels)
      .filter(([key]) => context[key as keyof typeof context])
      .map(
        ([key, label]) => `${label}：${context[key as keyof typeof context]}`,
      )
      .join("；");
    if (text)
      overview.unshift(
        row({ content: "面试准备（人工填写）：" + text, evidence: [] }),
      );
  }
  const summary = data.summary.map((x) => row(x));
  if (
    doc.interviewer_evaluation?.trim() &&
    !doc.interview_evaluation_needs_review
  )
    summary.push(
      row({
        content: "面试官评价（人工填写）：" + doc.interviewer_evaluation.trim(),
        evidence: [],
      }),
    );
  return {
    ...doc,
    overview,
    conclusions: data.qa.map((x) =>
      row(
        { ...x, evidence: [...(x.question_evidence || []), ...x.evidence] },
        `问题：${x.question}\n回答摘要：${x.content}`,
      ),
    ),
    decisions: data.competencies.map((x) =>
      row(x, `${x.dimension}｜${coverageLabels[x.coverage]}：${x.content}`),
    ),
    open_questions: data.concerns
      .filter((_, i) => doc.interview_reviews?.[String(i)] === "kept")
      .map((x) =>
        row(
          x,
          `关注点：${x.content}\n依据说明：${x.reason}\n建议追问：${x.followup}`,
        ),
      ),
    action_items: data.followups.map((x) => row(x)),
    summary,
  };
}
export default function InterviewMinutesBody({
  document: doc,
  segments,
  translations,
  onChange,
  onSeek,
  onPreparation,
}: {
  document: MeetingMinutes;
  segments: Segment[];
  translations?: Record<string, string>;
  onChange: (d: MeetingMinutes) => void;
  onSeek: (t: number, track?: Segment["source_track"]) => void;
  onPreparation: () => void;
}) {
  const data = doc.interview!;
  const change = (next: MeetingMinutes) => onChange(projectInterview(next));
  const patch = (
    key: keyof InterviewResult,
    index: number,
    values: Record<string, unknown>,
  ) => {
    const reviews = { ...doc.interview_reviews };
    if (key === "concerns") delete reviews[String(index)];
    change({
      ...doc,
      interview_reviews: reviews,
      interview: {
        ...data,
        [key]: data[key].map((item, i) =>
          i === index ? { ...item, ...values } : item,
        ),
      },
    });
  };
  const headings: [keyof InterviewResult, string][] = [
    [
      "background",
      doc.interview_context?.speaker_roles ? "候选人经历与项目" : "经历与项目",
    ],
    ["qa", "核心问答记录"],
    ["competencies", "岗位能力证据"],
    ["concerns", "AI 关注与待追问"],
    ["followups", "明确约定的后续动作"],
    ["summary", "面试总结"],
  ];
  return (
    <div className="interview-body">
      <div className="interview-context-card">
        <strong>{doc.interview_context?.candidate || "候选人未注明"}</strong>
        <span>
          {doc.interview_context?.role || "岗位未设置"} ·{" "}
          {doc.interview_context?.round || "轮次未注明"}
        </span>
        <button onClick={onPreparation}>面试准备</button>
      </div>
      {doc.interview_context?.focus && (
        <p className="interview-hint">
          本轮计划考察：{doc.interview_context.focus}
        </p>
      )}
      <p className="interview-hint">
        {doc.interview_context?.speaker_roles
          ? "已按确认角色校验问题和回答的原文依据。"
          : "发言人角色未确认。可通过原文依据回听核对发言归属。"}
        AI 关注点经人工保留后进入导出。
      </p>
      {headings.map(([key, title]) => (
        <section className="minutes-section" key={key}>
          <h3>{title}</h3>
          {!data[key].length && (
            <p className="minutes-empty">
              {key === "concerns"
                ? "没有生成待核对关注点。"
                : "本轮未整理到有依据的内容。"}
            </p>
          )}
          {data[key].map((item, index) => (
            <article className="interview-item" key={index}>
              {"question" in item && (
                <label>
                  面试问题
                  <input
                    aria-label={`面试问题 ${index + 1}`}
                    value={item.question}
                    maxLength={1000}
                    onChange={(e) =>
                      patch(key, index, { question: e.target.value })
                    }
                  />
                </label>
              )}
              {"coverage" in item && (
                <div className="interview-coverage">
                  <strong>{item.dimension}</strong>
                  <span>{coverageLabels[item.coverage]}</span>
                </div>
              )}
              <label>
                {key === "qa"
                  ? "回答摘要"
                  : key === "concerns"
                    ? "AI 关注点（待人工核对）"
                    : "内容"}
                <textarea
                  aria-label={`${title} ${index + 1}`}
                  value={item.content}
                  rows={3}
                  maxLength={2000}
                  onChange={(e) =>
                    patch(key, index, { content: e.target.value })
                  }
                />
              </label>
              {"reason" in item && (
                <>
                  <label>
                    依据说明
                    <textarea
                      rows={2}
                      maxLength={1000}
                      value={item.reason}
                      onChange={(e) =>
                        patch(key, index, { reason: e.target.value })
                      }
                    />
                  </label>
                  <label>
                    建议追问
                    <textarea
                      rows={2}
                      maxLength={1000}
                      value={item.followup}
                      onChange={(e) =>
                        patch(key, index, { followup: e.target.value })
                      }
                    />
                  </label>
                  <label>
                    人工处理
                    <select
                      aria-label={`关注点 ${index + 1} 人工处理`}
                      value={
                        doc.interview_reviews?.[String(index)] || "pending"
                      }
                      onChange={(e) => {
                        const reviews = { ...doc.interview_reviews };
                        if (e.target.value === "pending")
                          delete reviews[String(index)];
                        else
                          reviews[String(index)] = e.target.value as
                            "kept" | "ignored";
                        change({ ...doc, interview_reviews: reviews });
                      }}
                    >
                      <option value="pending">待核对 · 不导出</option>
                      <option value="kept">保留 · 纳入导出</option>
                      <option value="ignored">忽略 · 不导出</option>
                    </select>
                  </label>
                </>
              )}
              {"owner" in item && (
                <div className="note-compose-options">
                  <label>
                    负责人
                    <input
                      value={item.owner || ""}
                      maxLength={200}
                      onChange={(e) =>
                        patch(key, index, { owner: e.target.value || null })
                      }
                    />
                  </label>
                  <label>
                    期限
                    <input
                      value={item.due_date || ""}
                      maxLength={200}
                      onChange={(e) =>
                        patch(key, index, { due_date: e.target.value || null })
                      }
                    />
                  </label>
                </div>
              )}
              {(item.evidence.length > 0 ||
                ("question_evidence" in item &&
                  !!item.question_evidence?.length)) && (
                <details className="interview-evidence">
                  <summary>原文依据</summary>
                  {[
                    ...("question_evidence" in item
                      ? (item.question_evidence || []).map((e) => ({
                          ...e,
                          label: "问题依据",
                        }))
                      : []),
                    ...item.evidence.map((e) => ({
                      ...e,
                      label:
                        "question" in item && item.question_evidence?.length
                          ? "回答依据"
                          : "原文依据",
                    })),
                  ].map((e, i) => {
                    const segment = segments.find((s) => s.id === e.segment_id);
                    const ids = e.span_segment_ids?.length ? e.span_segment_ids : [e.segment_id];
                    return (
                      <blockquote key={i}>
                        <small>
                          {e.label}
                          {segment?.speaker ? ` · ${segment.speaker}` : ""}
                        </small>
                        <p>{e.quote}</p>
                        {translations?.[e.segment_id] && (
                          <p>生成时的中文译文：{translations[e.segment_id]}</p>
                        )}
                        {ids.map((id, index) => {
                          const source = segments.find((s) => s.id === id);
                          return (
                            <button
                              key={id}
                              disabled={!source}
                              onClick={() => source && onSeek(source.start, source.source_track)}
                            >
                              {!source ? "原文依据待核对" : ids.length > 1 ? `回听片段 ${index + 1}` : "定位并回听原文"}
                            </button>
                          );
                        })}
                      </blockquote>
                    );
                  })}
                </details>
              )}
            </article>
          ))}
        </section>
      ))}
      <section className="minutes-section">
        <h3>面试官评价（人工填写）</h3>
        <textarea
          aria-label="面试官评价"
          rows={4}
          maxLength={1800}
          placeholder="填写基于岗位要求的专业判断，AI 不代填。"
          value={doc.interviewer_evaluation || ""}
          onChange={(e) =>
            change({ ...doc, interviewer_evaluation: e.target.value })
          }
        />
        {doc.interview_evaluation_needs_review && (
          <label className="analysis-notice">
            <input
              type="checkbox"
              checked={false}
              onChange={() =>
                change({ ...doc, interview_evaluation_needs_review: false })
              }
            />
            来源已变化，评价暂不导出；我已重新核对该评价
          </label>
        )}
      </section>
    </div>
  );
}
