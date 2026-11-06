"""Offline, manifest-bound data backups. Never imports the app or starts workers/models."""
import fcntl,hashlib,json,os,re,shutil,sqlite3,stat,tempfile,time,sys
from contextlib import ExitStack,contextmanager,closing,nullcontext
from pathlib import Path,PurePosixPath
from .maintenance import DataLease

FORMAT_VERSION=1
ID=re.compile(r'^[0-9a-f]{32}$')
SHA=re.compile(r'^[0-9a-f]{64}$')
EXCLUDED={'maintenance.lock','service.lock','worker.lock','.restore-report.json'}
MARKERS={'.backup-payload','.restore-in-progress'}

class BackupError(ValueError):pass


def sync_dir(path):
    fd=os.open(path,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)


def write_json(path,value):
    with path.open('x',encoding='utf-8') as out:
        os.chmod(path,0o600);json.dump(value,out,ensure_ascii=False,sort_keys=True,indent=2)
        out.flush();os.fsync(out.fileno())


def read_metadata(path):
    if path.stat().st_size>4*1024*1024:raise ValueError('metadata too large')
    return json.loads(path.read_text())


def require_space(path,size):
    parent=path
    while not parent.exists():parent=parent.parent
    if shutil.disk_usage(parent).free<size+64*1024*1024:raise BackupError('目标磁盘可用空间不足，未开始复制')


def signature(info):return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)


def is_dataless(info):
    return bool(getattr(info,'st_flags',0)&getattr(stat,'SF_DATALESS',0x40000000 if sys.platform=='darwin' else 0))


def scan(root,sidecars=(),allow_dataless=False):
    files={};directories=[];skip=set(sidecars)
    for folder,dirs,names in os.walk(root,followlinks=False):
        parent=Path(folder);relative=parent.relative_to(root)
        for name in list(dirs):
            path=parent/name;rel=path.relative_to(root).as_posix()
            if path.is_symlink():raise BackupError('数据中存在符号链接，未跟随或复制')
            if relative==Path('.') and name=='services':dirs.remove(name);continue
            directories.append(rel)
        for name in names:
            path=parent/name;rel=path.relative_to(root).as_posix();info=path.lstat()
            if not stat.S_ISREG(info.st_mode):raise BackupError('数据中存在非普通文件，未执行复制')
            if (relative==Path('.') and name in EXCLUDED|MARKERS) or rel in skip:continue
            if not allow_dataless and is_dataless(info):raise BackupError('数据含尚未下载到本机的云端占位文件，请先在 Finder 中下载并保留本地副本，再运行 preflight；未跳过文件或发布备份')
            files[rel]=signature(info)
    return files,sorted(directories)


def preflight(source):
    root=Path(source).resolve()
    if not root.is_dir() or not (root/'meetings.sqlite3').is_file():raise BackupError('源数据目录无效')
    files,_=scan(root,allow_dataless=True)
    placeholders=sum(is_dataless((root/rel).lstat()) for rel in files)
    return {'status':'needs_download' if placeholders else 'ready_for_offline_backup',
            'file_count':len(files),'estimated_bytes':sum(info[2] for info in files.values()),
            'cloud_placeholder_count':placeholders,'content_read':False,'service_state_checked':False}


def sqlite_files(root,files):
    result=[]
    for rel in files:
        with (root/rel).open('rb') as source:
            if source.read(16)==b'SQLite format 3\0':result.append(rel)
    if 'meetings.sqlite3' not in result:raise BackupError('未找到有效的会议数据库，未生成备份')
    return sorted(result)


def db_connect(path):
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
    db.execute('PRAGMA query_only=ON');db.execute('PRAGMA trusted_schema=OFF')
    return db


def db_inventory(db):
    if db.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:raise BackupError('数据库完整性检查未通过')
    if db.execute('PRAGMA foreign_key_check').fetchone() is not None:raise BackupError('数据库外键检查未通过')
    schema=db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
    tables={}
    for kind,name,*_ in schema:
        if kind=='table':tables[name]=db.execute('SELECT count(*) FROM "'+name.replace('"','""')+'"').fetchone()[0]
    return {'user_version':db.execute('PRAGMA user_version').fetchone()[0],
            'schema_sha256':hashlib.sha256(json.dumps(schema,ensure_ascii=False).encode()).hexdigest(),'tables':tables,'integrity':'ok'}


def category(rel,database=False):
    parts=PurePosixPath(rel).parts
    if database:return 'database'
    if len(parts)>1 and ID.fullmatch(parts[0]) and parts[-1]=='original':return 'original_audio'
    if parts[0]=='recordings' and Path(rel).suffix in ('.pcm','.part','.wav','.webm','.m4a'):return 'recording_audio'
    if Path(rel).suffix=='.json':return 'metadata'
    if len(parts)>1 and ID.fullmatch(parts[0]):return 'derived'
    return 'other'


