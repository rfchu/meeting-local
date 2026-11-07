"""D1 read-only post-meeting handoff. No inference, file probes or implicit decisions."""
import hashlib,json
from .db import encode
from . import meeting_flow,minutes_versions,minutes_delivery
from .meeting_review import MeetingReview
from .live_overview import approved_context

REASON_LABELS={'chinese_record_changed':'中文记录或译文已变化','transcript_changed':'原文版本变化','meeting_context_changed':'会议资料或已确认关注点变化',
    'dependency_limit':'来源链过长，需核对','dependency_stale':'来源纪要已过期','dependency_missing':'来源纪要不存在','dependency_cycle':'来源关系循环',
    'note_changed':'已采用简记变化','attention_changed':'简记用途确认变化','invalid_provenance':'来源记录无法核验','meeting_missing':'会议不存在'}


def snapshot(db,mid,quality=None):
    row=db.execute('SELECT id,title,revision,segments,duration FROM meetings WHERE id=?',(mid,)).fetchone()
    if not row:raise KeyError(mid)
    revision=row['revision'];segments=json.loads(row['segments']);has_text=bool(revision and segments)
    review=MeetingReview.snapshot(db,mid)
    notes=[dict(item) for item in db.execute('SELECT * FROM meeting_notes WHERE meeting_id=? ORDER BY id',(mid,))]
    reviews=meeting_flow.dispositions(db,mid,revision)
    attention=meeting_flow.attention_counts(db,mid,revision)
    pending,optional,approved=(attention[key] for key in ('pending_meeting','optional','confirmed_meeting'))
    chosen=minutes_versions.selected(db,mid)
    artifacts=[dict(item) for item in db.execute("SELECT * FROM artifacts WHERE meeting_id=? AND kind='meeting_minutes' ORDER BY created DESC,rowid DESC",(mid,))]
    def reasons(item):return meeting_flow.artifact_reasons(db,mid,json.loads(item['provenance']),item['revision'])
    chosen_reasons=reasons(chosen) if chosen else []
    valid=bool(chosen and not chosen_reasons)
    valid_versions=[item for item in artifacts if not reasons(item)]
    drafts=[item for item in valid_versions if json.loads(item['provenance']).get('draft') and (not chosen or item['id']!=chosen['id'])
        and not any(json.loads(child['provenance']).get('parent_minutes_id')==item['id'] for child in valid_versions)]
    provenance=json.loads(chosen['provenance']) if chosen else {};document=json.loads(chosen['payload']) if chosen else {}
    adopted=len(provenance.get('adopted_notes',[])) if valid else 0
    jobs=[dict(item) for item in db.execute("SELECT id,kind,state,stage,config FROM jobs WHERE meeting_id=? AND state IN ('queued','running') ORDER BY created,rowid",(mid,))]
    def planned_minutes(job):
        try:return bool(json.loads(job['config']).get('minutes_followup'))
        except (ValueError,TypeError,AttributeError):return False
    history=[dict(item) for item in db.execute('SELECT id,kind,state,config FROM jobs WHERE meeting_id=? ORDER BY created DESC,rowid DESC',(mid,))]
    def interrupted(predicate,revision_key):
        job=next((item for item in history if predicate(item)),None)
        if not job or job['state'] not in ('failed','cancelled'):return None
        try:bound=json.loads(job['config']).get(revision_key)
        except (ValueError,TypeError,AttributeError):return None
        return job if bound==revision else None
    asr_interruption=interrupted(lambda item:item['kind'] in (None,'transcription'),'base_revision') if not has_text else None
    minutes_interruption=interrupted(lambda item:item['kind']=='minutes' or planned_minutes(item),'revision') if not valid else None
    minutes_processing=any(item['kind']=='minutes' or planned_minutes(item) for item in jobs)
    live=db.execute('SELECT status,cursor,config FROM live_sessions WHERE recording_id=?',(mid,)).fetchone()
    source=db.execute('SELECT source FROM revisions WHERE meeting_id=? AND revision=?',(mid,revision)).fetchone()
    try:origin=json.loads(source['source']) if source else {}
    except (ValueError,TypeError):origin={}
    if not isinstance(origin,dict):origin={}
    engine=origin.get('engine');live_engine=None
    if live:
        try:live_engine=json.loads(live['config']).get('engine')
        except (ValueError,TypeError,AttributeError):pass
    live_counts={item['state']:item['n'] for item in db.execute('SELECT state,count(*) AS n FROM live_asr_ranges WHERE recording_id=? GROUP BY state',(mid,))}
    latest_overview=db.execute('SELECT version,cursor,payload FROM live_overview_snapshots WHERE recording_id=? ORDER BY version DESC LIMIT 1',(mid,)).fetchone()
    try:
        overview_items=json.loads(latest_overview['payload']).get('items',[]) if latest_overview else []
        overview_count=len(overview_items) if isinstance(overview_items,list) else None
    except (ValueError,TypeError,AttributeError):overview_count=None
    overview_reviews=[dict(item) for item in db.execute('SELECT item_id,state,payload FROM live_overview_reviews WHERE recording_id=? ORDER BY item_id',(mid,))]
    try:usable=approved_context(db,mid,segments) if has_text else []
    except (ValueError,TypeError,KeyError):usable=[]
    confirmed=sum(item['state']=='confirmed' for item in overview_reviews)
    stale_overview=max(0,confirmed-len(usable))
    todos=meeting_flow.tasks(db,mid);meeting_todos=[item for item in todos if item['note_scope']=='meeting']
    stale_tasks=sum(bool(item['stale']) and item['status']=='pending' for item in meeting_todos)
    historical_stale=sum(bool(item['stale']) and item['status']!='pending' for item in meeting_todos)
    bindings=meeting_flow.action_bindings(db,mid,chosen) if valid else {}
    candidates=sum(str(index) not in bindings for index,_ in enumerate(document.get('action_items',[]))) if valid else 0
    deliveries=db.execute('SELECT id,artifact_id FROM minutes_deliveries WHERE meeting_id=? ORDER BY created DESC,rowid DESC',(mid,)).fetchall()
    try:delivery_payload=minutes_delivery.prepare(db,mid,chosen['id'],False) if valid else None
    except ValueError:delivery_payload=None
    matching=minutes_delivery.matching(db,mid,chosen['id'],delivery_payload,exact_citations=False,review=review) if valid else []
    delivered=bool(matching and all(review['steps'][step]['state']=='done' for step in ('transcript','speakers','minutes')))
    stages=[]
    def stage(key,label,state,summary,counts,**source):
        stages.append({'key':key,'label':label,'state':state,'summary':summary,'counts':counts,'source':source})
    transcript_state='processing' if any(item['kind'] in (None,'transcription') for item in jobs) else 'ready' if has_text and review['steps']['transcript']['state']=='done' else 'review' if has_text or asr_interruption else 'missing'
    stage('transcript','文字稿',transcript_state,f"原文 v{revision}，"+('已记录人工核对' if transcript_state=='ready' else '待回听核对' if has_text else '转写失败或已取消，请查看原任务' if asr_interruption else '尚无正式文字稿'),
        {'segments':len(segments),'live_completed_attempts':live_counts.get('completed',0),'live_failed_attempts':live_counts.get('failed',0)},revision=revision,
        origin='live_reuse' if origin.get('kind')=='live_reuse' else 'transcription' if origin.get('engine') else 'revision',engine=engine,live_engine=live_engine,
        live_confirmed_seconds=round(live['cursor']/16000,2) if live else None,interrupted_job_id=asr_interruption['id'] if asr_interruption else None)
    stage('notes','现场简记','review' if pending else 'ready' if approved else 'optional',f'{pending} 条会议关注点待核对；{optional} 条个人或暂缓记录可稍后整理',
        {'total':len(notes),'pending_meeting':pending,'confirmed_meeting':approved,'optional':optional,'adopted':adopted},revision=revision)
    overview_state='stale' if stale_overview else 'ready' if usable else 'review' if latest_overview and overview_count!=0 else 'optional'
    stage('overview','会中概览',overview_state,f'{len(usable)} 条已确认且可映射到原文；{stale_overview} 条原有确认需复核' if confirmed else '本场未形成可用概览，可直接整理正式纪要' if latest_overview and overview_count==0 else '会中概览仅作线索，可按需核对后纳入整理' if latest_overview else '未使用，可直接整理纪要',
        {'confirmed_usable':len(usable),'stale_confirmed':stale_overview,'items':overview_count or 0},version=latest_overview['version'] if latest_overview else None,
        covered_segments=latest_overview['cursor'] if latest_overview else None,scope='partial_recording',snapshot_readable=overview_count is not None)
    minutes_state='processing' if minutes_processing else 'stale' if chosen and not valid else 'ready' if valid and review['steps']['minutes']['state']=='done' else 'review' if valid or minutes_interruption else 'missing'
    stage('minutes','会议纪要',minutes_state,'纪要整理或前序步骤处理中，已有稿件保留' if minutes_processing else '纪要任务失败或已取消；已有稿件保留，可查看原任务' if minutes_interruption else ('采用稿需更新：'+'、'.join(REASON_LABELS.get(key,key) for key in chosen_reasons)) if chosen_reasons else f'{len(drafts)} 份新草稿待选择；采用稿保持不变' if drafts else '采用稿已核对，可预览交付' if minutes_state=='ready' else '已有采用稿，发送前请核对' if valid else '可直接从全文生成，不要求先做分析',
        {'current_drafts':len(drafts)},artifact_id=chosen['id'] if chosen else None,revision=chosen['revision'] if chosen else None,
        reason_codes=chosen_reasons,interrupted_job_id=minutes_interruption['id'] if minutes_interruption else None,latest_current_draft=drafts[0]['id'] if drafts else None)
    stage('actions','行动与跟进','stale' if stale_tasks else 'review' if candidates else 'ready' if meeting_todos else 'optional',f'{candidates} 项纪要候选待确认；{stale_tasks} 项待办来源需复核'+(f'；{historical_stale} 项已完成事项仅保留历史提醒' if historical_stale else ''),
        {'candidates':candidates,'tracked':len(meeting_todos),'stale':stale_tasks+historical_stale,'active_stale':stale_tasks,'historical_stale':historical_stale,'pending':sum(item['status']=='pending' for item in meeting_todos)},artifact_id=chosen['id'] if valid else None)
    stage('delivery','交付版本','ready' if delivered else 'review' if valid else 'missing','当前采用稿已有交付快照，导出不等于已发送' if delivered else '核对后确认交付版本；旧交付快照保留',
        {'snapshots':len(deliveries),'current':int(delivered)},artifact_id=chosen['id'] if chosen else None,current_delivery_id=matching[0] if delivered else None)
    def action(code,target,label,summary,job_id=None):return {'code':code,'target':target,'label':label,'summary':summary,**({'job_id':job_id} if job_id else {})}
    if jobs:
        job=jobs[0];primary=action('processing','minutes' if job['kind']=='minutes' else 'tasks','查看纪要进度' if job['kind']=='minutes' else '查看处理进度',job['stage'] or '后台任务处理中，已有资料保留',job['id'])
    elif asr_interruption:primary=action('interrupted_transcription','tasks','查看转写任务','转写失败或已取消，检查点和已有原音保留；不会自动重试。',asr_interruption['id'])
    elif not has_text:primary=action('transcribe','transcribe','开始转写','原音导入后，先生成正式文字稿；实时预览不等同于已保存文字稿。')
    elif quality and quality.get('pending_count',0):primary=action('recording_review','recording','核对录音',f"有 {quality['pending_count']} 条录音观察待核对；先回听受影响内容。")
    elif review['steps']['transcript']['state']!='done':primary=action('review_transcript','transcript','核对文字稿与主题','文字稿已保存，先核对关键人名、数字和表述。')
    elif pending:primary=action('review_notes','notes','核对会议关注点',f'{pending} 条准备用于会议成果的简记需确认用途与依据；个人记录可稍后处理。')
    elif review['steps']['speakers']['state']!='done':primary=action('review_speakers','speakers','核对发言人','核对人物对应，也可明确选择本次不区分。')
    elif drafts and (not valid or review['steps']['minutes']['state']!='done'):primary=action('choose_draft','minutes','查看纪要草稿',f'{len(drafts)} 份当前草稿可供比较；不会自动替换采用稿。')
    elif minutes_interruption:primary=action('interrupted_minutes','tasks','查看纪要任务','当前纪要任务失败或已取消；先查看原因，已有稿件保持不变。',minutes_interruption['id'])
    elif not valid:primary=action('refresh_minutes' if chosen else 'generate_minutes','minutes','更新会议纪要' if chosen else '生成会议纪要','、'.join(REASON_LABELS.get(key,key) for key in chosen_reasons) if chosen else '直接从当前全文整理，已确认关注点只作为整理上下文。')
    elif review['steps']['minutes']['state']!='done':primary=action('review_minutes','minutes','核对并保存交付版本','核对决策、负责人、期限及个人信息；最后确认会同时保存纪要核对记录与交付快照。')
    elif (candidates or stale_tasks) and not delivered:primary=action('review_actions','actions','核对行动与跟进',f'{candidates} 项候选、{stale_tasks} 项来源变化；不自动转为承诺，也不要求执行完才交付。')
    else:primary=action('preview' if delivered else 'delivery','preview' if delivered else 'minutes','查看交付与导出' if delivered else '确认交付版本','当前采用稿核对已完成；个人简记和未确认概览可稍后整理。')
    edges=[]
    if origin.get('kind')=='live_reuse':edges.append({'from':'live_asr','to':'transcript','relation':'reused_audio_ranges','count':origin.get('reused_ranges',0)})
    if valid and provenance.get('flow_context_hash'):
        if approved:edges.append({'from':'notes','to':'minutes','relation':'context','count':approved})
        if usable:edges.append({'from':'overview','to':'minutes','relation':'context','count':len(usable)})
    if adopted:edges.append({'from':'notes','to':'minutes','relation':'explicit_adoption','count':adopted})
    if bindings:edges.append({'from':'minutes','to':'actions','relation':'confirmed_tracking','count':len(bindings)})
    version=hashlib.sha256(encode([row['title'],revision,stages,edges,primary,review]).encode()).hexdigest()
    return {'version':1,'source_version':version,'revision':revision,'title':row['title'],'stages':stages,'primary':primary,'edges':edges,
        'review_required':sum(item['state'] in ('review','stale','missing') and item['key'] not in ('overview','delivery') for item in stages)}
