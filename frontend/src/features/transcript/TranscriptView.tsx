import MeetingRecordingQuality from "../notes/MeetingRecordingQuality";
import TermDialog from "../terminology/TermDialog";
import { searchForms, type Term } from "../terminology/terms";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../api";
import Modal from "../../components/Modal";
import ActionMenu from "../../components/ActionMenu";
import type { LLMConfig, Meeting, Revision, Segment } from "../../types";
import { clock, revisionLabel } from "../../utils";
import LiveCorrectionReview from "../notes/LiveCorrectionReview";
import TranscriptList from "./TranscriptList";
import SpeakerReviewDialog from "./SpeakerReviewDialog";
import ChineseMeetingRecord from "../meeting/ChineseMeetingRecord";
import {
  IconBook,
  IconClock,
  IconEdit,
  IconFileText,
  IconGlobe,
  IconScissors,
  IconSearch,
  IconSparkles,
  IconUsers,
  IconWrench,
} from "../../styles/icons";

interface Props {
  meeting: Meeting;
  qualityReviewRequest?: number;
  qualityReviewView?: "pending" | "all" | "gaps";
  jumpSegmentId?: string;
  onChange: (segments: Segment[]) => void;
  activeSegment?: string;
  onSeek: (time: number, track?: Segment["source_track"]) => void;
  onSave: () => Promise<void>;
  onRefresh: () => Promise<void>;
  toast: (text: string) => void;
  transcriptDirty?: boolean;
  bilingualProfiles?: LLMConfig[];
  onContinueBilingualMinutes?: (profileId: string) => void;
}

function CompareDialog({
  meeting,
  onClose,
}: {
  meeting: Meeting;
  onClose: () => void;
}) {
  const model = meeting.revisions.filter((item) => item.source !== "manual");
  const [left, setLeft] = useState(
    model[1]?.revision ||
      meeting.revisions[1]?.revision ||
      meeting.revisions[0]?.revision,
  );
  const [right, setRight] = useState(
    model[0]?.revision || meeting.revisions[0]?.revision,
  );
  const [data, setData] = useState<Record<number, Revision>>({});
  const load = async (revision: number) => {
    if (!data[revision]) {
      const result = await api.revision(meeting.id, revision);
      setData((old) => ({ ...old, [revision]: result }));
    }
  };
  const options = meeting.revisions.map((item) => (
    <option key={item.revision} value={item.revision}>
      v{item.revision} · {revisionLabel(item.source)}
    </option>
  ));
  return (
    <Modal id="compareDialog" onClose={onClose}>
      <div className="compare-header">
        <div>
          <h2>对比转写版本</h2>
          <p>查看两个版本的识别结果，不会修改当前校对稿。</p>
        </div>
        <button className="text-btn" onClick={onClose}>
          关闭
        </button>
      </div>
      <div className="compare-grid">
        {(
          [
            ["left", left, setLeft],
            ["right", right, setRight],
          ] as const
        ).map(([side, revision, setter]) => (
          <section key={side}>
            <select
              value={revision}
              onChange={(e) => {
                const value = Number(e.target.value);
                setter(value);
                void load(value);
              }}
            >
              {options}
            </select>
            <div className="compare-content">
              {data[revision]?.segments ? (
                <>
                  <p className="compare-meta">
                    v{revision} · {revisionLabel(data[revision].source)}
                  </p>
                  {data[revision].segments!.map((segment) => (
                    <div className="compare-segment" key={segment.id}>
                      <small>
                        {clock(segment.start)} · {segment.speaker}
                      </small>
                      <p>{segment.text}</p>
                    </div>
                  ))}
                </>
              ) : (
                <button onClick={() => void load(revision)}>加载此版本</button>
              )}
            </div>
          </section>
        ))}
      </div>
    </Modal>
  );
}

