"""Local, versioned retrieval units and sqlite-vec cosine search."""
from . import minutes_versions
import hashlib,json,math,re,sqlite3,urllib.request,urllib.error
from .analysis import OpenAICompatibleProvider
from .db import encode
from .local_embedding import local_embedding,MANIFEST,MODEL_ID

UNIT_VERSION='retrieval-units-v2'
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise RuntimeError('本地向量接口不允许重定向')

def digest(value):return hashlib.sha256(encode(value).encode()).hexdigest()

def embeddings(profile,model,texts,dimension=None):
    if profile['provider_type']!='local':raise ValueError('向量检索首版仅支持本机模型')
    provider=OpenAICompatibleProvider(profile)
    request=urllib.request.Request(provider.base_url+'/embeddings',data=encode({'model':model,'input':texts}).encode(),headers=provider._headers())
    try:
        with urllib.request.build_opener(NoRedirect()).open(request,timeout=60) as response:payload=json.load(response)
    except urllib.error.HTTPError as error:raise RuntimeError(f'向量模型接口返回 HTTP {error.code}，请确认模型支持 Embedding') from error
    except (urllib.error.URLError,TimeoutError) as error:raise RuntimeError('无法连接本机向量模型服务') from error
    rows=payload.get('data',[])
    if not isinstance(rows,list) or len(rows)!=len(texts):raise ValueError('向量接口返回数量不一致')
    by_index={}
    for row in rows:
        index=row.get('index');vector=row.get('embedding')
        if not isinstance(index,int) or isinstance(index,bool) or index in by_index or not 0<=index<len(texts):raise ValueError('向量接口索引无效')
        if not isinstance(vector,list) or not 1<=len(vector)<=4096 or any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in vector):raise ValueError('向量数据无效')
        if dimension is not None and len(vector)!=dimension:raise ValueError('向量维度已变化，请重新配置并重建索引')
        norm=math.sqrt(sum(x*x for x in vector))
        if not math.isfinite(norm) or norm==0:raise ValueError('向量不能为零或溢出')
        by_index[index]=[x/norm for x in vector]
    if len({len(v) for v in by_index.values()})!=1:raise ValueError('向量维度不一致')
    return [by_index[i] for i in range(len(texts))]

