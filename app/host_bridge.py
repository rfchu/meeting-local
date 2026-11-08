"""E1 read-only affinity contract for the macOS host. No capture ownership transfer."""
import hashlib,hmac,json,os,re,sqlite3,stat,time

PRODUCT='cn.seeknow.tingji'
PROTOCOL_VERSION=1

def path_fingerprint(path):
    return hashlib.sha256(str(path.resolve()).encode('utf-8')).hexdigest()

def identity(paths):
    return {'product':PRODUCT,'protocol_version':PROTOCOL_VERSION,
            'project_fingerprint':path_fingerprint(paths.repository),'data_fingerprint':path_fingerprint(paths.data),
            'pid':os.getpid(),'instance_id':os.getenv('TINGJI_INSTANCE_ID'),
            'service_mode':os.getenv('TINGJI_SERVICE_MODE','foreground'),
            'workspace_path':'/','capture_ownership':{'browser':'recording_page','native':'api_service','host':'none'},
            'restart_capture_reattach':False}


def workspace_binding(paths):
    value='v1:'+path_fingerprint(paths.repository)+':'+path_fingerprint(paths.data)
    return hashlib.sha256(value.encode('utf-8')).hexdigest()

def read_workspace_key(paths):
    path = paths.data.parent / 'workspace.key'
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as file:
        info = os.fstat(file.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size != 32:
            raise ValueError('工作空间授权文件权限或格式无效')
        key = file.read(33)
    if len(key) != 32:
        raise ValueError('工作空间授权文件格式无效')
    return key


def workspace_mac(key, message):
    return hmac.digest(key, message.encode('ascii'), 'sha256').hex()


def workspace_proof(key, purpose, challenge):
    if key and re.fullmatch(r'[0-9a-f]{32}', challenge):
        return {'proof': workspace_mac(key, purpose + ':' + challenge)}
    return {}


def maintenance_report(data, now=None):
    """Classify real writes separately from sessions that can be recovered later."""
    from contextlib import closing
    from .capture_ownership import inspect

    cutoff = (time.time() if now is None else now) - 120
    recordings = data / 'recordings'
    blockers = []
    lease = inspect(recordings)['lease']
    if lease != 'free':
        blockers.append('capture_active' if lease == 'held' else 'capture_unverified')
    published = set()
    database = data / 'meetings.sqlite3'
    if database.exists():
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
            if db.execute("SELECT 1 FROM jobs WHERE state IN ('running','queued') LIMIT 1").fetchone():
                blockers.append('jobs_active')
            published = {row[0] for row in db.execute('SELECT id FROM meetings')}
    recoverable = 0
    for path in recordings.glob('*/session.json'):
        if path.parent.name in published:
            continue
        try:
            session = json.loads(path.read_text())
            if session['state'] not in ('starting', 'recording', 'paused'):
                continue
            updated = session.get('updated')
            if type(updated) not in (int, float) or not 0 < updated < float('inf'):
                blockers.append('capture_unverified')
                continue
            if updated >= cutoff:
                blockers.append('capture_active')
                continue
            recent_audio = any(
                f.stat().st_mtime >= cutoff for pattern in ('*.part', 'live-input/*.wav', '*.pcm')
                for f in path.parent.glob(pattern)
            )
            if recent_audio:
                blockers.append('capture_active')
            else:
                recoverable += 1
        except FileNotFoundError:
            continue
        except (OSError, ValueError, KeyError, TypeError):
            blockers.append('capture_unverified')
    return {'blockers': list(dict.fromkeys(blockers)), 'recoverable': recoverable}


def maintenance_busy(data):
    return bool(maintenance_report(data)['blockers'])
