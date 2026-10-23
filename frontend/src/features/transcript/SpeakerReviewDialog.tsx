import { useModelChoice } from "../../preferences/useModelChoice";
import { resolveModelChoice } from "../../preferences/modelChoices";
import { useEffect, useRef, useState } from "react";
import { api } from "../../api";
import Modal from "../../components/Modal";
import type {
  Artifact,
  LLMConfig,
  Meeting,
  SpeakerReviewResult,
} from "../../types";
import { clock } from "../../utils";

export default function SpeakerReviewDialog({
  meeting,
  onClose,
  onSave,
  onRefresh,
  toast,
}: {
  meeting: Meeting;
  onClose: () => void;
  onSave: () => Promise<void>;
  onRefresh: () => Promise<void>;
  toast: (text: string) => void;
}) {
  const [profiles, setProfiles] = useState<LLMConfig[]>([]),
    [profile, setProfile] = useModelChoice("speakers"),
    [review, setReview] = useState<Artifact<SpeakerReviewResult> | null>(
      () =>
        (meeting.artifacts.find(
          (a) => a.kind === "speaker_review" && a.revision === meeting.revision,
        ) as Artifact<SpeakerReviewResult>) || null,
    ),
    [busy, setBusy] = useState(false),
    [selected, setSelected] = useState<string[]>([]),
    [ignored, setIgnored] = useState<string[]>([]);
  const [snapshot, setSnapshot] = useState(meeting);
  const audio = useRef<HTMLAudioElement>(null);
  useEffect(() => {
    void api
      .configs()
      .then((configs) => {
        setProfiles(configs);
        setProfile(
          resolveModelChoice(
            profile,
            configs.map((p) => p.id),
            configs.find((p) => p.is_default)?.id,
          ),
        );
      })
      .catch((error) => toast(error.message));
  }, []);
  const defaultProfile = profiles.find((p) => p.is_default) || profiles[0];
  const listen = (time: number) => {
    if (!audio.current) return;
    audio.current.currentTime = time;
    void audio.current.play().catch((error) => toast(error.message));
  };
  const generate = async () => {
    setBusy(true);
    try {
      await onSave();
      const fresh = await api.meeting(meeting.id);
      setSnapshot(fresh);
      const result = await api.speakerReview(meeting.id, profile || undefined);
      const source = await api.revision(meeting.id, result.revision);
      setSnapshot({
        ...fresh,
        revision: result.revision,
        segments: source.segments || fresh.segments,
      });
      setReview(result);
      setSelected([]);
      setIgnored([]);
      await onRefresh();
    } catch (error) {
      toast((error as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const apply = async () => {
    if (!review) return;
    setBusy(true);
    try {
      await onSave();
      await api.applySpeakerReview(
        meeting.id,
        review.id,
        review.revision,
        selected,
      );
      await onRefresh();
      setReview(null);
      setSelected([]);
      toast("已保存为新版本；可通过版本历史撤销，会议分析与纪要需要更新");
    } catch (error) {
      toast((error as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      id="speakerReviewDialog"
      onClose={() => {
        if (!busy) onClose();
      }}
    >
      <div className="config-header">
        <h2>AI 校对发言人</h2>
        <button disabled={busy} onClick={onClose}>
          关闭
        </button>
      </div>
      <p>
        先回听代表性片段，勾选“已确认”作为人物样本。AI
        根据原文上下文提出建议，应用前请回听核对；不会新增姓名。
      </p>
      <div className="config-actions">
        <select
          aria-label="发言人校对模型"
          value={profile}
          onFocus={() =>
            void api
              .configs()
              .then((configs) => {
                setProfiles(configs);
                setProfile(
                  resolveModelChoice(
                    profile,
                    configs.map((p) => p.id),
                    configs.find((p) => p.is_default)?.id,
                  ),
                );
              })
              .catch((error) => toast(error.message))
          }
          onChange={(e) => setProfile(e.target.value)}
          disabled={busy}
        >
          <option value="">
            {defaultProfile
              ? `默认：${defaultProfile.name} · ${defaultProfile.provider_type === "cloud" ? "云端" : "本地"}`
              : "LM Studio（本地）"}
          </option>
          {profiles.map((p) => (
            <option value={p.id} key={p.id}>
              {p.name} · {p.provider_type === "cloud" ? "云端" : "本地"}
            </option>
          ))}
        </select>
        <button disabled={busy} onClick={() => void generate()}>
          {busy ? "处理中…" : "生成校对建议"}
        </button>
      </div>
      <p className="speaker-review-hint">
        使用云端配置时会将文字稿发送至该模型接口。当前范围：已有发言人之间的片段改签；“依据充分”不代表准确率。
      </p>
      <audio
        className="speaker-review-audio"
        ref={audio}
        src={`/api/meetings/${meeting.id}/audio`}
        controls
        preload="metadata"
        aria-label="发言人校对回听"
      />
      {review && (
        <>
          <div className="config-actions">
            <span>
              基于 v{review.revision} · {review.payload.suggestions.length}{" "}
              条建议
              {review.revision !== meeting.revision
                ? " · 文字稿已更新，请重新生成"
                : ""}
            </span>
            <button
              disabled={busy}
              onClick={() =>
                setSelected(
                  review.payload.suggestions
                    .filter(
                      (s) =>
                        !ignored.includes(s.segment_id) &&
                        s.strength === "supported",
                    )
                    .map((s) => s.segment_id),
                )
              }
            >
              选择依据充分项
            </button>
            <button
              className="primary"
              disabled={
                busy || !selected.length || review.revision !== meeting.revision
              }
              onClick={() => void apply()}
            >
              应用所选 {selected.length} 项
            </button>
          </div>
          {!review.payload.suggestions.length && (
            <p>没有发现有充分上下文依据的改签建议，可继续人工回听校对。</p>
          )}
          <div className="speaker-review-list">
            {review.payload.suggestions
              .filter((s) => !ignored.includes(s.segment_id))
              .map((s) => {
                const segment = snapshot.segments.find(
                  (x) => x.id === s.segment_id,
                );
                return (
                  <article key={s.segment_id}>
                    <label>
                      <input
                        type="checkbox"
                        disabled={busy}
                        checked={selected.includes(s.segment_id)}
                        onChange={(e) =>
                          setSelected((old) =>
                            e.target.checked
                              ? [...old, s.segment_id]
                              : old.filter((id) => id !== s.segment_id),
                          )
                        }
                      />
                      {segment ? clock(segment.start) : "—"} ·{" "}
                      {s.original_speaker} → <b>{s.proposed_speaker}</b>
                    </label>
                    <small>
                      {
                        {
                          supported: "依据充分",
                          review: "需要回听",
                          conflict: "存在冲突",
                        }[s.strength]
                      }
                    </small>
                    <p>{segment?.text}</p>
                    <p>依据：{s.reason}</p>
                    <div className="speaker-evidence">
                      {s.evidence_ids.map((id) => {
                        const source = snapshot.segments.find(
                          (x) => x.id === id,
                        );
                        return source ? (
                          <div key={id}>
                            <button onClick={() => listen(source.start)}>
                              {clock(source.start)} · {source.speaker}
                            </button>
                            <span>{source.text}</span>
                          </div>
                        ) : null;
                      })}
                    </div>
                    <button
                      disabled={!segment}
                      onClick={() =>
                        listen(Math.max(0, (segment?.start || 0) - 4))
                      }
                    >
                      回听前后文
                    </button>
                    <button
                      disabled={busy}
                      onClick={() => {
                        setIgnored((old) => [...old, s.segment_id]);
                        setSelected((old) =>
                          old.filter((id) => id !== s.segment_id),
                        );
                      }}
                    >
                      保持原标签
                    </button>
                  </article>
                );
              })}
          </div>
        </>
      )}
    </Modal>
  );
}
