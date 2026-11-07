"""Private, explicitly authorized audio and human references. Never seeds gold from ASR."""
import io,json,math,os,sqlite3,subprocess,tempfile,threading,time,uuid,wave
from pathlib import Path
from .quality_evaluation import digest,distance,normalize,file_digest,rubric_digest

MAX_UPLOAD=20*1024*1024
TAGS={'clean','terms','numbers','noise','overlap','long_boundary'}


def normalize_audio(payload):
    if not payload or len(payload)>MAX_UPLOAD:raise ValueError('请选择不超过 20 MB 的短音频片段')
    with tempfile.TemporaryDirectory(prefix='tingji-quality-import-') as folder:
        source=Path(folder)/'source';target=Path(folder)/'normalized.wav';source.write_bytes(payload)
        try:subprocess.run(['ffmpeg','-nostdin','-v','error','-protocol_whitelist','file,pipe','-i',str(source),'-vn','-map_metadata','-1','-ac','1','-ar','16000','-c:a','pcm_s16le','-t','121',str(target)],capture_output=True,check=True,timeout=30)
        except (OSError,subprocess.SubprocessError):raise ValueError('无法读取该音频，请选择可播放的短片段') from None
        result=target.read_bytes()
    with wave.open(io.BytesIO(result),'rb') as audio:
        duration=audio.getnframes()/16000
        if not 1<=duration<=120:raise ValueError('样本须为 1～120 秒片段，不会自动截短长会议')
    return result,duration