def copy_file(source,target,expected=None):
    fd=os.open(source,os.O_RDONLY|os.O_NOFOLLOW)
    digest=hashlib.sha256();size=0
    with os.fdopen(fd,'rb') as src:
        before=signature(os.fstat(src.fileno()))
        if expected is not None and before!=expected:raise BackupError('源文件已变化，未发布备份')
        target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        dst_fd=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        with os.fdopen(dst_fd,'wb') as out:
            while block:=src.read(1024*1024):digest.update(block);out.write(block);size+=len(block)
            out.flush();os.fsync(out.fileno())
        if signature(os.fstat(src.fileno()))!=before:raise BackupError('源文件复制期间变化，未发布结果')
    return {'size':size,'sha256':digest.hexdigest()}


def hash_file(path):
    digest=hashlib.sha256();size=0
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as file:
        before=signature(os.fstat(file.fileno()))
        if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):raise BackupError('文件类型不受支持')
        while block:=file.read(1024*1024):digest.update(block);size+=len(block)
        if signature(os.fstat(file.fileno()))!=before:raise BackupError('校验期间文件变化')
    return {'size':size,'sha256':digest.hexdigest()}


def logical_inventory(root,entries):
    files={entry['path']:entry for entry in entries}
    result={'meeting_count':0,'artifact_count':0,'revision_count':0,'note_count':0,'delivery_count':0,
            'recording_session_count':0,'missing_original_count':0,'invalid_meeting_id_count':0,
            'missing_asset_manifest_count':0,'invalid_asset_manifest_count':0,'asset_hash_mismatch_count':0,
            'invalid_session_count':0,'unclosed_recording_count':0,'pending_job_count':0}
    with closing(db_connect(root/'meetings.sqlite3')) as db:
        tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'meetings' not in tables:raise BackupError('会议数据库缺少会议表')
        for table,key in [('meetings','meeting_count'),('artifacts','artifact_count'),('revisions','revision_count'),('meeting_notes','note_count'),('minutes_deliveries','delivery_count')]:
            if table in tables:result[key]=db.execute('SELECT count(*) FROM '+table).fetchone()[0]
        if 'jobs' in tables:result['pending_job_count']=db.execute("SELECT count(*) FROM jobs WHERE state IN ('running','queued')").fetchone()[0]
        for (mid,) in db.execute('SELECT id FROM meetings'):
            if not isinstance(mid,str) or not ID.fullmatch(mid):result['invalid_meeting_id_count']+=1;continue
            original=files.get(mid+'/original')
            if not original:result['missing_original_count']+=1
            asset=files.get(mid+'/asset.json')
            if not asset:result['missing_asset_manifest_count']+=1;continue
            try:
                value=read_metadata(root/asset['path'])
                if not isinstance(value,dict):raise ValueError()
                if original and (value.get('sha256') not in (None,original['sha256']) or value.get('size') not in (None,original['size'])):result['asset_hash_mismatch_count']+=1
            except (OSError,ValueError):result['invalid_asset_manifest_count']+=1
    for rel in files:
        if re.fullmatch(r'recordings/[0-9a-f]{32}/session\.json',rel):
            result['recording_session_count']+=1
            try:
                value=read_metadata(root/rel)
                if value.get('state') in ('recording','paused','starting','interrupted'):result['unclosed_recording_count']+=1
            except (OSError,ValueError,AttributeError):result['invalid_session_count']+=1
    return result


@contextmanager
def offline(root):
    if (root/'.restore-in-progress').exists() or (root/'.backup-payload').exists():raise BackupError('源目录尚未就绪或属于备份载荷')
    with DataLease(root,exclusive=True) as lease,ExitStack() as locks:
        for name in ('service.lock','worker.lock'):
            handle=locks.enter_context((root/name).open('a'))
            try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError:raise BackupError('服务或工作进程仍在运行，请先安全停止后备份') from None
        # Fresh native metadata can indicate a surviving helper after an abnormal service exit.
        for path in (root/'recordings').glob('*/capture.json'):
            try:value=read_metadata(path)
            except (OSError,ValueError):continue
            if isinstance(value,dict) and value.get('state') in ('starting','recording','paused') and time.time()-path.stat().st_mtime<90:
                raise BackupError('发现新鲜的原生采集状态，请先确认采集已结束；未停止或操作录音')
        for path in (root/'recordings').glob('*/session.json'):
            try:value=read_metadata(path)
            except (OSError,ValueError):continue
            if isinstance(value,dict) and value.get('source')!='macos' and value.get('state') in ('starting','recording','paused') and time.time()-path.stat().st_mtime<90:
                raise BackupError('浏览器采集最近仍在报告，请先确认录音已结束')
        yield lease