class Retrieval:
    def __init__(self,store):
        self.store=store
        with store.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS retrieval_units (
              id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL, content TEXT NOT NULL, metadata TEXT NOT NULL,
              fingerprint TEXT NOT NULL, embedding BLOB, identity TEXT NOT NULL DEFAULT '');
              CREATE TABLE IF NOT EXISTS retrieval_settings (id INTEGER PRIMARY KEY CHECK(id=1), config TEXT NOT NULL);
              CREATE INDEX IF NOT EXISTS retrieval_meeting ON retrieval_units(meeting_id);''')
    def config(self):
        row=self.store.one('SELECT config FROM retrieval_settings WHERE id=1')
        return json.loads(row['config']) if row else None
    def profile(self,config):
        if config.get("unit_version")!=UNIT_VERSION:raise ValueError("检索单元规则已升级，请重新测试保存向量模型配置，再更新索引")
        if config.get('provider')=='builtin':
            if config.get('manifest')!=digest(MANIFEST):raise ValueError('内置模型规则变化，请重新启用并重建索引')
            if not local_embedding().installed():raise ValueError('请先安装内置中文检索模型')
            return {'provider_type':'builtin'}
        profile=self.store.one('SELECT * FROM llm_configs WHERE id=?',(config['profile_id'],))
        if not profile or profile['provider_type']!='local':raise ValueError('本地向量模型配置不存在，请重新配置')
        provider=OpenAICompatibleProvider(profile)
        if provider.base_url!=config['base_url']:raise ValueError('向量模型服务地址已变化，请重新配置')
        return profile
    def configure(self,profile,model):
        if profile['provider_type']!='local':raise ValueError('向量检索首版仅支持本机模型')
        if not model.strip():raise ValueError('请输入 Embedding 模型名称')
        if self.store.one("SELECT 1 FROM jobs WHERE kind='retrieval_index' AND state IN ('running','queued')"):raise ValueError('索引任务正在处理')
        prefixes={'document_prefix':'search_document: ','query_prefix':'search_query: '} if 'nomic-embed-text' in model.lower() else {'document_prefix':'','query_prefix':''}
        vectors=embeddings(profile,model,[prefixes['document_prefix']+'会议资料检索配置测试'])
        config={'profile_id':profile['id'],'base_url':OpenAICompatibleProvider(profile).base_url,'model':model,'dimension':len(vectors[0]),'unit_version':UNIT_VERSION,**prefixes}
        config['identity']=digest(config)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM jobs WHERE kind='retrieval_index' AND state IN ('running','queued')").fetchone():raise ValueError('索引正在建立，请完成或取消后修改模型')
            db.execute('INSERT OR REPLACE INTO retrieval_settings(id,config) VALUES(1,?)',(encode(config),))
            db.execute("UPDATE retrieval_units SET embedding=NULL,identity='' WHERE identity!=?",(config['identity'],))
        return config
    def configure_builtin(self):
        if self.store.one("SELECT 1 FROM jobs WHERE kind='retrieval_index' AND state IN ('running','queued')"):raise ValueError('索引正在建立，请先完成或取消')
        runtime=local_embedding();runtime.encode(['会议资料检索配置测试'])
        config={'provider':'builtin','model':MODEL_ID,'dimension':512,'unit_version':UNIT_VERSION,'manifest':digest(MANIFEST)}
        config['identity']=digest(config)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM jobs WHERE kind='retrieval_index' AND state IN ('running','queued')").fetchone():raise ValueError('索引正在建立，请先完成或取消')
            db.execute('INSERT OR REPLACE INTO retrieval_settings(id,config) VALUES(1,?)',(encode(config),))
            db.execute("UPDATE retrieval_units SET embedding=NULL,identity='' WHERE identity!=?",(config['identity'],))
        return config
    def vectorize(self,config,texts,query=False):
        profile=self.profile(config)
        if config.get('provider')=='builtin':return local_embedding().encode(texts,query=query)
        prefix=config.get('query_prefix' if query else 'document_prefix','')
        return embeddings(profile,config['model'],[prefix+text for text in texts],config['dimension'])
    def units(self,db):
        units=[]
        for meeting in db.execute('SELECT * FROM meetings ORDER BY created,id').fetchall():
            mid=meeting['id'];revision=meeting['revision'];segments=json.loads(meeting['segments']);by_id={s['id']:s for s in segments}
            def add(key,kind,text,ids,start=None,end=None,artifact_id=None,note_scope=None):
                if not text.strip():return
                # Bound model input, while retaining every text part and its provenance.
                for offset in range(0,len(text),700):
                    part=text[offset:offset+700]
                    uid=mid+':'+key+':'+str(offset)
                    meta={'meeting_id':mid,'title':meeting['title'],'date':meeting['created'][:10],'revision':revision,
                          'kind':kind,'source_segment_ids':ids,'start':start,'end':end,'artifact_id':artifact_id,'note_scope':note_scope}
                    fp=digest([UNIT_VERSION,revision,kind,part,ids,start,end,artifact_id,note_scope])
                    units.append((uid,mid,part,encode(meta),fp))
            group=[];size=0
            def flush():
                if group:add('transcript:'+group[0]['id'],'transcript','\n'.join(s['speaker']+'：'+s['text'] for s in group),[s['id'] for s in group],group[0]['start'],group[-1]['end'])
            for segment in segments:
                if group and (size+len(segment['text'])>650 or segment['end']-group[0]['start']>90):flush();group=[];size=0
                group.append(segment);size+=len(segment['text'])
            flush()
            note_rows=db.execute('SELECT * FROM meeting_notes WHERE meeting_id=?',(mid,)).fetchall()
            notes_by_id={note['id']:note for note in note_rows}
            from .meeting_flow import dispositions,artifact_current
            reviewed=dispositions(db,mid,revision)
            for note in note_rows:
                add('note:'+note['id'],'note',note['raw_text'],[],note['captured_at'],note['captured_at'],note_scope=note['note_scope'])
                ids=json.loads(note['source_segment_ids'])
                if note['status']=='accepted' and note['expansion_revision']==revision and ids and set(ids)<=by_id.keys():
                    add('expanded:'+note['id'],'expanded_note',note['accepted_text'],ids,min(by_id[i]['start'] for i in ids),max(by_id[i]['end'] for i in ids),note_scope=note['note_scope'])
            for nid,item in reviewed.items():
                ids=item['source_segment_ids']
                if not item['stale'] and item['decision']=='meeting' and ids and set(ids)<=by_id.keys():
                    add('reviewed:'+nid,'expanded_note',item['content'],ids,min(by_id[i]['start'] for i in ids),max(by_id[i]['end'] for i in ids),artifact_id=item['review_key'],note_scope='meeting')
            artifact=minutes_versions.selected(db,mid,revision)
            if artifact and not artifact_current(db,mid,json.loads(artifact['provenance'] or '{}')):artifact=None
            if artifact:
                from .notes import fingerprint
                adopted=json.loads(artifact['provenance'] or '{}').get('adopted_notes',[])
                if any(item.get('note_id') not in notes_by_id or item.get('note_fingerprint')!=fingerprint(notes_by_id[item['note_id']]) for item in adopted):artifact=None
            # Only user-saved minutes participate; raw AI candidates are not trusted retrieval facts.
            if artifact and json.loads(artifact['provenance']).get('edited'):
                document=json.loads(artifact['payload'])
                for section in ('overview','conclusions','decisions','action_items','open_questions','summary'):
                    for index,item in enumerate(document.get(section,[])):
                        ids=item.get('source_segment_ids',[])
                        if not ids or not set(ids)<=by_id.keys():continue
                        add(f"minutes:{artifact['id']}:{section}:{index}",'minutes',item['content'],ids,min(by_id[i]['start'] for i in ids),max(by_id[i]['end'] for i in ids),artifact['id'])
        return units
    def sync(self):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            units=self.units(db);wanted={u[0] for u in units}
            for row in db.execute('SELECT id FROM retrieval_units').fetchall():
                if row['id'] not in wanted:db.execute('DELETE FROM retrieval_units WHERE id=?',(row['id'],))
            for unit in units:
                db.execute('''INSERT INTO retrieval_units(id,meeting_id,content,metadata,fingerprint) VALUES(?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET content=excluded.content,metadata=excluded.metadata,fingerprint=excluded.fingerprint,
                    embedding=CASE WHEN retrieval_units.fingerprint=excluded.fingerprint THEN retrieval_units.embedding ELSE NULL END,
                    identity=CASE WHEN retrieval_units.fingerprint=excluded.fingerprint THEN retrieval_units.identity ELSE '' END''',unit)
        return len(units)
    def status(self):
        count=self.sync();config=self.config()
        embedded=self.store.one('SELECT count(*) AS n FROM retrieval_units WHERE embedding IS NOT NULL AND identity=?',(config['identity'] if config else '',))['n']
        job=self.store.one("SELECT id,state,stage,progress,error FROM jobs WHERE kind='retrieval_index' ORDER BY created DESC,rowid DESC LIMIT 1")
        try:
            import sqlite_vec
            with self.store.connect() as db:
                db.enable_load_extension(True)
                try:sqlite_vec.load(db)
                finally:db.enable_load_extension(False)
            available=True;error=''
        except (ImportError,AttributeError,sqlite3.Error) as e:available=False;error='SQLite-vec 未就绪，请安装项目依赖并使用支持扩展的 Python'
        profile_error=''
        if config:
            try:self.profile(config)
            except ValueError as e:profile_error=str(e)
            if config.get('provider')=='builtin' and not local_embedding().status()['runtime_ready']:profile_error='内置模型运行依赖未安装，请重新运行 scripts/setup.sh'
        return {'config':config,'units':count,'indexed':embedded,'pending':count-embedded,'vec_ready':available,'error':error or profile_error,'job':job,'builtin':local_embedding().status()}
    def build(self,worker,job):
        config=json.loads(job['config']);jid=job['id'];profile=self.profile(config)
        if self.config()!=config:raise ValueError('索引模型已变化，请新建任务')
        self.sync();pending=self.store.all('SELECT * FROM retrieval_units WHERE embedding IS NULL OR identity!=?',(config['identity'],))
        import sqlite_vec
        for offset in range(0,len(pending),16):
            worker.check(jid);worker.update(jid,f'建立本地索引 {min(offset+16,len(pending))}/{len(pending)}',offset/max(1,len(pending)))
            batch=pending[offset:offset+16];vectors=self.vectorize(config,[row['content'] for row in batch])
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE');worker.check(jid)
                if self.config()!=config:raise ValueError('索引配置已变化')
                for row,vector in zip(batch,vectors):
                    db.execute('UPDATE retrieval_units SET embedding=?,identity=? WHERE id=? AND fingerprint=?',(sqlite_vec.serialize_float32(vector),config['identity'],row['id'],row['fingerprint']))
                worker.check(jid)
        self.sync();worker.update(jid,'检索索引建立完成',1)
    def search(self,query,mode='keyword',limit=20,meeting_id=None,kind=None,include_personal=True):
        self.sync();config=self.config();query=query.strip()
        tokens=re.findall(r'[\w]+',query.lower())
        vector=None
        if mode!='keyword':
            if not config:raise ValueError('请先配置本地向量模型并建立索引')
            indexed=self.store.one('SELECT count(*) AS n FROM retrieval_units WHERE embedding IS NOT NULL AND identity=?',(config['identity'],))['n']
            if mode=='semantic' and not indexed:raise ValueError('尚无可用语义索引，请先更新索引')
            if indexed:vector=self.vectorize(config,[query],query=True)[0]
        # Final sync also excludes edits/deletions which happened during the embedding request.
        self.sync()
        if vector and self.config()!=config:raise ValueError('模型配置已变化，请重试')
        with self.store.connect() as db:
            filters=[];args=[]
            if meeting_id:filters.append('meeting_id=?');args.append(meeting_id)
            if kind:filters.append("json_extract(metadata,'$.kind')=?");args.append(kind)
            if not include_personal:filters.append("COALESCE(json_extract(metadata,'$.note_scope'),'')!='personal'")
            where=' WHERE '+' AND '.join(filters) if filters else ''
            rows=[dict(row) for row in db.execute('SELECT id,content,metadata,fingerprint FROM retrieval_units'+where,args)]
            lexical=[]
            for row in rows:
                text=(json.loads(row['metadata'])['title']+' '+row['content']).lower()
                score=sum(text.count(token) for token in tokens)
                if score:lexical.append((row['id'],score))
            lexical.sort(key=lambda pair:(-pair[1],pair[0]))
            semantic=[]
            if vector:
                import sqlite_vec
                db.enable_load_extension(True)
                try:sqlite_vec.load(db)
                finally:db.enable_load_extension(False)
                conditions=filters+["embedding IS NOT NULL","identity=?"]
                semantic=[(r['id'],r['distance']) for r in db.execute('SELECT id,vec_distance_cosine(embedding,?) AS distance FROM retrieval_units WHERE '+' AND '.join(conditions)+' ORDER BY distance,id LIMIT 100', [sqlite_vec.serialize_float32(vector)]+args+[config['identity']])]
            scores={};distances=dict(semantic)
            for ranking in ([lexical] if mode=='keyword' else [semantic] if mode=='semantic' else [lexical,semantic]):
                for rank,(key,_) in enumerate(ranking[:100]):scores[key]=scores.get(key,0)+1/(60+rank+1)
            by_id={row['id']:row for row in rows};results=[]
            for key in sorted(scores,key=lambda key:(-scores[key],key))[:limit]:
                row=by_id[key];results.append({'id':key,'content':row['content'],'fingerprint':row['fingerprint'],**json.loads(row['metadata']),
                    'match':('hybrid' if key in distances and any(x[0]==key for x in lexical) else 'semantic' if key in distances else 'keyword')})
        return {'results':results,'mode':mode,'pending':self.status()['pending']}
    def resolve(self,key,fingerprint):
        self.sync();row=self.store.one('SELECT * FROM retrieval_units WHERE id=?',(key,))
        if not row or row['fingerprint']!=fingerprint:raise ValueError('搜索结果已变化，请重新搜索')
        return {'content':row['content'],**json.loads(row['metadata'])}
