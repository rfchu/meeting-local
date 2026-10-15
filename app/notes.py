"""Manual note intent, bookmarks and explicitly confirmed downstream use."""
import hashlib,json
from .db import uid,encode
from .note_expansion import nearby_segments
from .minutes import MeetingMinutes,MinutesItem,validate_minutes_citations

def insert_note(db,mid,note):
    nid=uid()
    db.execute('INSERT INTO meeting_notes(id,meeting_id,captured_at,note_type,raw_text,note_scope,is_marker) VALUES(?,?,?,?,?,?,?)',
        (nid,mid,note['captured_at'],note['note_type'],note['raw_text'].strip(),note.get('note_scope','personal'),int(note.get('is_marker',False))))
    return nid

def fingerprint(note):
    fields={key:note[key] for key in ('id','raw_text','note_type','captured_at','note_scope','is_marker','accepted_text','expansion_revision')}
    return hashlib.sha256(encode(fields).encode()).hexdigest()

def review(store,mid,nid):
    note=store.one('SELECT * FROM meeting_notes WHERE id=? AND meeting_id=?',(nid,mid))
    meeting=store.one('SELECT * FROM meetings WHERE id=?',(mid,))
    if not note or not meeting:raise ValueError('简记不存在')
    return {'fingerprint':fingerprint(note),'revision':meeting['revision'],'segments':nearby_segments(json.loads(meeting['segments']),note['captured_at'])}

def use_note(store,mid,nid,body):
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        note=db.execute('SELECT * FROM meeting_notes WHERE id=? AND meeting_id=?',(nid,mid)).fetchone()
        meeting=db.execute('SELECT * FROM meetings WHERE id=?',(mid,)).fetchone()
        if not note or not meeting:raise ValueError('简记不存在')
        if fingerprint(note)!=body.fingerprint or meeting['revision']!=body.revision:raise ValueError('简记或文字稿已变化，请重新核对')
        if not body.confirmed:raise ValueError('请先核对并确认使用内容')
        text=body.content.strip()
        if not text:raise ValueError('请先补充要采用的内容')
        nearby={s['id'] for s in nearby_segments(json.loads(meeting['segments']),note['captured_at'])}
        ids=list(dict.fromkeys(body.source_segment_ids))
        if not set(ids)<=nearby:raise ValueError('引用不属于当前附近原文，请重新核对')
        key=hashlib.sha256(encode([nid,body.fingerprint,body.revision,body.kind,body.section if body.kind=='minutes' else None,text,ids if body.kind=='minutes' else []]).encode()).hexdigest()
        if body.kind=='todo':
            existing=db.execute('SELECT id FROM note_todos WHERE id=?',(key,)).fetchone()
            if existing:return {'id':key,'reused':True}
            db.execute('INSERT INTO note_todos(id,meeting_id,note_id,content,revision,note_fingerprint,source_segment_ids,note_scope) VALUES(?,?,?,?,?,?,?,?)',
                (key,mid,nid,text,body.revision,body.fingerprint,encode(ids),note['note_scope']))
            return {'id':key,'reused':False}
        if note['note_scope']!='meeting':raise ValueError('个人备注不能直接采用到纪要，请先改为会议内容并核对')
        if not body.revision or not ids:raise ValueError('采用到纪要需要当前文字稿及至少一条原文依据')
        artifact=db.execute("SELECT * FROM artifacts WHERE meeting_id=? AND revision=? AND kind='meeting_minutes' ORDER BY created DESC,rowid DESC LIMIT 1",(mid,meeting['revision'])).fetchone()
        if not artifact:raise ValueError('请先生成当前版本的会议纪要，再采用简记')
        provenance=json.loads(artifact['provenance']);adopted=provenance.get('adopted_notes',[])
        if any(item.get('key')==key for item in adopted):return {'id':artifact['id'],'reused':True}
        if artifact['id']!=body.minutes_id:raise ValueError('会议纪要已变化，请重新打开核对面板')
        document=MeetingMinutes.model_validate(json.loads(artifact['payload']))
        if any(item.content==text and item.source_segment_ids==ids for item in getattr(document,body.section)):return {'id':artifact['id'],'reused':True}
        getattr(document,body.section).append(MinutesItem(content=text,source_segment_ids=ids))
        document=MeetingMinutes.model_validate(document.model_dump())
        validate_minutes_citations(document,{s['id'] for s in json.loads(meeting['segments'])})
        adopted.append({'key':key,'note_id':nid,'note_fingerprint':body.fingerprint,'section':body.section,'source_segment_ids':ids,'content':text})
        provenance.update(edited=True,adopted_notes=adopted,base_minutes_id=artifact['id'])
        aid=uid();db.execute('INSERT INTO artifacts(id,meeting_id,revision,kind,payload,provenance) VALUES(?,?,?,?,?,?)',
            (aid,mid,meeting['revision'],'meeting_minutes',encode(document.model_dump()),encode(provenance)))
        return {'id':aid,'reused':False}