def create_backup(source,output,code_version=None,maintenance=None):
    root=Path(source).resolve();output=Path(output).resolve()
    if not root.is_dir() or not (root/'meetings.sqlite3').is_file():raise BackupError('源数据目录无效')
    if output==root or output.is_relative_to(root) or root.is_relative_to(output):raise BackupError('备份目录必须位于源数据目录之外')
    if output.exists():raise BackupError('目标已存在，未覆盖任何内容')
    if maintenance is not None and (not isinstance(maintenance,DataLease) or not maintenance.exclusive or maintenance.root!=root or maintenance.handle is None or maintenance.handle.closed):raise BackupError('未持有当前数据目录的排他维护锁')
    owned=False
    try:
        with (offline(root) if maintenance is None else nullcontext(maintenance)),ExitStack() as stack:
            initial,_=scan(root);databases=sqlite_files(root,initial)
            sidecars={rel+suffix for rel in databases for suffix in ('-wal','-shm','-journal')}
            before,dirs=scan(root,sidecars)
            readers={};versions={};source_info={}
            for rel in databases:
                db=db_connect(root/rel);stack.callback(db.close);readers[rel]=db
                versions[rel]=db.execute('PRAGMA data_version').fetchone()[0];source_info[rel]=db_inventory(db)
            require_space(output.parent,sum(value[2] for value in before.values()))
            output.parent.mkdir(parents=True,exist_ok=True)
            output.mkdir(mode=0o700);owned=True
            (output/'.backup-in-progress').touch(mode=0o600)
            payload=output/'data';payload.mkdir(mode=0o700)
            for rel in dirs:(payload/rel).mkdir(parents=True,exist_ok=True,mode=0o700)
            entries=[]
            for rel in sorted(before):
                if rel in readers:
                    destination=payload/rel;destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                    with sqlite3.connect(destination) as target:
                        deadline=time.monotonic()+120
                        def progress(*_):
                            if time.monotonic()>deadline:raise BackupError('数据库快照超时，未发布备份')
                        readers[rel].backup(target,pages=256,progress=progress,sleep=.05)
                        target.execute('PRAGMA journal_mode=DELETE')
                    os.chmod(destination,0o600)
                    with destination.open('rb') as f:os.fsync(f.fileno())
                    with closing(db_connect(destination)) as db:
                        if db_inventory(db)!=source_info[rel]:raise BackupError('数据库快照逻辑不一致')
                    info=hash_file(destination)
                else:info=copy_file(root/rel,payload/rel,before[rel])
                entries.append({'path':rel,'kind':category(rel,rel in readers),**info})
            after,after_dirs=scan(root,sidecars)
            if before!=after or dirs!=after_dirs:raise BackupError('源目录发生变化，未发布备份')
            if any(readers[rel].execute('PRAGMA data_version').fetchone()[0]!=versions[rel] for rel in readers):raise BackupError('数据库发生并发写入，未发布备份')
            logical=logical_inventory(payload,entries)
            manifest={'format_version':FORMAT_VERSION,'status':'complete','created_at':time.time(),'source_root':str(root),
                'code_version':code_version,'scope':'data_root_only','external_models_included':False,'browser_storage_included':False,
                'files':entries,'directories':dirs,'databases':source_info,'logical_inventory':logical,
                'excluded_operational':sorted(EXCLUDED)+['services/','SQLite WAL/SHM/journal incorporated into snapshots']}
            for rel in sorted(dirs,key=lambda value:value.count('/'),reverse=True):sync_dir(payload/rel)
            write_json(payload/'.backup-payload',{'format_version':1})
            write_json(output/'manifest.json',manifest)
            (output/'.backup-in-progress').unlink();sync_dir(payload);sync_dir(output)
        return verify_backup(output)
    except BaseException:
        if owned:shutil.rmtree(output)
        raise


def safe_relative(value):
    if not isinstance(value,str) or not value or '\\' in value or '\0' in value:raise BackupError('清单路径无效')
    path=PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or path.as_posix()!=value or value=='.':raise BackupError('清单路径越界或不规范')
    if path.parts[0] in EXCLUDED|MARKERS or path.parts[0]=='services':raise BackupError('清单含运行期保留路径')
    return value


