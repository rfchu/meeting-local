"""Real-time speculative intent detector for meeting decisions and action items.

Operates as a streaming observer on incremental ASR transcript segments.
Detects high-confidence consensus decisions and promised action items with zero latency.
"""
from dataclasses import asdict, dataclass, field
import hashlib
import re
from typing import Any, Dict, List, Optional, Set


# Negative patterns: questions, confirmations, greetings, or short acknowledgments
QUESTION_OR_CHITCHAT_REGEX = re.compile(
    r"[?？]|(?:是否|能不能|行不行|怎么样|好不好|定了吗|确定吗|要不要|行吗|如何|怎么|为什么|"
    r"听得(?:见|到|清楚)|喂喂|哈哈|收到|明白|好的|对对|确实|不错|真不错)"
)

# Words that should never be recognized as an owner/assignee
NON_OWNER_WORDS = {
    "今天", "明天", "后天", "昨天", "现在", "目前", "刚刚", "开会", "讨论", "经过",
    "这个", "那个", "可能", "如果", "为了", "因为", "所以", "大家", "团队", "已经",
    "然后", "接着", "由于", "大家", "之后", "之前", "最后", "总体", "其实"
}

# Decision trigger patterns
DECISION_PATTERNS = [
    re.compile(
        r"(?:我们|大家|团队)?(?:一致)?(?:确定|决定|定下来|敲定|按这个定|采用|选定|方案定为|结论是)"
        r"(?:采用|使用|以)?[:：\s]*(?P<content>.+)"
    ),
    re.compile(r"(?P<content>.+?)(?:定为|作为)(?:最终|正式)?(?:方案|标准|架构|策略|规范)"),
    re.compile(r"确认(?:以|使用|采用)?(?P<content>.+?)(?:作为|定为)"),
    re.compile(
        r"(?:数据库|技术方案|架构|接口|规范|排期|上线时间|发布时间)(?:确定|定在|决定)(?P<content>.+)"
    ),
]

# Time boundary patterns
TIME_PATTERNS = [
    re.compile(
        r"(?P<time>(?:本周[一二三四五六日天]|周[一二三四五六日天]|明天(?:上午|下午|晚上)?|"
        r"后天|下周[一二三四五六日天]?|月底|年底|今天下班前)(?:前|之前)?)"
    ),
    re.compile(r"(?P<time>尽快|这两天|稍后)"),
]

# Regexes for parsing action items / todos
TODO_PATTERN_DISPATCH = re.compile(
    r"(?P<owner>我|你|您|[\u4e00-\u9fa5]{2,3})(?:负责|来|去)(?P<task>[^，。！？；]+)"
)

TODO_PATTERN_BA = re.compile(
    r"(?P<owner>我|你|您|[\u4e00-\u9fa5]{2,3}).*?把(?P<task>[^，。！？；]+?)"
    r"(?P<verb>发给|发送|发|提交|输出|同步|提供)(?P<target>[^，。！？；]*)"
)

TODO_PATTERN_PASSIVE = re.compile(
    r"(?P<task>[^，。！？；]+?)(?:由|交给)(?P<owner>我|你|您|[\u4e00-\u9fa5]{2,3})(?:负责|来做|跟进)"
)

TODO_PATTERN_COMPLETE = re.compile(
    r"(?P<owner>我|你|您|[\u4e00-\u9fa5]{2,3})(?:在|于)?(?P<time>本周[一二三四五六日天]|周[一二三四五六日天]|明天|下周)?"
    r"(?:前|之前)?完成(?P<task>[^，。！？；]+)"
)


@dataclass
class SpeculativeCandidate:
    candidate_id: str
    kind: str  # "decision" | "todo"
    title: str
    content: str
    owner: str = ""
    due_date: str = ""
    confidence: float = 0.90
    speaker: str = ""
    timestamp: float = 0.0
    anchor_segments: List[Any] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def generate_candidate_id(kind: str, content: str) -> str:
    """Generate a deterministic 16-char hex ID for candidate deduplication."""
    clean = re.sub(r"[\s，。！？、：；]", "", content).strip().lower()
    return hashlib.sha256(f"{kind}:{clean}".encode("utf-8")).hexdigest()[:16]


