"""HTTP input contracts, independent of application construction."""
from .languages import SourceLanguage, OutputLanguage
from .interview import InterviewPreparation
from typing import Literal
from pydantic import BaseModel, Field, model_validator
from .minutes import MeetingMinutes
from .minutes_composition import CompositionSettings
from .auto_minutes import MinutesFollowupInput

class RecordingQualityReviewInput(BaseModel):
    status: Literal['pending','clear','gap','uncertain']
    expected_version: int=Field(ge=0)


class Segment(BaseModel):
    id: str=Field(min_length=1,max_length=100)
    start: float=Field(ge=0,allow_inf_nan=False)
    end: float=Field(ge=0,allow_inf_nan=False)
    text: str=Field(max_length=20000)
    speaker: str=Field(default='未分配',max_length=100)
    boundary_review: bool=False
    timestamp_precision: Literal['chunk','aligned'] | None=None
    alignment_warning: str=Field(default='',max_length=300)
    speaker_locked: bool=False
    continuation_id: str=Field(default='',max_length=160)
    continues_previous: bool=False
    continues_next: bool=False
    boundary_reason: str=Field(default='',max_length=30)


class RevisionInput(BaseModel):
    revision: int
    segments: list[Segment]=Field(max_length=50000)


class JobInput(BaseModel):
    source_language: SourceLanguage | None=None
    recording_handoff: bool=False
    minutes_followup: MinutesFollowupInput | None=None
    completion_mode: Literal["full","quick"]="full"
    engine: Literal["whisper","qwen"]="whisper"
    diarization: bool=False
    num_speakers: int=Field(default=0,ge=0,le=30)


class AnalysisJobInput(BaseModel):
    minutes_followup: dict | None=None
    profile_id: str | None=Field(default=None,max_length=100)
    model: str | None=Field(default=None,max_length=500)


class SpeakerReviewApplyInput(BaseModel):
    revision: int
    segment_ids: list[str]=Field(min_length=1,max_length=500)


class LLMConfigInput(BaseModel):
    config_id: str | None=Field(default=None,max_length=100)
    name: str=Field(min_length=1,max_length=100)
    provider_type: Literal['local','cloud']
    base_url: str=Field(min_length=1,max_length=1000)
    model: str=Field(min_length=1,max_length=500)
    context_window: int=Field(default=32768,ge=4096,le=2_000_000)
    analysis_chunk_mode: Literal['auto','quality','speed','custom']='auto'
    analysis_chunk_chars: int=Field(default=0,ge=0,le=1_000_000)
    api_key: str | None=Field(default=None,max_length=10000)
    is_default: bool=False
    clear_api_key: bool=False


class RenameInput(BaseModel):
    title: str=Field(min_length=1,max_length=200)


class MeetingNoteInput(BaseModel):
    client_id: str=Field(default='',max_length=100)
    overview_focus: bool=False
    captured_at: float=Field(default=0,ge=0,allow_inf_nan=False)
    note_type: Literal['note','highlight','question','decision','action']='note'
    raw_text: str=Field(default='',max_length=5000)
    note_scope: Literal['personal','meeting']='personal'
    is_marker: bool=False
    @model_validator(mode='after')
    def valid_note(self):
        if not self.raw_text.strip() and not self.is_marker:raise ValueError('简记内容不能为空，或使用标记此刻')
        return self


class MeetingNotesBatchInput(BaseModel):
    notes: list[MeetingNoteInput]=Field(max_length=1000)


class RetrievalConfigInput(BaseModel):
    provider: Literal['external','builtin']='external'
    profile_id: str=Field(default='',max_length=100)
    model: str=Field(default='',max_length=500)


class SearchInput(BaseModel):
    query: str=Field(min_length=1,max_length=500)
    mode: Literal['keyword','semantic','hybrid']='keyword'
    meeting_id: str | None=None
    kind: Literal['transcript','note','expanded_note','minutes'] | None=None


class SearchResolveInput(BaseModel):
    id: str=Field(min_length=1,max_length=1000)
    fingerprint: str=Field(min_length=64,max_length=64)


class IndexBuildInput(BaseModel):
    force: bool=False


class QAPrepareInput(BaseModel):
    output_language: OutputLanguage='zh'
    question: str=Field(min_length=1,max_length=500)
    profile_id: str=Field(min_length=1,max_length=100)
    query: str=Field(default='',max_length=500)
    mode: Literal['keyword','semantic','hybrid']='keyword'
    meeting_id: str | None=None
    kind: Literal['transcript','note','expanded_note','minutes'] | None='transcript'
    include_personal: bool=False


class QAGenerateInput(BaseModel):
    confirm_cloud: bool=False


class NoteAcceptInput(BaseModel):
    artifact_id: str=Field(min_length=1,max_length=100)
    accepted_text: str | None=Field(default=None,min_length=1,max_length=3000)


class NoteUseInput(BaseModel):
    kind: Literal['minutes','todo']
    fingerprint: str=Field(min_length=64,max_length=64)
    revision: int=Field(ge=0)
    content: str=Field(min_length=1,max_length=2000)
    source_segment_ids: list[str]=Field(default_factory=list,max_length=20)
    section: Literal['conclusions','decisions','action_items','open_questions']='conclusions'
    minutes_id: str | None=None
    confirmed: bool=False


class TodoUpdate(BaseModel):
    status: Literal['pending','done']
    owner: str=Field(default='',max_length=200)
    due_date: str=Field(default='',max_length=100)


class MeetingNoteUpdate(BaseModel):
    note_type: Literal['note','highlight','question','decision','action']
    raw_text: str=Field(max_length=5000)
    note_scope: Literal['personal','meeting'] | None=None
    is_marker: bool | None=None
    captured_at: float | None=Field(default=None,ge=0,allow_inf_nan=False)