def load_manifest(bundle):
    if bundle.is_symlink() or (bundle/'.backup-in-progress').exists():raise BackupError('备份目录尚未完成或不受支持')
    path=bundle/'manifest.json'
    if path.is_symlink() or not path.is_file() or path.stat().st_size>16*1024*1024:raise BackupError('备份清单无效')
    try:value=json.loads(path.read_text())
    except (OSError,ValueError):raise BackupError('备份清单无法读取') from None
    if not isinstance(value,dict) or value.get('format_version')!=FORMAT_VERSION or value.get('status')!='complete':raise BackupError('不支持或未完成的备份格式')
    if not isinstance(value.get('files'),list) or not isinstance(value.get('directories'),list) or not isinstance(value.get('databases'),dict):raise BackupError('备份清单结构无效')
    if not isinstance(value.get('source_root'),str) or not Path(value['source_root']).is_absolute() or '\0' in value['source_root']:raise BackupError('备份来源记录无效')
    seen=set()
    for entry in value['files']:
        if not isinstance(entry,dict):raise BackupError('备份文件记录无效')
        rel=safe_relative(entry.get('path'))
        if rel in seen or type(entry.get('size')) is not int or entry['size']<0 or not isinstance(entry.get('sha256'),str) or not SHA.fullmatch(entry['sha256']):raise BackupError('备份文件记录冲突或无效')
        if entry.get('kind') not in ('database','original_audio','recording_audio','metadata','derived','other'):raise BackupError('文件类别不受支持')
        seen.add(rel)
    dirs=[safe_relative(rel) for rel in value['directories']]
    if len(set(dirs))!=len(dirs) or seen.intersection(dirs):raise BackupError('备份目录记录冲突')
    if {e['path'] for e in value['files'] if e['kind']=='database'}!=set(value['databases']) or 'meetings.sqlite3' not in value['databases']:raise BackupError('数据库清单不一致')
    return value


def verify_data(root,manifest):
    if root.is_symlink() or not root.is_dir():raise BackupError('备份数据目录无效')
    actual,dirs=scan(root)
    expected={entry['path'] for entry in manifest['files']}
    if set(actual)!=expected or dirs!=sorted(manifest['directories']):raise BackupError('文件或目录清单不一致，未通过校验')
    for entry in manifest['files']:
        if hash_file(root/entry['path'])!={'size':entry['size'],'sha256':entry['sha256']}:raise BackupError('备份文件校验失败')
    for rel,expected_db in manifest['databases'].items():
        with closing(db_connect(root/rel)) as db:
            if db_inventory(db)!=expected_db:raise BackupError('数据库校验不一致')
    logical=logical_inventory(root,manifest['files'])
    if logical!=manifest.get('logical_inventory'):raise BackupError('会议与文件逻辑清单不一致')
    return logical


def verify_backup(bundle):
    bundle=Path(bundle).absolute();manifest=load_manifest(bundle)
    logical=verify_data(bundle/'data',manifest)
    return {'status':'verified','format_version':1,'file_count':len(manifest['files']),
            'total_bytes':sum(e['size'] for e in manifest['files']),'database_count':len(manifest['databases']),
            'logical_inventory':logical,'audio_content_validated':False}


def restore_backup(bundle,target):
    bundle=Path(bundle).absolute();manifest=load_manifest(bundle);bundle=bundle.resolve();target=Path(target).resolve()
    source=Path(manifest.get('source_root','/')).resolve()
    if target==bundle or target.is_relative_to(bundle) or bundle.is_relative_to(target) or target==source or target.is_relative_to(source):raise BackupError('恢复必须使用备份和原数据目录之外的新目录')
    if target.exists():raise BackupError('恢复目标已存在，未覆盖任何内容')
    verify_backup(bundle)
    require_space(target.parent,sum(entry['size'] for entry in manifest['files']))
    owned=False
    try:
        target.parent.mkdir(parents=True,exist_ok=True);target.mkdir(mode=0o700)
        with DataLease(target,exclusive=True):
            owned=True
            (target/'.restore-in-progress').touch(mode=0o600)
            for rel in manifest['directories']:(target/rel).mkdir(parents=True,exist_ok=True,mode=0o700)
            for entry in manifest['files']:
                info=copy_file(bundle/'data'/entry['path'],target/entry['path'])
                if info!={'size':entry['size'],'sha256':entry['sha256']}:raise BackupError('恢复复制校验失败')
            logical=verify_data(target,manifest)
            result={'status':'restore_verified','file_count':len(manifest['files']),'logical_inventory':logical,'workers_started':False,'source_overwritten':False}
            write_json(target/'.restore-report.json',result)
            for rel in sorted(manifest['directories'],key=lambda value:value.count('/'),reverse=True):sync_dir(target/rel)
            (target/'.restore-in-progress').unlink();sync_dir(target)
        return result
    except BaseException:
        # Incomplete targets remain marked and cannot be opened by the application.
        raise


def drill_backup(bundle):
    with tempfile.TemporaryDirectory(prefix='tingji-restore-drill-') as folder:
        result=restore_backup(bundle,Path(folder)/'restored')
    return dict(result,status='drill_passed',temporary_restore_removed=True)