def clean_owner(owner: str) -> str:
    owner = owner.strip()
    if owner.startswith(("我", "你", "您")):
        return owner[0]
    for time_pat in TIME_PATTERNS:
        owner = time_pat.sub("", owner).strip()
    for suffix in ("负责", "来做", "跟进", "去"):
        if owner.endswith(suffix) and len(owner) > len(suffix):
            owner = owner[: -len(suffix)]
    return owner


class SpeculativeDetector:
    """Zero-latency streaming semantic detector for meeting consensus and tasks."""

    def __init__(self, min_confidence: float = 0.85):
        self.min_confidence = min_confidence

    def _extract_time(self, text: str) -> str:
        for time_pat in TIME_PATTERNS:
            match = time_pat.search(text)
            if match:
                return match.group("time").strip()
        return ""

    def detect_text(
        self,
        text: str,
        speaker: str = "",
        timestamp: float = 0.0,
        segment_id: Optional[Any] = None,
    ) -> List[SpeculativeCandidate]:
        clean_text = text.strip()
        # An entire ASR segment is only a candidate when its wording is affirmative.
        if re.search(r"没有|尚未|还没|并未|未决定|不(?:再|会|能|由|负责|采用|使用|决定)|不能|如果|假如|要是|可能|考虑|建议|暂定|拟定", clean_text):
            return []
        if len(clean_text) < 6:
            return []

        # Question / chitchat negative filter
        if any(q in clean_text for q in ("？", "?", "吗", "呢", "好不好", "行不行", "听得到")):
            return []

        candidates: List[SpeculativeCandidate] = []
        anchor = [segment_id] if segment_id is not None else []

        # 1. Decision detection
        for pat in DECISION_PATTERNS:
            match = pat.search(clean_text)
            if match:
                decision_content = match.group("content").strip(" ，。；")
                if (
                    len(decision_content) >= 3
                    and not QUESTION_OR_CHITCHAT_REGEX.search(decision_content)
                ):
                    cid = generate_candidate_id("decision", decision_content)
                    title = f"确定: {decision_content[:24]}"
                    candidates.append(
                        SpeculativeCandidate(
                            candidate_id=cid,
                            kind="decision",
                            title=title,
                            content=clean_text,
                            speaker=speaker,
                            confidence=0.92,
                            timestamp=timestamp,
                            anchor_segments=anchor,
                        )
                    )
                    break

        # 2. Todo / Action item detection
        detected_due_date = self._extract_time(clean_text)

        # Pattern 1: Owner + 负责/来/去 + Task
        match = TODO_PATTERN_DISPATCH.search(clean_text)
        if match:
            raw_owner = match.group("owner")
            owner = clean_owner(raw_owner)
            task = match.group("task").strip(" ，。；")
            # Strip trailing time/completion words from task
            task = re.sub(r"[，,]?(?:本周|周|明天|下周|尽快).*?(?:前|之前)?(?:完成|交付)?$", "", task).strip()

            if owner not in NON_OWNER_WORDS and len(task) >= 2:
                title_owner = f"{owner}负责" if owner != "我" else "我负责"
                time_suffix = f" ({detected_due_date})" if detected_due_date else ""
                title = f"{title_owner}: {task[:20]}{time_suffix}"
                cid = generate_candidate_id("todo", f"{owner}:{task}")
                candidates.append(
                    SpeculativeCandidate(
                        candidate_id=cid,
                        kind="todo",
                        title=title,
                        content=clean_text,
                        owner=owner,
                        due_date=detected_due_date,
                        speaker=speaker,
                        confidence=0.91 if detected_due_date else 0.86,
                        timestamp=timestamp,
                        anchor_segments=anchor,
                    )
                )
                return candidates

        # Pattern 2: Owner + 把 + Task + Verb
        match = TODO_PATTERN_BA.search(clean_text)
        if match:
            raw_owner = match.group("owner")
            owner = clean_owner(raw_owner)
            task = match.group("task").strip(" ，。；")
            if owner not in NON_OWNER_WORDS and len(task) >= 2:
                title_owner = f"{owner}负责" if owner != "我" else "我负责"
                time_suffix = f" ({detected_due_date})" if detected_due_date else ""
                title = f"{title_owner}: {task[:20]}{time_suffix}"
                cid = generate_candidate_id("todo", f"{owner}:{task}")
                candidates.append(
                    SpeculativeCandidate(
                        candidate_id=cid,
                        kind="todo",
                        title=title,
                        content=clean_text,
                        owner=owner,
                        due_date=detected_due_date,
                        speaker=speaker,
                        confidence=0.90,
                        timestamp=timestamp,
                        anchor_segments=anchor,
                    )
                )
                return candidates

        # Pattern 3: Passive Task 由/交给 Owner
        match = TODO_PATTERN_PASSIVE.search(clean_text)
        if match:
            raw_owner = match.group("owner")
            owner = clean_owner(raw_owner)
            task = match.group("task").strip(" ，。；")
            if owner not in NON_OWNER_WORDS and len(task) >= 2:
                title_owner = f"{owner}负责" if owner != "我" else "我负责"
                time_suffix = f" ({detected_due_date})" if detected_due_date else ""
                title = f"{title_owner}: {task[:20]}{time_suffix}"
                cid = generate_candidate_id("todo", f"{owner}:{task}")
                candidates.append(
                    SpeculativeCandidate(
                        candidate_id=cid,
                        kind="todo",
                        title=title,
                        content=clean_text,
                        owner=owner,
                        due_date=detected_due_date,
                        speaker=speaker,
                        confidence=0.88,
                        timestamp=timestamp,
                        anchor_segments=anchor,
                    )
                )
                return candidates

        # Pattern 4: Owner + 完成 + Task
        match = TODO_PATTERN_COMPLETE.search(clean_text)
        if match:
            raw_owner = match.group("owner")
            owner = clean_owner(raw_owner)
            task = match.group("task").strip(" ，。；")
            time_val = match.groupdict().get("time") or detected_due_date
            if owner not in NON_OWNER_WORDS and len(task) >= 2:
                title_owner = f"{owner}负责" if owner != "我" else "我负责"
                time_suffix = f" ({time_val})" if time_val else ""
                title = f"{title_owner}: {task[:20]}{time_suffix}"
                cid = generate_candidate_id("todo", f"{owner}:{task}")
                candidates.append(
                    SpeculativeCandidate(
                        candidate_id=cid,
                        kind="todo",
                        title=title,
                        content=clean_text,
                        owner=owner,
                        due_date=time_val,
                        speaker=speaker,
                        confidence=0.87,
                        timestamp=timestamp,
                        anchor_segments=anchor,
                    )
                )
                return candidates

        return candidates


def detect_candidates_from_segments(
    segments: List[Dict[str, Any]],
    seen_ids: Optional[Set[str]] = None,
    detector: Optional[SpeculativeDetector] = None,
) -> List[SpeculativeCandidate]:
    """Inspects segments in the current window and filters out seen candidate IDs."""
    if detector is None:
        detector = SpeculativeDetector()
    if seen_ids is None:
        seen_ids = set()

    found: List[SpeculativeCandidate] = []
    emitted_ids = set(seen_ids)

    for seg in segments:
        text = seg.get("text", "")
        speaker = seg.get("speaker", "")
        start_time = float(seg.get("start", 0.0))
        seg_id = seg.get("id")

        detected = detector.detect_text(
            text=text,
            speaker=speaker,
            timestamp=start_time,
            segment_id=seg_id,
        )

        for cand in detected:
            if cand.candidate_id not in emitted_ids:
                emitted_ids.add(cand.candidate_id)
                found.append(cand)

    return found
