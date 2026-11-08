"""Copilot in-meeting endpoints for shorthand note synthesis and speculative intent."""
import asyncio
import logging
from collections import OrderedDict
from typing import Optional
from fastapi import HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ..application_services import ApplicationServices
from ..shorthand import ShorthandSynthesizer
from ..analysis import OpenAICompatibleProvider
from ..model_preferences import resolve_profile
from ..speculative_intent import (
    SpeculativeDetector,
    detect_candidates_from_segments,
)

logger = logging.getLogger(__name__)

class ShorthandInput(BaseModel):
    raw_text: str = Field(min_length=1, max_length=2000)
    captured_at: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    synthesize: bool = False
    speaker_hint: str = Field(default="", max_length=100)
    client_id: str = Field(default="", max_length=100)


def register_routes(app, services: ApplicationServices):
    recordings = services.recordings
    store = services.store
    live_sessions = services.live_sessions
    live_results = services.live_results
    resources = services.resources
    pending_synthesis = {}

    detector = SpeculativeDetector()
    candidate_sessions = OrderedDict()

    def candidate_cache(rid):
        cache = candidate_sessions.setdefault(rid, {"active": {}, "accepted": {}})
        candidate_sessions.move_to_end(rid)
        if len(candidate_sessions) > 32:
            candidate_sessions.popitem(last=False)
        return cache

    async def synthesis_context(rid, captured_at):
        deadline = asyncio.get_running_loop().time() + 20
        while live_sessions:
            try:
                snapshot = live_sessions.snapshot(rid)
            except (OSError, ValueError):
                break
            partial = snapshot.get("partial") or {}
            preview = partial.get("tracks") or ([partial] if partial.get("text") else [])
            segments = snapshot.get("segments", [])
            if (snapshot.get("status") not in ("running", "draining")
                    or snapshot.get("cursor", 0) >= captured_at + 10
                    or asyncio.get_running_loop().time() >= deadline):
                return segments + [dict(item, provisional=True) for item in preview if item.get("text")]
            await asyncio.sleep(.5)
        session = recordings.read(rid)
        engine = session.get("draft_engine", "qwen")
        ranges = live_results.snapshot(rid, engine, session.get("source_language"))
        return [segment for entry in ranges for segment in entry["segments"]]

    @app.post("/api/recordings/{rid}/notes/shorthand", status_code=201)
    async def create_shorthand_note(rid: str, body: ShorthandInput):
        clean_text = body.raw_text.strip()
        if body.client_id.startswith(("candidate:", "copilot:")):
            raise HTTPException(422, "速记请求编号无效")
        if not clean_text:
            raise HTTPException(422, "速记内容不能为空")

        try:
            session = recordings.read(rid)
        except (FileNotFoundError, ValueError):
            raise HTTPException(404, "录音会话不存在")

        if session.get("state") == "completed" and not store.one(
            "SELECT id FROM meetings WHERE id=?", (rid,)
        ):
            raise HTTPException(409, "录音已完成且不可编辑")

        captured_at = body.captured_at
        if captured_at is None:
            if session.get("source") == "macos":
                try:
                    capture_status = services.native_capture.status(rid, touch=False)
                    captured_at = float(
                        capture_status.get("duration", session.get("duration", 0.0))
                    )
                except Exception:
                    captured_at = float(session.get("duration", 0.0))
            else:
                captured_at = float(session.get("duration", 0.0))

        initial_status = "synthesizing" if body.synthesize else "raw"
        note_entry = {
            "raw_text": clean_text,
            "captured_at": captured_at,
            "note_type": "note",
            "status": initial_status,
            "speaker_hint": body.speaker_hint,
            "client_id": body.client_id,
        }

        try:
            created = await run_in_threadpool(recordings.add_note, rid, note_entry)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        if live_sessions:
            try:
                live_sessions.emit(rid, "note_created", note=created)
            except Exception as e:
                logger.warning("Failed to emit note_created: %s", e)

        key = (rid, created["id"])
        if created["status"] == "synthesizing" and key not in pending_synthesis:
            async def run_synthesis(note_id: str):
                try:
                    profile = resolve_profile(store, "notes")
                    if not profile or profile["provider_type"] != "local":
                        profile = store.one(
                            "SELECT * FROM llm_configs WHERE provider_type='local' ORDER BY is_default DESC, created, id LIMIT 1"
                        )
                    if not profile:
                        raise ValueError("未配置本地扩写模型")
                    segments = await synthesis_context(rid, created["captured_at"])
                    synthesizer = ShorthandSynthesizer(
                        llm_provider=OpenAICompatibleProvider(profile),
                        resources=resources, owner=("live_shorthand", rid),
                    )
                    result = await synthesizer.synthesize(clean_text, created["captured_at"], segments, session.get("brief"))

                    speaker = (
                        result["speakers"][0]
                        if result["speakers"]
                        else body.speaker_hint
                    )
                    updated = await run_in_threadpool(
                        recordings.update_note, rid,
                        note_id,
                        expected_raw=clean_text,
                        status=result["status"],
                        synthesized_text=result["synthesized_text"],
                        anchor_start=result["anchor_start"],
                        anchor_end=result["anchor_end"],
                        speaker_hint=speaker,
                        shorthand_quote=result["evidence_quote"],
                        shorthand_track=result.get("source_track", ""),
                    )
                    if live_sessions and updated:
                        try:
                            live_sessions.emit(rid, "note_synthesized", note=updated)
                        except Exception:
                            pass
                except (Exception, asyncio.CancelledError) as err:
                    logger.warning("Shorthand synthesis unavailable: %s", type(err).__name__)
                    try:
                        updated = await run_in_threadpool(
                            recordings.update_note, rid, note_id, expected_raw=clean_text, status="failed"
                        )
                        if updated and live_sessions:
                            live_sessions.emit(rid, "note_synthesized", note=updated)
                    except (OSError, ValueError, KeyError):
                        logger.warning("Could not update shorthand status")
                finally:
                    pending_synthesis.pop(key, None)

            if len(pending_synthesis) >= 32:
                created = recordings.update_note(rid, created["id"], status="failed")
            else:
                pending_synthesis[key] = asyncio.create_task(run_synthesis(created["id"]))

        return created

    @app.get("/api/recordings/{rid}/notes/shorthand")
    def shorthand_notes(rid: str):
        try:
            notes = recordings.copilot_notes(rid)
        except (FileNotFoundError, ValueError) as error:
            raise HTTPException(404, "录音会话不存在") from error
        return [
            {**note, "status": "failed"} if note["status"] == "synthesizing"
            and (rid, note["id"]) not in pending_synthesis else note
            for note in notes
        ]

    @app.get("/api/recordings/{rid}/candidates")
    async def get_candidates(rid: str):
        try:
            recordings.read(rid)
        except (FileNotFoundError, ValueError):
            raise HTTPException(404, "录音会话不存在")

        session_cache = candidate_cache(rid)

        segments = []
        if live_sessions:
            try:
                snapshot = live_sessions.snapshot(rid)
                segments = snapshot.get("segments", [])
            except Exception:
                pass
        if not segments and live_results:
            try:
                session = recordings.read(rid)
                ranges = live_results.snapshot(rid, session.get("draft_engine", "qwen"), session.get("source_language"))
                for r in ranges:
                    segments.extend(r.get("segments", []))
            except Exception:
                pass

        session = recordings.read(rid)
        dismissed = set(session.get("dismissed_candidates", []))
        published = {row["client_id"].removeprefix("candidate:") for row in store.all(
            "SELECT client_id FROM meeting_notes WHERE meeting_id=? AND client_id LIKE 'candidate:%'", (rid,)
        )}
        saved = {note["candidate_id"] for note in session["notes"] if note.get("candidate_id")}
        accepted_ids = published | saved | set(session_cache["accepted"])
        detected = detect_candidates_from_segments(
            sorted(segments, key=lambda item: item["start"])[-60:],
            seen_ids=accepted_ids | dismissed, detector=detector,
        )
        session_cache["active"] = {cand.candidate_id: cand for cand in detected}
        return {
            "candidates": [candidate.to_dict() for candidate in detected],
            "accepted_count": len(accepted_ids),
            "dismissed_count": len(dismissed),
        }

    @app.post("/api/recordings/{rid}/candidates/{cid}/accept")
    async def accept_candidate(rid: str, cid: str):
        try:
            recordings.read(rid)
        except (FileNotFoundError, ValueError):
            raise HTTPException(404, "录音会话不存在")

        session_cache = candidate_cache(rid)

        accepted = session_cache["accepted"].get(cid)
        if accepted:
            return {"ok": True, "candidate_id": cid, "status": "accepted", "note": accepted}
        cand = session_cache["active"].get(cid)
        if not cand:
            saved = next((note for note in recordings.read(rid)["notes"]
                          if note.get("candidate_id") == cid), None)
            saved = saved or store.one(
                "SELECT * FROM meeting_notes WHERE meeting_id=? AND client_id=?", (rid, f"candidate:{cid}")
            )
            if saved:
                return {"ok": True, "candidate_id": cid, "status": "accepted", "note": saved}
            raise HTTPException(404, "候选意图不存在或已处理")
        try:
            created_note = recordings.add_note(rid, {
                "raw_text": cand.content,
                "captured_at": cand.timestamp,
                "note_type": "action" if cand.kind == "todo" else "decision",
                "note_scope": "meeting",
                "speaker_hint": cand.speaker,
                "shorthand_track": cand.source_track,
                "owner": cand.owner,
                "due_date": cand.due_date,
                "candidate_id": cid,
                "client_id": f"candidate:{cid}",
            })
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        session_cache["active"].pop(cid, None)
        session_cache["accepted"][cid] = created_note

        if live_sessions:
            try:
                live_sessions.emit(
                    rid,
                    "candidate_accepted",
                    candidate_id=cid,
                    kind=cand.kind,
                    note=created_note,
                )
            except Exception as e:
                logger.warning("Failed to emit candidate_accepted: %s", e)

        return {
            "ok": True,
            "candidate_id": cid,
            "status": "accepted",
            "note": created_note,
        }

    @app.post("/api/recordings/{rid}/candidates/{cid}/reject")
    async def reject_candidate(rid: str, cid: str):
        try:
            recordings.read(rid)
        except (FileNotFoundError, ValueError):
            raise HTTPException(404, "录音会话不存在")

        session_cache = candidate_cache(rid)

        if cid in recordings.read(rid).get("dismissed_candidates", []):
            return {"ok": True, "candidate_id": cid, "status": "dismissed"}
        if cid not in session_cache["active"]:
            raise HTTPException(404, "候选意图不存在或已处理")
        recordings.dismiss_candidate(rid, cid)
        session_cache["active"].pop(cid, None)

        if live_sessions:
            try:
                live_sessions.emit(rid, "candidate_dismissed", candidate_id=cid)
            except Exception as e:
                logger.warning("Failed to emit candidate_dismissed: %s", e)

        return {
            "ok": True,
            "candidate_id": cid,
            "status": "dismissed",
        }
