"""On-device shorthand note synthesis and temporal context alignment."""
import asyncio
import json
import logging
from typing import Any, Callable, Coroutine, Dict, List, Optional

logger = logging.getLogger(__name__)

SYNTHESIS_SCHEMA = {
    "type": "object",
    "required": ["summary", "details", "speakers", "anchor_start", "anchor_end", "evidence_quote"],
    "additionalProperties": False,
    "properties": {
        "summary": {
            "type": "string",
            "description": "One concise phrase summarizing the core takeaway",
        },
        "details": {
            "type": "string",
            "description": "Objective details and factual context derived from transcript",
        },
        "speakers": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Speakers who stated or agreed on this point",
        },
        "anchor_start": {
            "type": "number",
            "description": "Earliest start timestamp of relevant speech",
        },
        "anchor_end": {
            "type": "number",
            "description": "Latest end timestamp of relevant speech",
        },
        "evidence_quote": {
            "type": "string",
            "description": "Exact excerpt from the transcript supporting this point",
        },
    },
}

SYSTEM_INSTRUCTION = """/no_think
你是一个端侧实时会议智能副驾驶助手（类似 Google Foresight）。
你的任务是将用户在会议进行中快速记录的碎片化关键词（速记），结合会议中对应时间区间的语音转写上下文，扩写为一条客观、清晰、结构化的会议要点。

规则：
1. 严禁捏造未经讨论的事实、人名或截止时间。
2. 保留用户的意图，如果上下文明确支持，提取出具体的结论、决策或行动项。
3. 如果上下文提及较少，以用户原意为主，不要过度脑补。
4. 引用必须逐字取自同一条已确认片段。标注 provisional 的片段仅供理解，不可引用。
5. 返回格式必须是严格的 JSON 对象，包含 summary, details, speakers, anchor_start, anchor_end, evidence_quote。
"""


def extract_context_window(
    segments: List[Dict[str, Any]],
    captured_at: float,
    before: float = 30.0,
    after: float = 10.0,
) -> List[Dict[str, Any]]:
    """Extract transcript segments within [captured_at - before, captured_at + after]."""
    if not segments:
        return []

    start_bound = max(0.0, captured_at - before)
    end_bound = captured_at + after

    candidates = [
        s for s in segments
        if s.get("end", 0) >= start_bound
        and s.get("start", 0) <= end_bound
        and str(s.get("text", "")).strip()
    ]

    return sorted(candidates, key=lambda s: s.get("start", 0))


class ShorthandSynthesizer:
    """Synthesizes minimal shorthand user notes into context-anchored meeting notes."""

    def __init__(
        self,
        store: Optional[Any] = None,
        llm_provider: Optional[Any] = None,
        llm_call: Optional[Callable[[List[Dict[str, str]], Dict[str, Any]], Coroutine[Any, Any, Dict[str, Any]]]] = None,
    ):
        self.store = store
        self.provider = llm_provider
        self._custom_call = llm_call

    async def _execute_llm(
        self, messages: List[Dict[str, str]], schema: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Dispatch call to custom caller or provider."""
        if self._custom_call:
            return await self._custom_call(messages, schema)

        if not self.provider:
            raise RuntimeError("No LLM provider configured for shorthand synthesis")

        # Provider-based execution via loopback chat completions
        import urllib.request
        from scripts.lmstudio_analyze import NoRedirect, extract_json, native_minutes_requester
        payload = {
            "model": self.provider.resolve_model(),
            "temperature": 0.1,
            "max_tokens": 512,
            "stream": False,
            "messages": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "shorthand_synthesis",
                    "strict": True,
                    "schema": schema,
                },
            },
        }

        if "qwen" in payload["model"].lower():
            payload["chat_template_kwargs"] = {"enable_thinking": False}

        url = f"{self.provider.base_url.rstrip('/')}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if getattr(self.provider, "token", None):
            headers["Authorization"] = f"Bearer {self.provider.token}"

        def do_request():
            import time

            deadline = time.monotonic() + 3
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

            def request_once(endpoint, headers, body):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("本地扩写超过 3 秒")
                request = urllib.request.Request(
                    endpoint, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST"
                )
                with opener.open(request, timeout=remaining) as response:
                    return json.load(response)

            if "qwen" in payload["model"].lower():
                requester = native_minutes_requester(request_once, request_once)
            else:
                requester = request_once
            response = requester(url, headers, payload)
            return extract_json(response)

        return await asyncio.to_thread(do_request)

    async def synthesize(
        self,
        raw_text: str,
        captured_at: float,
        segments: List[Dict[str, Any]],
        brief: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Synthesize shorthand note with surrounding context; fails gracefully to raw text."""
        raw_text_clean = str(raw_text).strip()
        if not raw_text_clean:
            return {
                "status": "failed",
                "summary": "",
                "details": "",
                "speakers": [],
                "anchor_start": captured_at,
                "anchor_end": captured_at,
                "evidence_quote": "",
                "synthesized_text": "",
                "error": "Empty shorthand note",
            }

        context_segments = extract_context_window(segments, captured_at)
        user_prompt = {
            "user_shorthand_note": raw_text_clean,
            "captured_timestamp": round(captured_at, 2),
            "meeting_brief": brief or {},
            "context_transcript": [
                {
                    "start": round(s.get("start", 0), 2),
                    "end": round(s.get("end", 0), 2),
                    "speaker": s.get("speaker") or s.get("source_track") or "发言人",
                    "text": s.get("text", "").strip(),
                    "provisional": bool(s.get("provisional")),
                }
                for s in context_segments
            ],
        }

        messages = [
            {"role": "system", "content": SYSTEM_INSTRUCTION},
            {"role": "user", "content": json.dumps(user_prompt, ensure_ascii=False)},
        ]

        try:
            parsed = await asyncio.wait_for(
                self._execute_llm(messages, SYNTHESIS_SCHEMA),
                timeout=4.0,
            )

            summary = parsed["summary"]
            details = parsed["details"]
            quote = parsed["evidence_quote"]
            if not isinstance(summary, str) or not summary.strip() or not isinstance(details, str):
                raise ValueError("扩写内容格式无效")
            if not isinstance(quote, str) or not quote.strip():
                raise ValueError("扩写缺少原文引用")
            evidence = next((segment for segment in context_segments
                             if not segment.get("provisional") and quote.strip() in segment["text"]), None)
            if evidence is None:
                raise ValueError("扩写引用与已确认原文不符")
            speaker = evidence.get("speaker") or evidence.get("source_track")
            anchor_start, anchor_end = evidence["start"], evidence["end"]
            synthesized_text = f"【{summary.strip()}】{details.strip()}"

            return {
                "status": "ready",
                "summary": summary,
                "details": details,
                "speakers": [speaker] if speaker else [],
                "anchor_start": anchor_start,
                "anchor_end": anchor_end,
                "evidence_quote": quote.strip(),
                "source_track": evidence.get("source_track") or "",
                "synthesized_text": synthesized_text,
                "error": None,
            }

        except Exception as error:
            logger.warning("Shorthand synthesis failed gracefully: %s", error)
            return {
                "status": "failed",
                "summary": raw_text_clean,
                "details": "",
                "speakers": [],
                "anchor_start": None,
                "anchor_end": None,
                "evidence_quote": "",
                "synthesized_text": raw_text_clean,
                "error": str(error),
            }