class MeetingBriefInput(BaseModel):
    source_language: SourceLanguage | None=None
    output_language: OutputLanguage | None=None
    interview:InterviewPreparation|None=None
    title: str=Field(default='',max_length=200)
    purpose: str=Field(default='',max_length=1000)
    agenda: str=Field(default='',max_length=3000)
    participants: str=Field(default='',max_length=1000)
    questions: str=Field(default='',max_length=2000)
    previous_meeting_id: str | None=Field(default=None,max_length=100)


class AttentionDecisionInput(BaseModel):
    decision: Literal['meeting','personal','deferred']
    fingerprint: str=Field(min_length=64,max_length=64)
    revision: int=Field(ge=0)
    content: str=Field(default='',max_length=3000)
    source_segment_ids: list[str]=Field(default_factory=list,max_length=20)
    confirmed: bool=False


class ActionTrackInput(BaseModel):
    content: str=Field(min_length=1,max_length=2000)
    owner: str=Field(default='',max_length=200)
    due_date: str=Field(default='',max_length=200)
    confirmed: bool=False


class DeliveryInput(BaseModel):
    artifact_id: str=Field(min_length=1,max_length=100)
    include_citations: bool=False
    confirmed: bool=False


class RecordingInput(BaseModel):
    mime_type: Literal['audio/webm;codecs=opus','audio/webm','audio/mp4','audio/mp4;codecs=mp4a.40.2']='audio/webm'


class RecordingMetadata(MeetingNotesBatchInput):
    brief: MeetingBriefInput | None=None
    duration: float=Field(default=0,ge=0,allow_inf_nan=False)
    chunk_count: int=Field(default=0,ge=0,le=100000)


class CaptureStateInput(BaseModel):
    state:Literal['recording','paused','stopped','interrupted']


class RecordingFinish(BaseModel):
    chunk_count: int=Field(ge=1,le=100000)


class NativeDraftInput(BaseModel):
    source_language: SourceLanguage | None=None
    context_enabled: bool=True
    terms: str=Field(default="",max_length=1000)
    engine: Literal["whisper","qwen"]="whisper"
    offset: float=Field(default=0,ge=0,le=86400,allow_inf_nan=False)
    responsive: bool=False
    window_seconds: Literal[5,10,15]=15


class LiveSessionInput(BaseModel):
    source_language: SourceLanguage | None=None
    engine: Literal["whisper","qwen"]="qwen"
    prefix_enabled: bool=True
    responsive: bool=True
    context_enabled: bool=True
    terms: str=Field(default='',max_length=1000)


class LiveSessionState(BaseModel):
    paused: bool=False


class LiveCorrectionInput(BaseModel):
    range_id: str=Field(min_length=1,max_length=100)
    segment_index: int=Field(ge=0,le=100)
    profile_id: str=Field(min_length=1,max_length=100)


class LiveCorrectionDecision(BaseModel):
    state: Literal['accepted','rejected','suggested']


class KeepAwakeInput(BaseModel):
    enabled: bool=True


class ReviewCheckpointInput(BaseModel):
    fingerprint: str=Field(pattern=r'^[0-9a-f]{64}$')
    decision: Literal['confirmed','skipped']='confirmed'


class NativeRecordingInput(BaseModel):
    microphone: bool=True
    keep_awake: bool=True


class NativeCommandInput(BaseModel):
    command: Literal['pause','resume','stop']


class MinutesGenerateInput(BaseModel):
    style: Literal['formal','brief','internal']='formal'
    template_id: str=Field(default='general-meeting',max_length=100)


class MinutesSaveInput(BaseModel):
    merge_from_minutes_id: str | None=Field(default=None,max_length=100)
    source_minutes_id: str | None=Field(default=None,max_length=100)
    source_analysis_id: str | None=Field(default=None,max_length=100)
    allow_branch: bool=False
    document: MeetingMinutes


class MinutesPolishInput(BaseModel):
    output_language: OutputLanguage | None=None
    profile_id: str | None=Field(default=None,max_length=100)
    model: str | None=Field(default=None,max_length=500)


class AIMinutesGenerateInput(MinutesPolishInput):
    composition: CompositionSettings | None=None
    source_minutes_id: str | None=Field(default=None,max_length=100)
    mode: Literal['quick','full']='quick'
    template_id: str=Field(default='general-meeting',max_length=100)


class SourceReviewInput(BaseModel):
    artifact_id: str=Field(min_length=1,max_length=100)
    source_id: str=Field(min_length=1,max_length=300)
    fingerprint: str=Field(pattern=r'^[a-f0-9]{64}$')
    request_id: str=Field(min_length=1,max_length=80)
    decision: Literal['reviewed','keep_history','reset']
    confirmed: bool=False


class DeliverySentInput(BaseModel):
    sent: bool


class AudiencePreviewInput(BaseModel):
    artifact_id: str = Field(min_length=1,max_length=100)
    audience: Literal['internal','external']
    selection: list[str] = Field(default_factory=list,max_length=250)
    include_citations: bool = False


class AudienceFinalizeInput(AudiencePreviewInput):
    fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')
    confirmed: bool = False


class DeliveryFinalizeInput(DeliveryInput):
    fingerprint: str=Field(pattern=r'^[a-f0-9]{64}$')


class RecoverCaptureControl(BaseModel):
    expected_generation:int=Field(ge=0,le=9007199254740991)

class RecoverLiveSession(BaseModel):
    expected_seq:int=Field(ge=0,le=9007199254740991)
