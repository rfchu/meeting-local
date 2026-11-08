"""Composition root: construct shared resources, bind lifecycle and register feature routes."""
from .task_management import register_task_management
from .model_preferences import register_model_preferences
from .runtime_management import register_runtime_management
from .recording_acceptance import RecordingAcceptance,register_acceptance
from .quality_samples import QualitySamples
from .quality_sample_routes import register_quality_samples
import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from .db import Store
from .worker import Worker
from .providers import WhisperCpp, get_asr_provider
from .analysis import LMStudioProvider
from .speaker_review import generate_review
from .recordings import RecordingStore
from .native_capture import NativeCapture
from .live_session import LiveSessions
from .live_results import LiveResults
from .live_corrections import LiveCorrections
from .qwen_resident import QwenResident
from .keep_awake import KeepAwake
from .recording_health import RecordingHealth
from .lifecycle import Lifecycle
from .recording_runtime import RecordingRuntime
from .maintenance import DataLease
from .recording_commands import RecordingCommands, register_recording_commands
from .recording_validation import RecordingValidation, register_recording_validation
from .meeting_review import MeetingReview
from .qa import QA
from .minutes_generation import MinutesGeneration
from .auto_minutes import prepare as prepare_auto_minutes
from .retrieval import Retrieval
from .terminology import register_terms
from .capture_probe import CaptureProbe
from .onboarding import register_onboarding
from .live_overview import LiveOverview, register_overview
from .local_embedding import local_embedding
from .paths import RuntimePaths
from .resources import ResourceScheduler,ResourceGate
from .application_services import ApplicationServices,ProviderBindings
from .application_lifecycle import application_lifespan
from .meeting_queries import MeetingQueries
from .configuration_queries import ConfigurationQueries
from .analysis_jobs import AnalysisJobs
from .minutes_jobs import MinutesJobs
from .http_security import register_local_only
from .routes.runtime import register_routes as runtime_routes
from .routes.retrieval import register_routes as retrieval_routes
from .routes.recordings import register_routes as recording_routes
from .routes.meetings import register_routes as meeting_routes
from .routes.minutes import register_routes as minutes_routes
from .routes.copilot import register_routes as copilot_routes
from .http_schemas import (RecordingQualityReviewInput,Segment,RevisionInput,JobInput,AnalysisJobInput,SpeakerReviewApplyInput,LLMConfigInput,RenameInput,MeetingNoteInput,MeetingNotesBatchInput,RetrievalConfigInput,SearchInput,SearchResolveInput,IndexBuildInput,QAPrepareInput,QAGenerateInput,NoteAcceptInput,NoteUseInput,TodoUpdate,MeetingNoteUpdate,MeetingBriefInput,AttentionDecisionInput,ActionTrackInput,DeliveryInput,RecordingInput,RecordingMetadata,CaptureStateInput,RecordingFinish,NativeDraftInput,LiveSessionInput,LiveSessionState,LiveCorrectionInput,LiveCorrectionDecision,KeepAwakeInput,ReviewCheckpointInput,NativeRecordingInput,NativeCommandInput,MinutesGenerateInput,MinutesSaveInput,MinutesPolishInput,AIMinutesGenerateInput)

BASE=Path(__file__).resolve().parents[1]