class QualitySamples:
    def __init__(self,root):
        self.root=Path(root)/'quality-samples';self.db_path=self.root/'samples.sqlite3';self.lock=threading.RLock();self.available=True
        try:
            self.root.mkdir(mode=0o700,exist_ok=True)
            if self.root.is_symlink():raise ValueError('样本目录不允许使用链接')
            with self.connect() as db:db.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS samples(id TEXT PRIMARY KEY,audio_sha TEXT,duration REAL,tags TEXT,version INTEGER DEFAULT 0,created REAL,rights_confirmed INTEGER);
            CREATE TABLE IF NOT EXISTS sources(sample_id TEXT PRIMARY KEY,provenance TEXT);
            CREATE TABLE IF NOT EXISTS annotations(sample_id TEXT,version INTEGER,reference TEXT,fingerprint TEXT,origin TEXT,reviewed_at REAL,PRIMARY KEY(sample_id,version));
            CREATE TABLE IF NOT EXISTS datasets(id TEXT PRIMARY KEY,fingerprint TEXT,members TEXT,created REAL);
            CREATE TABLE IF NOT EXISTS reports(id TEXT PRIMARY KEY,dataset_id TEXT,payload TEXT,created REAL);''')
        except (OSError,ValueError,sqlite3.Error):self.available=False
    def connect(self):
        db=sqlite3.connect(self.db_path,timeout=3);db.row_factory=sqlite3.Row;return db
    def require(self):
        if not self.available:raise OSError('私有样本库暂不可用')
    def audio_path(self,sid):
        if not isinstance(sid,str) or len(sid)!=32 or any(c not in '0123456789abcdef' for c in sid):raise LookupError('样本不存在')
        path=self.root/(sid+'.wav')
        if path.is_symlink():raise ValueError('音频证据已变化')
        return path
    def verified_audio(self,sid):
        self.require()
        with self.connect() as db:row=db.execute('SELECT audio_sha FROM samples WHERE id=?',(sid,)).fetchone()
        if not row:raise LookupError('样本不存在')
        path=self.audio_path(sid)
        if file_digest(path)!=row['audio_sha']:raise ValueError('音频证据已变化')
        return path
    def get(self,sid):
        self.require()
        with self.connect() as db:
            row=db.execute('SELECT * FROM samples WHERE id=?',(sid,)).fetchone()
            if not row:raise LookupError('样本不存在')
            annotation=db.execute('SELECT * FROM annotations WHERE sample_id=? AND version=?',(sid,row['version'])).fetchone()
        with self.connect() as db:source=db.execute('SELECT provenance FROM sources WHERE sample_id=?',(sid,)).fetchone()
        return {'source':json.loads(source[0]) if source else {'kind':'user_upload'},'id':sid,'duration':row['duration'],'tags':json.loads(row['tags']),'version':row['version'],'state':'human_reviewed' if annotation else 'draft',
            'reference':annotation['reference'] if annotation else '', 'reference_origin':annotation['origin'] if annotation else None,
            'reference_fingerprint':annotation['fingerprint'] if annotation else None,'source_scope':'user_attested_real_speech','rights_confirmed':bool(row['rights_confirmed'])}
    def listing(self):
        self.require()
        with self.connect() as db:ids=[r[0] for r in db.execute('SELECT id FROM samples ORDER BY created DESC LIMIT 100')]
        return [{k:v for k,v in self.get(sid).items() if k not in ('reference',)} for sid in ids]
    def summary(self):
        self.require()
        with self.connect() as db:
            total=db.execute('SELECT COUNT(*) FROM samples').fetchone()[0];reviewed=db.execute('SELECT COUNT(*) FROM samples WHERE version>0').fetchone()[0];datasets=db.execute('SELECT COUNT(*) FROM datasets').fetchone()[0]
        return {'scope':'private_user_attested_real_speech','total':total,'human_reviewed':reviewed,'draft':total-reviewed,'datasets':datasets,'models_started':False}
    def add(self,payload,tags,rights_confirmed,real_speech):
        self.require()
        if not rights_confirmed or not real_speech:raise ValueError('请先确认录音使用权限和真实语音来源')
        if not tags or not set(tags).issubset(TAGS):raise ValueError('请选择有效样本类别')
        audio,duration=normalize_audio(payload);sid=uuid.uuid4().hex;path=self.audio_path(sid)
        with self.lock:
            with path.open('xb') as handle:os.chmod(path,0o600);handle.write(audio);handle.flush();os.fsync(handle.fileno())
            try:
                with self.connect() as db:db.execute('INSERT INTO samples VALUES(?,?,?,?,?,?,?)',(sid,file_digest(path),duration,json.dumps(sorted(set(tags))),0,time.time(),1))
            except BaseException:path.unlink();raise
        return self.get(sid)
    def record_source(self,sid,provenance):
        self.require()
        allowed={'kind','meeting_id','revision','segment_ids','start','end'}
        if set(provenance)-allowed or provenance.get('kind')!='selected_meeting':raise ValueError('来源证据格式无效')
        if not isinstance(provenance.get('meeting_id'),str) or len(provenance['meeting_id'])!=32 or any(c not in '0123456789abcdef' for c in provenance['meeting_id']):raise ValueError('来源会议无效')
        if type(provenance.get('revision')) is not int or provenance['revision']<1:raise ValueError('来源版本无效')
        start=provenance.get('start');end=provenance.get('end')
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in (start,end)) or not 0<=start<end<=86400:raise ValueError('来源范围无效')
        ids=provenance.get('segment_ids')
        if not isinstance(ids,list) or not 1<=len(ids)<=100 or any(not isinstance(s,str) or not s or len(s)>200 for s in ids):raise ValueError('来源片段无效')
        with self.lock,self.connect() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT version FROM samples WHERE id=?',(sid,)).fetchone()
            if not row or row['version']!=0:raise ValueError('来源须在首次标注前固定')
            db.execute('INSERT INTO sources VALUES(?,?)',(sid,json.dumps(provenance)))

    def annotate(self,sid,expected_version,reference,audio_reviewed,origin='human_audio_review'):
        self.require();reference=reference.strip()
        if origin not in ('human_audio_review','user_verified_existing_transcript'):raise ValueError('标注来源无效')
        if not audio_reviewed or not reference or not normalize(reference) or len(reference)>2000:raise ValueError('请回听并手工填写有效参考文字，不以模型结果直接充当真值')
        with self.lock,self.connect() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT * FROM samples WHERE id=?',(sid,)).fetchone()
            if not row:raise LookupError('样本不存在')
            if row['version']!=expected_version:raise ValueError('标注版本已变化，请刷新后重新核对')
            if file_digest(self.audio_path(sid))!=row['audio_sha']:raise ValueError('音频证据已变化，请重新导入样本')
            version=expected_version+1;fingerprint=digest({'audio':row['audio_sha'],'reference':reference,'version':version,'origin':origin})
            db.execute('INSERT INTO annotations VALUES(?,?,?,?,?,?)',(sid,version,reference,fingerprint,origin,time.time()))
            db.execute('UPDATE samples SET version=? WHERE id=?',(version,sid))
        return self.get(sid)
    def freeze(self,members):
        self.require()
        if not members or len(members)>100 or len({m['id'] for m in members})!=len(members):raise ValueError('请选择不重复的已标注样本，最多 100 条')
        with self.lock,self.connect() as db:
            db.execute('BEGIN IMMEDIATE');pinned=[]
            for item in members:
                row=db.execute('SELECT s.*,a.reference,a.fingerprint,a.origin FROM samples s JOIN annotations a ON s.id=a.sample_id AND s.version=a.version WHERE s.id=? AND s.version=?',(item['id'],item['version'])).fetchone()
                if not row or not row['rights_confirmed']:raise ValueError('存在未回听确认或版本已变化的样本')
                if file_digest(self.audio_path(row['id']))!=row['audio_sha']:raise ValueError('音频证据已变化')
                pinned.append({'id':row['id'],'version':row['version'],'reference':row['reference'],'reference_fingerprint':row['fingerprint'],'annotation_origin':row['origin'],'audio_sha':row['audio_sha'],'tags':json.loads(row['tags'])})
            pinned.sort(key=lambda r:r['id']);fingerprint=digest({'scope':'private_user_attested_real_speech','members':pinned});did=uuid.uuid4().hex
            db.execute('INSERT INTO datasets VALUES(?,?,?,?)',(did,fingerprint,json.dumps(pinned,ensure_ascii=False),time.time()))
        return {'id':did,'fingerprint':fingerprint,'samples':len(pinned),'scope':'private_user_attested_real_speech'}
    def dataset(self,did):
        self.require()
        with self.connect() as db:row=db.execute('SELECT * FROM datasets WHERE id=?',(did,)).fetchone()
        if not row:raise LookupError('固定集不存在')
        members=json.loads(row['members'])
        if row['fingerprint']!=digest({'scope':'private_user_attested_real_speech','members':members}):raise ValueError('固定集指纹不一致')
        for member in members:
            if file_digest(self.audio_path(member['id']))!=member['audio_sha']:raise ValueError('固定集音频证据已变化')
        return {'id':did,'fingerprint':row['fingerprint'],'members':members}
    def datasets(self):
        self.require()
        with self.connect() as db:rows=db.execute('SELECT id,fingerprint,members FROM datasets ORDER BY created DESC LIMIT 30').fetchall()
        result=[]
        with self.connect() as db:
            for row in rows:
                members=json.loads(row['members']);current=all((db.execute('SELECT version FROM samples WHERE id=?',(m['id'],)).fetchone() or [None])[0]==m['version'] for m in members)
                result.append({'id':row['id'],'fingerprint':row['fingerprint'],'samples':len(members),'current_annotations':current})
        return result
    def rubric(self):return digest({'text_rubric':rubric_digest(),'private_rubric':file_digest(Path(__file__))})
    def score(self,did,outputs,subject):
        data=self.dataset(did);members=data['members']
        if not isinstance(outputs,dict) or set(outputs)!={m['id'] for m in members}:raise ValueError('候选结果须完整覆盖固定集，失败样本不能跳过')
        rows=[]
        for member in members:
            text=outputs[member['id']]
            if not isinstance(text,str) or len(text)>8000:raise ValueError('候选文字类型或长度无效')
            ref=normalize(member['reference']);hyp=normalize(text);errors=distance(ref,hyp)
            rows.append({'id':member['id'],'version':member['version'],'reference_characters':len(ref),'character_errors':errors,'cer':errors/len(ref),'output_fingerprint':digest(text)})
        total=sum(r['reference_characters'] for r in rows);errors=sum(r['character_errors'] for r in rows)
        report={'schema_version':1,'mode':'private_real_speech_asr','dataset_fingerprint':data['fingerprint'],'rubric_fingerprint':self.rubric(),'subject':subject,'rows':rows,'metrics':{'samples':len(rows),'reference_characters':total,'character_errors':errors,'micro_cer':errors/total},'reference_origin':'user_attested_human_audio_review','annotation_methods':sorted({m.get('annotation_origin','legacy_human_audio_review') for m in members}),'created':time.time()}
        report['report_fingerprint']=digest(report)
        return report
    def save_report(self,did,report):
        data=self.dataset(did)
        if report.get('dataset_fingerprint')!=data['fingerprint'] or report.get('report_fingerprint')!=digest({k:v for k,v in report.items() if k!='report_fingerprint'}):raise ValueError('结果与固定集不一致')
        rid=uuid.uuid4().hex
        with self.connect() as db:db.execute('INSERT INTO reports VALUES(?,?,?,?)',(rid,did,json.dumps(report,ensure_ascii=False),time.time()))
        return rid

    def reports(self):
        self.require()
        with self.connect() as db:rows=db.execute('SELECT id,dataset_id,payload FROM reports ORDER BY created DESC LIMIT 30').fetchall()
        current={r['id']:r['current_annotations'] for r in self.datasets()}
        result=[]
        for row in rows:
            value=json.loads(row['payload'])
            result.append({'id':row['id'],'dataset_id':row['dataset_id'],'metrics':value['metrics'],'annotation_methods':value.get('annotation_methods',[]),'subject':{k:v for k,v in value['subject'].items() if k in ('engine','model_label','generation')},'current_annotations':current.get(row['dataset_id'],False),'current_rubric':value.get('rubric_fingerprint')==self.rubric()})
        return result
