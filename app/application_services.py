"""Application-scoped dependencies, passed explicitly to lifecycle and feature routers."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable,TYPE_CHECKING
from threading import Lock
if TYPE_CHECKING:
    from .db import Store
    from .worker import Worker
    from .lifecycle import Lifecycle
    from .recordings import RecordingStore
    from .native_capture import NativeCapture
    from .live_session import LiveSessions
    from .live_results import LiveResults
    from .live_corrections import LiveCorrections
    from .live_overview import LiveOverview
    from .qwen_resident import QwenResident
    from .capture_probe import CaptureProbe
    from .keep_awake import KeepAwake
    from .recording_health import RecordingHealth
    from .recording_runtime import RecordingRuntime
    from .recording_commands import RecordingCommands
    from .recording_validation import RecordingValidation
    from .meeting_review import MeetingReview
    from .minutes_generation import MinutesGeneration
    from .meeting_queries import MeetingQueries
    from .configuration_queries import ConfigurationQueries
    from .analysis_jobs import AnalysisJobs
    from .qa import QA
    from .retrieval import Retrieval

@dataclass(frozen=True)
class ProviderBindings:
    asr: Callable
    llm: Callable
    speaker_review: Callable
    auto_minutes: Callable
    embedding: Callable

@dataclass(frozen=True)
class ApplicationServices:
    store: Store
    worker: Worker
    lifecycle: Lifecycle
    recordings: RecordingStore
    native_capture: NativeCapture
    capture_probes: CaptureProbe
    keep_awake: KeepAwake
    recording_health: RecordingHealth
    recording_runtime: RecordingRuntime
    recording_validation: RecordingValidation
    recording_commands: RecordingCommands
    live_corrections: LiveCorrections
    live_results: LiveResults
    live_sessions: LiveSessions
    live_overview: LiveOverview
    live_asr_lock: Lock
    resident: QwenResident
    meeting_review: MeetingReview
    minutes_generation: MinutesGeneration
    meetings: MeetingQueries
    configurations: ConfigurationQueries
    analysis_jobs: AnalysisJobs
    qa: QA
    retrieval: Retrieval
    providers: ProviderBindings