def _create_app(data_dir=None,start_worker=True):
    store=Store(data_dir or os.getenv('MEETING_DATA',str(BASE/'data')))
    lifecycle=Lifecycle(store.root)
    worker=Worker(store)
    recordings=RecordingStore(store);recordings.lifecycle=lifecycle
    native_capture=NativeCapture(store,recordings)
    capture_probes=CaptureProbe(native_capture)
    meeting_review=MeetingReview(store)
    minutes_generation=MinutesGeneration(store)
    meetings=MeetingQueries(store,recordings,meeting_review,minutes_generation)
    keep_awake=KeepAwake(meetings.recording_open,lambda rid:getattr(native_capture,'is_running',lambda _:False)(rid))
    recording_health=RecordingHealth(store,recordings,native_capture)
    recording_runtime=RecordingRuntime(store,recordings,native_capture,recording_health,lifecycle)
    lifecycle.observer=recording_runtime.observe
    recording_validation=RecordingValidation(store.root,recordings,native_capture,recording_runtime)
    live_results=LiveResults(store,recordings)
    live_corrections=LiveCorrections(store)
    resources=ResourceScheduler()
    live_asr_lock=ResourceGate(resources)
    worker.resources=resources
    live_corrections.resources=resources
    resident=QwenResident(store.root)
    live_sessions=LiveSessions(store,recordings,native_capture,live_results,live_asr_lock,resident,lambda:get_asr_provider('qwen'),lambda:get_asr_provider('whisper'))
    recording_commands=RecordingCommands(recordings,native_capture,live_sessions.pause_boundary)
    live_overview=LiveOverview(store,recordings,live_results)
    live_overview.resources=resources
    # Late-bound factories keep model instances lazy and allow explicit replacement in tests.
    providers=ProviderBindings(
        asr=lambda *args,**kwargs:get_asr_provider(*args,**kwargs),
        llm=lambda *args,**kwargs:LMStudioProvider(*args,**kwargs),
        speaker_review=lambda *args,**kwargs:generate_review(*args,**kwargs),
        auto_minutes=lambda *args,**kwargs:prepare_auto_minutes(*args,**kwargs),
        embedding=lambda:local_embedding())
    analysis_jobs=AnalysisJobs(store,meetings,providers)
    minutes_jobs=MinutesJobs(store,meetings,providers,minutes_generation)
    worker.minutes_jobs=minutes_jobs
    services=ApplicationServices(
        store=store,worker=worker,lifecycle=lifecycle,recordings=recordings,resources=resources,
        native_capture=native_capture,capture_probes=capture_probes,keep_awake=keep_awake,
        recording_health=recording_health,recording_runtime=recording_runtime,
        recording_validation=recording_validation,recording_commands=recording_commands,
        live_corrections=live_corrections,live_results=live_results,live_sessions=live_sessions,
        live_overview=live_overview,live_asr_lock=live_asr_lock,resident=resident,
        meeting_review=meeting_review,minutes_generation=minutes_generation,meetings=meetings,
        configurations=ConfigurationQueries(store),analysis_jobs=analysis_jobs,minutes_jobs=minutes_jobs,
        qa=QA(store),retrieval=Retrieval(store),providers=providers)
    app=FastAPI(title='听记 · Local Meeting',lifespan=application_lifespan(services,start_worker))
    app.state.services=services
    # Existing integrations can continue accessing these application-state attributes.
    for name in ('store','worker','lifecycle','capture_probes','keep_awake','recording_runtime',
                 'recording_validation','live_sessions','recording_commands','live_overview'):
        setattr(app.state,name,getattr(services,name))
    register_local_only(app)
    register_onboarding(app,store,native_capture,capture_probes)
    quality_samples=QualitySamples(store.root)
    app.state.quality_samples=quality_samples
    register_quality_samples(app,quality_samples)
    acceptance=RecordingAcceptance(recording_validation)
    app.state.recording_acceptance=acceptance
    register_acceptance(app,acceptance)
    register_runtime_management(app,store,native_capture,resident,capture_probes,recording_runtime,resources,live_sessions,acceptance)
    register_model_preferences(app,store)
    register_task_management(app,store,worker,resources)
    register_recording_validation(app,recording_validation)
    register_recording_commands(app,recording_commands)
    register_overview(app,live_overview)
    register_terms(app,store,recordings)
    for register in (runtime_routes,retrieval_routes,recording_routes,meeting_routes,minutes_routes,copilot_routes):
        register(app,services)
    app.mount('/',StaticFiles(directory=BASE/'app/static',html=True),name='ui')
    return app


def create_app(data_dir=None,start_worker=True):
    paths=RuntimePaths.from_env(data=data_dir)
    with DataLease(paths.data):
        app=_create_app(paths.data,start_worker)
        app.state.paths=paths
        return app

app=create_app()