export default function TranscriptView({
  qualityReviewRequest = 0,
  qualityReviewView = "pending",
  meeting,
  jumpSegmentId,
  onChange,
  onSeek,
  activeSegment,
  onSave,
  onRefresh,
  toast,
  transcriptDirty = false,
  bilingualProfiles = [],
  onContinueBilingualMinutes,
}: Props) {
  const [viewMode, setViewMode] = useState<"original" | "bilingual">(
    "original",
  );
  const [replaceBarOpen, setReplaceBarOpen] = useState(false);
  const [termsOpen, setTermsOpen] = useState(false);
  const [terms, setTerms] = useState<Term[]>([]);
  const loadTerms = useCallback(
    () =>
      api
        .terms(meeting.id)
        .then((data) => setTerms([...data.terms, ...data.library])),
    [meeting.id],
  );
  useEffect(() => {
    setTerms([]);
    let live = true;
    void api
      .terms(meeting.id)
      .then((data) => {
        if (live) setTerms([...data.terms, ...data.library]);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [meeting.id, meeting.revision]);
  const [liveReviewOpen, setLiveReviewOpen] = useState(false);
  const [hasLiveReview, setHasLiveReview] = useState(false);
  useEffect(() => {
    let valid = true;
    setHasLiveReview(false);
    api
      .liveCorrections(meeting.id)
      .then((rows) => {
        if (valid) setHasLiveReview(rows.length > 0);
      })
      .catch(() => {});
    return () => {
      valid = false;
    };
  }, [meeting.id, meeting.revision]);
  const [search, setSearch] = useState(""),
    [speakerOpen, setSpeakerOpen] = useState(false),
    [compareOpen, setCompareOpen] = useState(false),
    [replaceOpen, setReplaceOpen] = useState(false),
    [fullDom, setFullDom] = useState(false);
  useEffect(() => {
    setFullDom(false);
  }, [meeting.id]);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [speakerFrom, setSpeakerFrom] = useState(""),
    [speakerTo, setSpeakerTo] = useState(""),
    [find, setFind] = useState(""),
    [replacement, setReplacement] = useState("");
  const counts = useMemo(
    () =>
      meeting.segments.reduce(
        (map, item) => map.set(item.speaker, (map.get(item.speaker) || 0) + 1),
        new Map<string, number>(),
      ),
    [meeting.segments],
  );
  const shown = useMemo(() => {
    const q = search.trim().toLowerCase();
    const forms = searchForms(search, terms);
    return meeting.segments.filter(
      (item) =>
        !q ||
        item.id === editingId ||
        forms.some((form) => item.text.toLowerCase().includes(form)) ||
        item.speaker.toLowerCase().includes(q),
    );
  }, [meeting.segments, search, terms, editingId]);
  const latest = useRef({ segments: meeting.segments, onChange, onSeek });
  latest.current = { segments: meeting.segments, onChange, onSeek };
  const update = useCallback(
    (segment: Segment) =>
      latest.current.onChange(
        latest.current.segments.map((item) =>
          item.id === segment.id ? segment : item,
        ),
      ),
    [],
  );
  const seek = useCallback(
    (time: number, track?: Segment["source_track"]) =>
      latest.current.onSeek(time, track),
    [],
  );
  useEffect(() => {
    if (jumpSegmentId) setSearch("");
  }, [jumpSegmentId]);
  const mergeShort = () => {
    const merged: Segment[] = [];
    let count = 0;
    for (const source of meeting.segments) {
      const item = structuredClone(source),
        previous = merged.at(-1),
        gap = previous ? item.start - previous.end : Infinity,
        short =
          previous &&
          (previous.text.trim().length <= 18 || item.text.trim().length <= 18);
      if (
        previous &&
        !previous.alignment_warning &&
        !item.alignment_warning &&
        !previous.speaker_locked &&
        !item.speaker_locked &&
        previous.speaker === item.speaker &&
        (previous.source_track ?? null) === (item.source_track ?? null) &&
        gap <= 1.5 &&
        short &&
        previous.text.length + item.text.length <= 140
      ) {
        previous.end = Math.max(previous.end, item.end);
        previous.text +=
          (/[，。！？；：,.!?;:]$/.test(previous.text.trim()) ? "" : "，") +
          item.text.trim();
        previous.boundary_review ||= item.boundary_review;
        if (previous.continuation_id === item.continuation_id) {
          previous.continues_next = item.continues_next;
          previous.boundary_reason = item.boundary_reason;
        } else {
          previous.continuation_id = undefined;
          previous.continues_previous = false;
          previous.continues_next = false;
        }
        count++;
      } else merged.push(item);
    }
    if (count) {
      onChange(merged);
      toast(`已合并 ${count} 个连续短句`);
    } else toast("没有找到可合并的连续短句");
  };
  const restore = async (revision: number) => {
    await onSave();
    const result = await api.revision(meeting.id, revision);
    if (confirm(`将 v${revision} 恢复为新的校对版本？当前版本仍会保留。`)) {
      onChange(result.segments || []);
      toast("历史文稿已恢复，正在保存为新版本");
    }
  };
  return (
    <>
      <MeetingRecordingQuality
        key={`${meeting.id}:${qualityReviewRequest}`}
        initialView={qualityReviewRequest > 0 ? qualityReviewView : "pending"}
        initiallyOpen={qualityReviewRequest > 0}
        id={meeting.id}
        onSeek={onSeek}
      />
      {termsOpen && (
        <TermDialog
          scope={meeting.id}
          canScan
          onClose={() => {
            setTermsOpen(false);
            void loadTerms().catch((e) => toast(e.message));
            void onRefresh();
          }}
          onLocate={(id) => {
            const item = meeting.segments.find((s) => s.id === id);
            if (item) onSeek(item.start, item.source_track);
          }}
        />
      )}
      <div className="transcript-pro-toolbar">
        <div className="toolbar-main-row">
          <div className="search-capsule">
            <span className="search-icon">
              <IconSearch size={14} />
            </span>
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="搜索文字稿内容或发言人…"
              aria-label="搜索文字稿"
            />
            {search.trim() ? (
              <span className="search-badge">{shown.length} 条匹配</span>
            ) : null}
            {search ? (
              <button
                type="button"
                className="btn-clear-search"
                title="清空搜索"
                onClick={() => setSearch("")}
              >
                ✕
              </button>
            ) : null}
            <button
              type="button"
              className={`btn-toggle-replace ${replaceBarOpen ? "active" : ""}`}
              title="展开或收起快速文本替换"
              onClick={() => {
                const nextState = !replaceBarOpen;
                setReplaceBarOpen(nextState);
                if (nextState && !find && search) setFind(search);
              }}
            >
              ⇄ 替换
            </button>
          </div>

          <div
            className="transcript-view-switcher"
            role="radiogroup"
            aria-label="排版视图模式"
          >
            <button
              type="button"
              className={`view-pill ${viewMode === "original" ? "active" : ""}`}
              onClick={() => setViewMode("original")}
            >
              <IconFileText size={14} /> <span>纯文本校对</span>
            </button>
            <button
              type="button"
              className={`view-pill ${viewMode === "bilingual" ? "active" : ""}`}
              onClick={() => setViewMode("bilingual")}
            >
              <IconGlobe size={14} /> <span>双语对照</span>
            </button>
          </div>

          <div className="toolbar-actions">
            <ActionMenu
              icon={<IconWrench size={14} />}
              label="批量校对工具 ▾"
              actions={[
                {
                  icon: <IconUsers size={14} />,
                  label: `批量管理发言人 (${counts.size} 人)…`,
                  run: () => {
                    const first = counts.keys().next().value || "";
                    if (!first) return toast("请先完成转写");
                    setSpeakerFrom(first);
                    setSpeakerTo(first);
                    setSpeakerOpen(true);
                  },
                },
                {
                  icon: <IconBook size={14} />,
                  label: "专业术语与热词库…",
                  run: () => {
                    void onSave()
                      .then(() => setTermsOpen(true))
                      .catch((e) => toast(e.message));
                  },
                },
                {
                  icon: <IconScissors size={14} />,
                  label: "智能合并连续短句",
                  run: mergeShort,
                },
                {
                  icon: <IconSparkles size={14} />,
                  label: "AI 校对发言人…",
                  disabled: !meeting.segments.length,
                  run: () => setReviewOpen(true),
                },
                {
                  icon: <IconClock size={14} />,
                  label: "对比识别版本…",
                  disabled: meeting.revisions.length < 2,
                  run: () => setCompareOpen(true),
                },
                ...(hasLiveReview
                  ? [
                      {
                        icon: <IconEdit size={14} />,
                        label: "实时纠错记录…",
                        run: () => setLiveReviewOpen(true),
                      },
                    ]
                  : []),
              ]}
            />
            <ActionMenu
              key={`rev-${meeting.id}-${meeting.revision}-${meeting.revisions.length}`}
              icon={<IconClock size={14} />}
              label={`版本历史 (v${meeting.revision}) ▾`}
              accessibleLabel="文稿历史版本"
              actions={
                meeting.revisions.length
                  ? meeting.revisions.map((item) => ({
                      label:
                        item.revision === meeting.revision
                          ? `v${item.revision} · ${revisionLabel(item.source)}（当前）`
                          : `v${item.revision} · ${revisionLabel(item.source)}`,
                      disabled: item.revision === meeting.revision,
                      run: () => void restore(item.revision),
                    }))
                  : [
                      {
                        label: "暂无历史版本",
                        disabled: true,
                        run: () => {},
                      },
                    ]
              }
            />
          </div>
        </div>

        {replaceBarOpen && (
          <div className="inline-replace-panel">
            <div className="replace-fields">
              <label>
                <span className="label-text">查找：</span>
                <input
                  value={find}
                  placeholder="输入要查找的文本…"
                  onChange={(e) => setFind(e.target.value)}
                />
              </label>
              <label>
                <span className="label-text">替换为：</span>
                <input
                  value={replacement}
                  placeholder="输入替换后的文本…"
                  onChange={(e) => setReplacement(e.target.value)}
                />
              </label>
            </div>
            <div className="replace-actions">
              <button
                type="button"
                className="primary btn-sm"
                onClick={() => {
                  const target = find.trim();
                  if (!target) return toast("请输入要查找的内容");
                  const count = meeting.segments.reduce(
                    (sum, item) => sum + item.text.split(target).length - 1,
                    0,
                  );
                  if (!count) return toast(`未找到匹配“${target}”的内容`);
                  if (
                    !confirm(
                      `确定将文字稿中全部 ${count} 处“${target}”替换为“${replacement}”？`,
                    )
                  )
                    return;
                  onChange(
                    meeting.segments.map((item) =>
                      item.text.includes(target)
                        ? {
                            ...item,
                            text: item.text.replaceAll(target, replacement),
                          }
                        : item,
                    ),
                  );
                  toast(`已完成 ${count} 处文本替换`);
                  setReplaceBarOpen(false);
                }}
                disabled={!find.trim()}
              >
                全部替换
              </button>
              <button
                type="button"
                className="text-btn btn-sm"
                onClick={() => setReplaceBarOpen(false)}
              >
                收起
              </button>
            </div>
          </div>
        )}
      </div>

      <div
        className={`transcript-workspace-scroll ${viewMode === "bilingual" ? "bilingual-mode" : ""}`}
      >
        {viewMode === "original" ? (
          <div className="transcript-editor-container">
            <div className="transcript-status-bar">
              <span className="count-info">
                共 {meeting.segments.length} 个片段
                {search.trim() ? ` · 检索到 ${shown.length} 个匹配片段` : ""}
                {meeting.segments.length > 300 ? " · 已启用大文稿流畅渲染" : ""}
              </span>
              <div className="transcript-status-tools">
                {meeting.segments.length > 300 && (
                  <label className="virtual-mode-toggle">
                    <input
                      type="checkbox"
                      checked={fullDom}
                      onChange={(e) => setFullDom(e.target.checked)}
                    />
                    <span>展开全部 DOM (⌘F)</span>
                  </label>
                )}
                <div className="keyboard-shortcuts-hint">
                  <span className="hint-title">快捷操作：</span>
                  <kbd>Option+R</kbd> 重听本句
                  <span className="dot">·</span>
                  <kbd>Tab</kbd> 切至下一段
                </div>
              </div>
            </div>
            <TranscriptList
              activeSegment={activeSegment}
              key={meeting.id}
              segments={shown}
              onEditingChange={setEditingId}
              total={meeting.segments.length}
              fullDom={fullDom}
              hideVirtualBar
              onUpdate={update}
              onSeek={seek}
              jumpSegmentId={jumpSegmentId}
            />
            {meeting.segments.length > 0 && shown.length === 0 && (
              <div className="empty">没有匹配的文字</div>
            )}
            {!meeting.segments.length && (
              <div className="empty">
                <div>≋</div>
                <h3>录音已就位</h3>
                <p>点击“开始转写”，文字稿将在这里出现。</p>
              </div>
            )}
          </div>
        ) : (
          <div className="transcript-bilingual-container">
            <ChineseMeetingRecord
              embedded
              activeSegment={activeSegment}
              key={`${meeting.id}-${meeting.revision}`}
              id={meeting.id}
              revision={meeting.revision}
              dirty={transcriptDirty ?? false}
              profiles={bilingualProfiles ?? []}
              onSeek={seek}
              onContinue={onContinueBilingualMinutes}
            />
          </div>
        )}
      </div>
      {liveReviewOpen && (
        <LiveCorrectionReview
          recordingId={meeting.id}
          onClose={() => setLiveReviewOpen(false)}
        />
      )}
      {speakerOpen && (
        <Modal id="speakerDialog" onClose={() => setSpeakerOpen(false)}>
          <div className="config-header">
            <div>
              <h2>管理发言人</h2>
              <p>
                共 {counts.size} 位发言人、{meeting.segments.length}{" "}
                个片段。选择已有名称作为新名称即可合并发言人。
              </p>
            </div>
            <button className="text-btn" onClick={() => setSpeakerOpen(false)}>
              关闭
            </button>
          </div>
          <form
            id="speakerForm"
            onSubmit={(e) => {
              e.preventDefault();
              const value = speakerTo.trim();
              if (!value) return;
              const changed = meeting.segments.filter(
                (item) => item.speaker === speakerFrom,
              ).length;
              onChange(
                meeting.segments.map((item) =>
                  item.speaker === speakerFrom
                    ? { ...item, speaker: value }
                    : item,
                ),
              );
              setSpeakerOpen(false);
              toast(`已更新 ${changed} 个片段`);
            }}
          >
            <label>
              选择发言人
              <select
                value={speakerFrom}
                onChange={(e) => {
                  setSpeakerFrom(e.target.value);
                  setSpeakerTo(e.target.value);
                }}
              >
                {[...counts].map(([name, count]) => (
                  <option key={name} value={name}>
                    {name} · {count} 段
                  </option>
                ))}
              </select>
            </label>
            <label>
              重命名或合并到
              <input
                required
                maxLength={100}
                value={speakerTo}
                onChange={(e) => setSpeakerTo(e.target.value)}
              />
            </label>
            <div className="config-actions">
              <button type="button" onClick={mergeShort}>
                合并连续短句
              </button>
              <button className="primary">应用到全部片段</button>
            </div>
          </form>
        </Modal>
      )}
      {reviewOpen && (
        <SpeakerReviewDialog
          meeting={meeting}
          onClose={() => setReviewOpen(false)}
          onSave={onSave}
          onRefresh={onRefresh}
          toast={toast}
        />
      )}
      {replaceOpen && (
        <Modal className="small-dialog" onClose={() => setReplaceOpen(false)}>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (!find) return;
              const count = meeting.segments.reduce(
                (sum, item) => sum + item.text.split(find).length - 1,
                0,
              );
              if (!count) return toast("没有找到匹配文字");
              onChange(
                meeting.segments.map((item) => ({
                  ...item,
                  text: item.text.split(find).join(replacement),
                })),
              );
              setSearch("");
              setReplaceOpen(false);
              toast(`已替换 ${count} 处文字`);
            }}
          >
            <h2>查找并替换</h2>
            <label>
              查找文字
              <input
                autoFocus
                value={find}
                onChange={(e) => setFind(e.target.value)}
              />
            </label>
            <label>
              替换为
              <input
                value={replacement}
                onChange={(e) => setReplacement(e.target.value)}
              />
            </label>
            <div className="config-actions">
              <button type="button" onClick={() => setReplaceOpen(false)}>
                取消
              </button>
              <button className="primary">全部替换</button>
            </div>
          </form>
        </Modal>
      )}
      {compareOpen && (
        <CompareDialog
          meeting={meeting}
          onClose={() => setCompareOpen(false)}
        />
      )}
    </>
  );
}
